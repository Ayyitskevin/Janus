"""Invariant tests for the Janus-owned, stable read boundary."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from janus import cli, core
from janus import export as stable_export
from janus.core import JanusError


def _gate(conn: sqlite3.Connection, **overrides) -> str:
    values = {
        "question": "Ship this exact candidate?",
        "kind": "authority",
        "decay": "the candidate diverges from main",
        "consumer": "beacon: display the evidence; execution re-verifies independently",
        "actor": "tester",
        "decay_check": "false",
    }
    values.update(overrides)
    return core.raise_gate(conn, **values)


@pytest.fixture()
def populated_ledger(tmp_path: Path) -> tuple[Path, str, str]:
    db = tmp_path / "janus.db"
    conn = core.connect(db)
    artifact = tmp_path / "candidate.txt"
    artifact.write_text("candidate-v1")
    ruled = _gate(
        conn,
        binding=core.resolve_binding("file", str(artifact)),
        options=[
            {"id": "ship", "label": "Ship it", "recommended": True},
            {"id": "hold", "label": "Hold", "recommended": False},
        ],
    )
    core.revise_check(
        conn,
        ruled,
        "decay",
        "true",
        "tester",
        "the old check measured the opposite condition",
    )
    for _ in range(7):
        core.observe(conn, ruled, "decay", "tester")
    core.close_gate(
        conn,
        ruled,
        state="approved",
        reason="the exact candidate passed review",
        actor="kevin",
        option_id="ship",
    )
    superseded = _gate(conn, question="A question the world moved past")
    core.close_gate(
        conn,
        superseded,
        state="superseded",
        reason="a newer candidate replaced it",
        actor="tester",
    )
    conn.close()
    return db, ruled, superseded


def _envelope(raw: bytes) -> dict:
    return json.loads(raw)


def _published_schema() -> dict:
    path = Path(__file__).parents[1] / "docs" / "spec" / "export-v1.schema.json"
    return json.loads(path.read_text())


def _record(document: dict, gate_id: str) -> dict:
    return next(
        entry["record"] for entry in document["records"] if entry["record"]["id"] == gate_id
    )


def _resign(envelope: dict) -> None:
    for entry in envelope["document"]["records"]:
        entry["integrity"]["record_sha256"] = hashlib.sha256(
            stable_export.canonical_json(entry["record"])
        ).hexdigest()
    envelope["integrity"]["document_sha256"] = hashlib.sha256(
        stable_export.canonical_json(envelope["document"])
    ).hexdigest()


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _all_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _all_keys(child)}
    return set()


def test_export_is_deterministic_complete_and_self_verifying(populated_ledger):
    db, ruled, superseded = populated_ledger

    first = stable_export.export_gates(db)
    second = stable_export.export_gates(db)
    assert first == second, "an unchanged ledger must have one content identity"

    document = stable_export.verify_export(first)
    assert document["semantics"] == {
        "rulings": "evidence_not_authority",
        "binding_verification": "not_performed",
        "stored_checks": "not_executed",
        "missing_binding_evidence": "invalid_record",
    }
    ruled_record = _record(document, ruled)
    assert ruled_record["terminal_event"]["type"] == "human_ruling"
    assert ruled_record["terminal_event"]["binding_evidence"]["status"] == "recorded"
    assert ruled_record["terminal_event"]["binding_evidence"]["sha256"]
    assert ruled_record["checks"]["decay"]["original"] == "false"
    assert ruled_record["checks"]["decay"]["effective"] == "true"
    assert len(ruled_record["observations"]) == 7
    assert _record(document, superseded)["terminal_event"]["type"] == "non_ruling_closure"

    forbidden = {"authorized", "can_act", "binding_matches", "decay_verdict", "overdue"}
    assert not (_all_keys(document) & forbidden)


def test_export_declares_state_semantics_instead_of_making_consumers_copy_them(
    populated_ledger,
):
    db, _, _ = populated_ledger
    states = stable_export.verify_export(stable_export.export_gates(db))["vocabulary"][
        "gate_states"
    ]
    assert {entry["word"] for entry in states} == {
        "open",
        "approved",
        "refused",
        "expired",
        "withdrawn",
        "superseded",
    }
    assert {entry["word"] for entry in states if entry["human_ruled"]} == {
        "approved",
        "refused",
    }
    assert {entry["word"] for entry in states if not entry["terminal"]} == {"open"}


def test_point_and_complete_exports_carry_the_identical_gate_record(populated_ledger):
    db, ruled, _ = populated_ledger
    complete = stable_export.verify_export(stable_export.export_gates(db))
    point = stable_export.verify_export(stable_export.export_gates(db, ruled))
    complete_entry = next(e for e in complete["records"] if e["record"]["id"] == ruled)
    assert point["records"] == [complete_entry]
    assert point["selection"] == {"gate_id": ruled}


def test_missing_point_read_is_distinct_from_an_empty_ledger(populated_ledger):
    db, _, _ = populated_ledger
    with pytest.raises(JanusError, match="no such gate"):
        stable_export.export_gates(db, "g00000000000")


def test_document_tampering_fails_before_it_can_be_consumed(populated_ledger):
    db, ruled, _ = populated_ledger
    envelope = _envelope(stable_export.export_gates(db))
    _record(envelope["document"], ruled)["question"] = "tampered"
    with pytest.raises(JanusError, match="document digest does not match"):
        stable_export.verify_export(json.dumps(envelope))


def test_record_digest_is_independently_checked(populated_ledger):
    db, ruled, _ = populated_ledger
    envelope = _envelope(stable_export.export_gates(db))
    _record(envelope["document"], ruled)["question"] = "tampered"
    envelope["integrity"]["document_sha256"] = hashlib.sha256(
        stable_export.canonical_json(envelope["document"])
    ).hexdigest()
    with pytest.raises(JanusError, match=f"record {ruled} digest mismatch"):
        stable_export.verify_export(json.dumps(envelope))


def test_a_recomputed_digest_cannot_make_unknown_semantics_compatible(populated_ledger):
    db, ruled, _ = populated_ledger
    envelope = _envelope(stable_export.export_gates(db))
    _record(envelope["document"], ruled)["binding"]["kind"] = "vibes"
    _resign(envelope)
    with pytest.raises(JanusError, match="binding.kind is incompatible"):
        stable_export.verify_export(json.dumps(envelope))

    envelope = _envelope(stable_export.export_gates(db))
    envelope["document"]["semantics"]["rulings"] = "permission_to_act"
    _resign(envelope)
    with pytest.raises(JanusError, match="authority semantics"):
        stable_export.verify_export(json.dumps(envelope))


@pytest.mark.parametrize("mutation, message", [
    (lambda envelope: envelope.__setitem__("can_act", True), "envelope keys"),
    (
        lambda envelope: envelope["document"]["source"].__setitem__("migrations", []),
        "non-empty array",
    ),
    (
        lambda envelope: envelope["document"]["records"][0]["record"].pop("question"),
        "record .* keys",
    ),
    (
        lambda envelope: envelope["document"]["records"][0]["record"].__setitem__(
            "authorized", True
        ),
        "extra: authorized",
    ),
])
def test_v1_is_closed_shape_even_when_an_attacker_recomputes_digests(
    populated_ledger, mutation, message
):
    db, _, _ = populated_ledger
    envelope = _envelope(stable_export.export_gates(db))
    mutation(envelope)
    _resign(envelope)
    with pytest.raises(JanusError, match=message):
        stable_export.verify_export(json.dumps(envelope))


def test_a_bound_ruling_with_no_digest_is_explicitly_invalid(tmp_path: Path):
    db = tmp_path / "invalid.db"
    conn = core.connect(db)
    artifact = tmp_path / "gone.txt"
    artifact.write_text("reviewed once")
    gate_id = _gate(conn, binding=core.resolve_binding("file", str(artifact)))
    conn.execute("DROP TRIGGER IF EXISTS ruling_bound_gate_requires_digest")
    conn.execute(
        "INSERT INTO rulings (gate_id, state, ruled_at, ruled_by, reason, bound_sha256) "
        "VALUES (?, 'approved', ?, 'legacy', 'old invalid row', NULL)",
        (gate_id, core.now()),
    )
    conn.commit()
    conn.close()

    document = stable_export.verify_export(stable_export.export_gates(db, gate_id))
    evidence = document["records"][0]["record"]["terminal_event"]["binding_evidence"]
    assert evidence == {"status": "invalid_missing", "sha256": None}


def test_a_non_ruling_closure_cannot_smuggle_an_option_into_export(tmp_path: Path):
    db = tmp_path / "invalid-option.db"
    conn = core.connect(db)
    gate_id = _gate(
        conn,
        options=[{"id": "a", "label": "A", "recommended": True}],
    )
    core.close_gate(
        conn,
        gate_id,
        state="superseded",
        reason="world moved",
        actor="tester",
        option_id="a",
    )
    conn.close()
    with pytest.raises(JanusError, match="non-ruling closure .* names option"):
        stable_export.export_gates(db)


def test_export_refuses_a_stored_ruling_that_names_an_unknown_option(tmp_path: Path):
    db = tmp_path / "invalid-ruling-option.db"
    conn = core.connect(db)
    gate_id = _gate(
        conn,
        options=[{"id": "offered", "label": "Offered", "recommended": True}],
    )
    conn.execute("DROP TRIGGER ruling_option_must_exist")
    conn.execute(
        "INSERT INTO rulings "
        "(gate_id, state, ruled_at, ruled_by, reason, option_id, bound_sha256) "
        "VALUES (?, 'approved', ?, 'legacy', 'invalid old row', 'ghost', NULL)",
        (gate_id, core.now()),
    )
    conn.commit()
    conn.close()

    with pytest.raises(JanusError, match="names an option the gate does not offer"):
        stable_export.export_gates(db)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda record: record["terminal_event"].__setitem__(
                "option_id", "not-offered"
            ),
            "names an option the gate does not offer",
        ),
        (
            lambda record: record["terminal_event"].__setitem__("option_id", None),
            "must name one offered option",
        ),
        (
            lambda record: record["options"][1].__setitem__("option_id", "ship"),
            "duplicate option id",
        ),
        (
            lambda record: record["options"][1].__setitem__("position", 7),
            "contiguous position order",
        ),
    ],
)
def test_recomputed_exports_cannot_break_option_relationships(
    populated_ledger, mutation, message
):
    db, ruled, _ = populated_ledger
    envelope = _envelope(stable_export.export_gates(db, ruled))
    mutation(envelope["document"]["records"][0]["record"])
    _resign(envelope)

    with pytest.raises(JanusError, match=message):
        stable_export.verify_export(json.dumps(envelope))


def test_duplicate_json_keys_and_floats_are_not_canonical():
    with pytest.raises(JanusError, match="duplicate JSON key"):
        stable_export.verify_export('{"schema":"one","schema":"two"}')
    with pytest.raises(JanusError, match="refuses floating-point"):
        stable_export.canonical_json({"uncertainty": 0.5})
    with pytest.raises(JanusError, match="invalid Unicode"):
        stable_export.canonical_json({"value": "\ud800"})


def test_export_opens_the_ledger_read_only(monkeypatch, populated_ledger):
    db, _, _ = populated_ledger
    real_connect = stable_export.sqlite3.connect
    calls = []

    def recording_connect(database, *args, **kwargs):
        calls.append((database, kwargs.copy()))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(stable_export.sqlite3, "connect", recording_connect)
    stable_export.export_gates(db)
    assert len(calls) == 1
    assert calls[0][0].endswith("?mode=ro")
    assert calls[0][1].get("uri") is True


def test_export_neither_creates_the_main_database_nor_migrates_a_ledger(tmp_path: Path):
    missing = tmp_path / "missing.db"
    with pytest.raises(JanusError, match="no Janus ledger"):
        stable_export.export_gates(missing)
    assert not missing.exists()

    stale = tmp_path / "stale.db"
    conn = core.connect(stale)
    conn.execute("DELETE FROM schema_migrations WHERE version = '0002_check_revisions'")
    conn.commit()
    conn.close()
    with pytest.raises(JanusError, match="migrations do not exactly match"):
        stable_export.export_gates(stale)
    with sqlite3.connect(stale) as check:
        assert check.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version = '0002_check_revisions'"
        ).fetchone()[0] == 0


def test_export_may_materialize_only_private_sqlite_coordination_sidecars(tmp_path: Path):
    db = tmp_path / "janus.db"
    conn = core.connect(db)
    conn.close()
    before_names = {path.name for path in tmp_path.iterdir()}
    before_database = db.read_bytes()

    stable_export.export_gates(db)

    after_names = {path.name for path in tmp_path.iterdir()}
    created = after_names - before_names
    allowed = {f"{db.name}-wal", f"{db.name}-shm"}
    assert created <= allowed
    assert db.read_bytes() == before_database
    for name in created:
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o600


def test_export_postchecks_sqlite_coordination_sidecars(tmp_path: Path, monkeypatch):
    db = tmp_path / "janus.db"
    conn = core.connect(db)
    conn.close()
    real_blocker = core.storage_open_blocker
    blocker_calls = 0

    def broaden_sidecar_before_postcheck(path):
        nonlocal blocker_calls
        blocker_calls += 1
        if blocker_calls == 2:
            Path(f"{db}-wal").chmod(0o644)
        return real_blocker(path)

    monkeypatch.setattr(core, "storage_open_blocker", broaden_sidecar_before_postcheck)
    with pytest.raises(
        JanusError,
        match="SQLite coordination storage became unsafe.*WAL mode 0644",
    ):
        stable_export.export_gates(db)
    assert blocker_calls == 2


def test_export_uses_the_same_storage_identity_boundary(tmp_path: Path):
    directory = tmp_path / "private"
    db = directory / "janus.db"
    conn = core.connect(db)
    conn.close()
    original = db.read_bytes()

    directory.chmod(0o777)
    with pytest.raises(JanusError, match=r"directory mode 0777 \(expected 0700\)"):
        stable_export.export_gates(db)

    directory.chmod(0o1777)
    with pytest.raises(JanusError, match=r"directory mode 1777 \(expected 0700\)"):
        stable_export.export_gates(db)
    directory.chmod(0o700)

    db_link = tmp_path / "database-link.db"
    db_link.symlink_to(db)
    with pytest.raises(JanusError, match="database is a symbolic link"):
        stable_export.export_gates(db_link)

    directory_link = tmp_path / "directory-link"
    directory_link.symlink_to(directory, target_is_directory=True)
    with pytest.raises(JanusError, match="directory path contains a symbolic link"):
        stable_export.export_gates(directory_link / db.name)

    second_name = directory / "second-name.db"
    os.link(db, second_name)
    with pytest.raises(JanusError, match="database has 2 hard links"):
        stable_export.export_gates(db)
    second_name.unlink()

    journal = Path(f"{db}-journal")
    journal.mkdir()
    with pytest.raises(JanusError, match="rollback journal is not a regular file"):
        stable_export.export_gates(db)

    assert db.read_bytes() == original


def test_cli_export_bypasses_the_normal_writable_connector(
    monkeypatch, capsysbinary, populated_ledger
):
    db, ruled, _ = populated_ledger

    def writable_connector_was_used(*_args, **_kwargs):
        raise AssertionError("export reached core.connect, which may create and migrate")

    monkeypatch.setattr(core, "connect", writable_connector_was_used)
    assert cli.main(["--db", str(db), "export", ruled]) == 0
    output = capsysbinary.readouterr().out
    document = stable_export.verify_export(output)
    assert [entry["record"]["id"] for entry in document["records"]] == [ruled]


def test_published_canonicalization_vectors_match_the_reference_codec():
    fixture = Path(__file__).parent / "fixtures" / "canonical-json-v1.json"
    vectors = json.loads(fixture.read_text())
    for vector in vectors["vectors"]:
        encoded = stable_export.canonical_json(vector["value"])
        assert encoded.decode("utf-8") == vector["canonical"]
        assert hashlib.sha256(encoded).hexdigest() == vector["sha256"]


def test_published_full_envelope_is_a_verified_golden_fixture():
    fixtures = Path(__file__).parent / "fixtures"
    raw = (fixtures / "export-v1.golden.json").read_bytes()
    document = stable_export.verify_export(raw)
    assert document["selection"] == {"gate_id": "g00000000001"}
    assert document["records"][0]["record"]["terminal_event"]["binding_evidence"] == {
        "status": "recorded",
        "sha256": "2" * 64,
    }

    schema = _published_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(raw))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema", "document", "integrity"}


def test_published_schema_and_reference_verifier_reject_negative_option_positions():
    raw = (Path(__file__).parent / "fixtures" / "export-v1.golden.json").read_bytes()
    envelope = _envelope(raw)
    envelope["document"]["records"][0]["record"]["options"][0]["position"] = -1
    _resign(envelope)

    with pytest.raises(ValidationError):
        Draft202012Validator(_published_schema()).validate(envelope)
    with pytest.raises(JanusError, match="position must be at least 0"):
        stable_export.verify_export(json.dumps(envelope))


def test_published_schema_and_reference_verifier_reject_non_ascii_timestamps():
    raw = (Path(__file__).parent / "fixtures" / "export-v1.golden.json").read_bytes()
    envelope = _envelope(raw)
    envelope["document"]["records"][0]["record"]["raised_at"] = (
        "٢٠٢٦-٠٨-٢٩T١٢:٠٠:٠٠Z"
    )
    _resign(envelope)

    with pytest.raises(ValidationError):
        Draft202012Validator(_published_schema()).validate(envelope)
    with pytest.raises(JanusError, match="raised_at must be a normalized UTC instant"):
        stable_export.verify_export(json.dumps(envelope))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda record: record.__setitem__("raised_at", record["raised_at"] + "\n"),
            "raised_at must be a normalized UTC instant",
        ),
        (
            lambda record: record.__setitem__("id", record["id"] + "\n"),
            "id is incompatible",
        ),
        (
            lambda record: record["binding"].__setitem__(
                "raised_sha256", record["binding"]["raised_sha256"] + "\n"
            ),
            "raised_sha256 must be lowercase SHA-256",
        ),
    ],
)
def test_published_schema_and_reference_verifier_reject_embedded_final_line_feeds(
    mutation, message
):
    raw = (Path(__file__).parent / "fixtures" / "export-v1.golden.json").read_bytes()
    envelope = _envelope(raw)
    mutation(envelope["document"]["records"][0]["record"])
    _resign(envelope)

    with pytest.raises(ValidationError):
        Draft202012Validator(_published_schema()).validate(envelope)
    with pytest.raises(JanusError, match=message):
        stable_export.verify_export(json.dumps(envelope))
