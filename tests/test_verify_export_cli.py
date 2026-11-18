"""Offline handoff verification through the Janus CLI."""

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from janus import cli, core
from janus.export import canonical_json

GOLDEN = Path(__file__).parent / "fixtures" / "export-v1.golden.json"
GATE = "g00000000001"
OTHER_GATE = "g00000000002"
PIN = "e5bd4cad7ee4938bd0e89df23163ec9e16da479ffe046461d6efdd394f091caf"


def _rehash(envelope, *, records=True):
    document = envelope["document"]
    if records:
        for entry in document["records"]:
            entry["integrity"]["record_sha256"] = hashlib.sha256(
                canonical_json(entry["record"])
            ).hexdigest()
    envelope["integrity"]["document_sha256"] = hashlib.sha256(canonical_json(document)).hexdigest()
    return json.dumps(envelope).encode()


@pytest.fixture
def verify(capsys, monkeypatch, tmp_path):
    ledger = tmp_path / "unused" / "janus.db"
    monkeypatch.setenv("JANUS_DB", str(ledger))

    def forbidden(*args, **kwargs):
        pytest.fail("offline verification attempted to access the ledger, binding, or a command")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(core, "resolve_binding", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)

    def run(*args, raw=None):
        if raw is not None:
            monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
            args = ("-", *args)
        rc = cli.main(["verify-export", *args])
        reply = capsys.readouterr()
        assert not ledger.parent.exists()
        if rc == 0:
            assert not reply.err
        else:
            assert rc == 2 and not reply.out and reply.err
        return rc, reply.out if rc == 0 else reply.err

    return run


def test_golden_point_receipt_and_independent_pins(verify):
    expected = {
        "gate_id": GATE,
        "record_count": 1,
        "document_sha256": PIN,
        "decision": {
            "question": "Ship the example candidate?",
            "state": "approved",
            "consumer": "example: independently re-verify before acting",
            "selected_option": {"id": "ship", "label": "Ship"},
            "binding_evidence": "recorded",
        },
    }
    for flags in ([], ["--expect-gate", GATE], ["--expect-document-sha256", PIN]):
        rc, output = verify(str(GOLDEN), "--json", *flags)
        assert rc == 0 and json.loads(output) == expected
        assert len(output.splitlines()) == 1

    # Different transport bytes still describe the identical canonical document.
    reformatted = json.dumps(json.loads(GOLDEN.read_bytes()), indent=4, sort_keys=True).encode()
    rc, output = verify("--json", "--expect-gate", GATE, "--expect-document-sha256", PIN,
                        raw=reformatted)
    assert rc == 0 and json.loads(output) == expected


def test_receipt_uses_actual_selection_and_quotes_untrusted_text(verify):
    artifact = json.loads(GOLDEN.read_bytes())
    record = artifact["document"]["records"][0]["record"]
    record["question"] = "Review\x1b[2Jthis?"
    record["options"].append(dict(record["options"][0], option_id="hold", label="Keep draft",
                                  position=1, recommended=False))
    record["terminal_event"]["option_id"] = "hold"
    raw = _rehash(artifact)
    rc, output = verify("--json", raw=raw)
    assert rc == 0 and "\x1b" not in output
    decision = json.loads(output)["decision"]
    assert decision["selected_option"] == {"id": "hold", "label": "Keep draft"}
    assert decision["question"] == record["question"]
    rc, output = verify(raw=raw)
    assert rc == 0 and "\x1b" not in output and "/example/candidate" not in output
    for field in (GATE, artifact["integrity"]["document_sha256"], "Review", "this?", "approved",
                  record["consumer"], "hold", "Keep draft", "recorded"):
        assert field in output


