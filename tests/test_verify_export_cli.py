"""Offline handoff verification through the Janus CLI."""

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from janus import cli, core
from janus.export import canonical_json, verify_export, verify_export_receipt

GOLDEN = Path(__file__).parent / "fixtures" / "export-v1.golden.json"
GATE = "g00000000001"
OTHER_GATE = "g00000000002"
PIN = "e5bd4cad7ee4938bd0e89df23163ec9e16da479ffe046461d6efdd394f091caf"
BOUND_FILE = "--expect-bound-file-sha256"
RAISED_SHA = "1" * 64
RULED_SHA = "2" * 64


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
        assert output == json.dumps(expected, separators=(",", ":")) + "\n"

    rc, output = verify(str(GOLDEN))
    assert rc == 0 and output == (
        f"Verified selection: {GATE}\nRecords: 1\nDocument SHA-256: {PIN}\n"
        "Question: 'Ship the example candidate?'\nState: approved\n"
        "Consumer: 'example: independently re-verify before acting'\n"
        "Selected option: 'ship' 'Ship'\nBinding evidence: recorded\n"
    )

    # Different transport bytes still describe the identical canonical document.
    reformatted = json.dumps(json.loads(GOLDEN.read_bytes()), indent=4, sort_keys=True).encode()
    rc, output = verify("--json", "--expect-gate", GATE, "--expect-document-sha256", PIN,
                        raw=reformatted)
    assert rc == 0 and json.loads(output) == expected


def test_bound_file_pin_matches_only_ruling_time_file_evidence(verify):
    raw = GOLDEN.read_bytes()
    expected = {**verify_export_receipt(raw), "matched_bound_file_sha256": RULED_SHA}
    for pins in ((), ("--expect-gate", GATE), ("--expect-document-sha256", PIN),
                 ("--expect-gate", GATE, "--expect-document-sha256", PIN)):
        rc, output = verify(str(GOLDEN), "--json", BOUND_FILE, RULED_SHA, *pins)
        assert rc == 0 and json.loads(output) == expected
    rc, output = verify(BOUND_FILE, RULED_SHA, raw=raw)
    assert rc == 0 and any(RULED_SHA in line and "rul" in line.lower() and "file" in line.lower()
                           for line in output.splitlines())
    for wrong in (RAISED_SHA, PIN):
        with pytest.raises(core.JanusError, match=r"(?i)match|differ"):
            verify_export_receipt(raw, expect_bound_file_sha256=wrong)
    rc, error = verify(str(GOLDEN), BOUND_FILE, RAISED_SHA)
    assert rc == 2 and "ruling-time file digest does not match" in error


def test_matching_file_preserves_refusal(verify):
    artifact = json.loads(GOLDEN.read_bytes())
    record = artifact["document"]["records"][0]["record"]
    event = record["terminal_event"]
    record["state"] = event["state"] = "refused"
    event["option_id"] = None
    raw = _rehash(artifact)
    expected = verify_export_receipt(raw)
    assert expected["decision"]["state"] == "refused"
    assert expected["decision"]["selected_option"] is None
    expected["matched_bound_file_sha256"] = RULED_SHA
    assert verify_export_receipt(raw, expect_bound_file_sha256=RULED_SHA) == expected
    rc, output = verify(BOUND_FILE, RULED_SHA, "--json", raw=raw)
    assert rc == 0 and json.loads(output) == expected


def _assert_no_file_comparison(verify, raw, reason="no recorded ruling-time file digest"):
    verify_export(raw)  # This is a valid v1 export, including its record integrity.
    rc, error = verify(BOUND_FILE, RULED_SHA, raw=raw)
    assert rc == 2 and reason in error


@pytest.mark.parametrize("kind", ["git", "text", None])
def test_bound_file_pin_requires_file_binding(verify, kind):
    artifact = json.loads(GOLDEN.read_bytes())
    record = artifact["document"]["records"][0]["record"]
    if kind is None:
        record["binding"] = None
        record["terminal_event"]["binding_evidence"] = {"status": "not_applicable", "sha256": None}
    else:
        record["binding"]["kind"] = kind  # Byte-for-byte equal recorded digest.
    _assert_no_file_comparison(verify, _rehash(artifact))


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
    _assert_no_file_comparison(verify, raw, reason="requires one gate")


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
    raw = _rehash(artifact)
    rc, output = verify("--json", raw=raw)
    assert rc == 0
    decision = json.loads(output)["decision"]
    assert decision["binding_evidence"] == status and decision["state"] == state
    if state != "approved":
        assert decision["selected_option"] is None
    _assert_no_file_comparison(verify, raw)