@pytest.mark.parametrize("count", [0, 1])
def test_complete_ledger_has_no_single_gate_decision(verify, count):
    artifact = json.loads(GOLDEN.read_bytes())
    document = artifact["document"]
    document["selection"]["gate_id"] = None
    document["records"] = document["records"][:count]
    raw = _rehash(artifact)
    digest = artifact["integrity"]["document_sha256"]
    rc, output = verify("--json", "--expect-document-sha256", digest, raw=raw)
    assert rc == 0 and json.loads(output) == {
        "gate_id": None, "record_count": count, "document_sha256": digest, "decision": None,
    }
    rc, error = verify("--expect-gate", GATE, raw=raw)
    assert rc == 2 and ("gate" in error.lower() or "selection" in error.lower())


@pytest.mark.parametrize("state,status", [
    ("open", None), ("approved", "invalid_missing"), ("superseded", "not_applicable"),
])
def test_no_selection_or_binding_evidence_is_inferred(verify, state, status):
    artifact = json.loads(GOLDEN.read_bytes())
    record = artifact["document"]["records"][0]["record"]
    record["state"] = state
    if state == "open":
        record["terminal_event"] = None
    else:
        event = record["terminal_event"]
        event["binding_evidence"] = {"status": status, "sha256": None}
        if state == "superseded":
            event.update(state=state, type="non_ruling_closure", option_id=None)
    rc, output = verify("--json", raw=_rehash(artifact))
    assert rc == 0
    decision = json.loads(output)["decision"]
    assert decision["binding_evidence"] == status and decision["state"] == state
    if state != "approved":
        assert decision["selected_option"] is None


def test_wrong_gate_same_gate_different_digest_and_tampering(verify):
    flags = ["--expect-gate", GATE, "--expect-document-sha256", PIN, "--json"]
    artifact = json.loads(GOLDEN.read_bytes())
    record = artifact["document"]["records"][0]["record"]
    record["consumer"] = "new recipient"
    rc, error = verify(*flags, raw=_rehash(artifact))
    assert rc == 2 and ("digest" in error.lower() or "sha256" in error.lower())

    record["id"] = artifact["document"]["selection"]["gate_id"] = OTHER_GATE
    rc, wrong_gate = verify(*flags, raw=_rehash(artifact))
    assert rc == 2 and ("gate" in wrong_gate.lower() or "selection" in wrong_gate.lower())
    assert wrong_gate != error

    record["question"] = "changed after export"
    rc, error = verify(*flags, raw=json.dumps(artifact).encode())
    assert rc == 2 and "document digest does not match" in error
    record["id"] = "corrupt\x1b[2J"
    rc, error = verify(*flags, raw=_rehash(artifact, records=False))
    assert rc == 2 and "record" in error and "digest mismatch" in error and "\x1b" not in error


def test_rehashed_bad_schema_fails_cleanly(verify):
    artifact = json.loads(GOLDEN.read_bytes())
    record = artifact["document"]["records"][0]["record"]
    record["state"] = []
    rc, error = verify("--json", raw=_rehash(artifact))
    assert rc == 2 and "state" in error
    record["state"] = "approved"
    artifact["document"]["vocabulary"]["gate_states"][0]["terminal"] = 0
    rc, error = verify("--json", raw=_rehash(artifact))
    assert rc == 2 and "vocabulary" in error


@pytest.mark.parametrize("raw", [b"\xff", b"{", b'{"schema":1,"schema":2}'])
def test_malformed_input_is_an_error(verify, raw):
    rc, _ = verify("--json", raw=raw)
    assert rc == 2


def test_missing_file_and_malformed_expectations_fail_cleanly(verify, tmp_path):
    rc, _ = verify(str(tmp_path / "missing.json"), "--json")
    assert rc == 2
    for flag, value in (("--expect-gate", "no-gate"), ("--expect-document-sha256", PIN.upper())):
        rc, _ = verify(str(GOLDEN), flag, value, "--json")
        assert rc == 2


def test_input_is_required_even_when_stdin_contains_an_export(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(GOLDEN.read_bytes())))
    with pytest.raises(SystemExit) as raised:
        cli.main(["verify-export", "--json"])
    reply = capsys.readouterr()
    assert raised.value.code == 2 and not reply.out and "required" in reply.err