def test_wrong_gate_same_gate_different_digest_and_tampering(verify):
    flags = ["--expect-gate", GATE, "--expect-document-sha256", PIN, BOUND_FILE, "bad", "--json"]
    artifact = json.loads(GOLDEN.read_bytes())
    record = artifact["document"]["records"][0]["record"]
    record["consumer"] = "new recipient"
    rc, error = verify(*flags, raw=_rehash(artifact))
    assert rc == 2 and "document digest does not match expected" in error

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


def test_bound_file_pin_requires_exact_lowercase_ascii_hex_once(verify):
    for value in (RULED_SHA[:-1], RULED_SHA + "0", "A" * 64, "g" * 64, "２" * 64,
                  RULED_SHA + "\n", "\x1b[2J"):
        rc, error = verify(str(GOLDEN), BOUND_FILE, value, "--json")
        assert rc == 2 and "\x1b" not in error and len(error.splitlines()) == 1
        assert "lowercase" in error.lower()
    rc, error = verify(str(GOLDEN), BOUND_FILE, RULED_SHA, BOUND_FILE, RULED_SHA)
    assert rc == 2 and BOUND_FILE in error


def test_input_is_required_even_when_stdin_contains_an_export(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(GOLDEN.read_bytes())))
    with pytest.raises(SystemExit) as raised:
        cli.main(["verify-export", "--json"])
    reply = capsys.readouterr()
    assert raised.value.code == 2 and not reply.out and "required" in reply.err


def _assert_stream_refusal(args, *, stdin=None, stdout=subprocess.PIPE, shell_setup=None,
                           python_setup=None):
    # Use the sources beside this test, including when run against an archived revision.
    command = [sys.executable, "-E"]
    if python_setup:
        script = "from janus.cli import main\n" + python_setup + "\nraise SystemExit(main())"
        command += ["-c", script]
    else:
        command += ["-m", "janus.cli"]
    command += ["verify-export", *args, "--json"]
    if shell_setup:
        command = ["sh", "-c", shell_setup + '\nexec "$@"', "sh", *command]
    result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1] / "src",
                            stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, timeout=10)
    assert result.returncode == 2, result.stderr
    assert not result.stdout
    assert result.stderr.startswith(b"janus: ") and len(result.stderr.splitlines()) == 1
    assert result.stderr.removeprefix(b"janus: ").strip()


@pytest.mark.parametrize("after_start", [False, True], ids=["missing-stream", "closed-descriptor"])
def test_unavailable_stdin_is_an_input_error(after_start):
    if after_start:
        _assert_stream_refusal(["-"], python_setup="import os\nos.close(0)")
    else:
        _assert_stream_refusal(["-"], shell_setup="exec 0<&-")


@pytest.mark.parametrize("has_export", [False, True], ids=["empty", "valid-prefix"])
def test_would_block_stdin_is_an_input_error(has_export):
    reader, writer = os.pipe()
    with os.fdopen(reader, "rb") as pipe, os.fdopen(writer, "wb", buffering=0) as producer:
        # The writer can still append more bytes after a valid-looking prefix.
        if has_export:
            raw = json.dumps(json.loads(GOLDEN.read_bytes()), separators=(",", ":")).encode()
            assert producer.write(raw) == len(raw)
        os.set_blocking(pipe.fileno(), False)
        _assert_stream_refusal(["-"], stdin=pipe)


def test_nonblocking_stdin_with_eof_verifies_against_pins():
    reader, writer = os.pipe()
    with os.fdopen(reader, "rb") as pipe:
        os.set_blocking(pipe.fileno(), False)
        with os.fdopen(writer, "wb") as producer:
            compact = json.dumps(json.loads(GOLDEN.read_bytes()), separators=(",", ":"))
            producer.write(compact.encode())
        command = [sys.executable, "-E", "-m", "janus.cli", "verify-export", "-", "--json",
                   "--expect-gate", GATE, "--expect-document-sha256", PIN]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1] / "src",
                                stdin=pipe, capture_output=True, timeout=10)
    assert result.returncode == 0 and not result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["gate_id"] == GATE and receipt["document_sha256"] == PIN


def test_missing_stdout_is_an_output_error():
    _assert_stream_refusal([str(GOLDEN)], shell_setup="exec 1>&-")


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="requires the ENOSPC test device")
def test_unwritable_stdout_is_an_output_error():
    with open("/dev/full", "wb", buffering=0) as full:
        _assert_stream_refusal([str(GOLDEN)], stdout=full)
