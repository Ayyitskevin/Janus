"""The stable, read-only Janus interchange surface.

An export reports ledger evidence. It never verifies live bindings, executes
stored checks, or answers whether a consumer may act.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import core
from .core import JanusError

ENVELOPE_SCHEMA = "janus.export.v1"
DOCUMENT_SCHEMA = "janus.gates.v1"
RECORD_SCHEMA = "janus.gate.v1"
CANONICALIZER = "janus.canonical-json.v1"
BINDING_KINDS = ("file", "git", "text")
SEMANTICS = {
    "rulings": "evidence_not_authority",
    "binding_verification": "not_performed",
    "stored_checks": "not_executed",
    "missing_binding_evidence": "invalid_record",
}

STATE_VOCABULARY = (
    {"word": "open", "terminal": False, "human_ruled": False},
    {"word": "approved", "terminal": True, "human_ruled": True},
    {"word": "refused", "terminal": True, "human_ruled": True},
    {"word": "expired", "terminal": True, "human_ruled": False},
    {"word": "withdrawn", "terminal": True, "human_ruled": False},
    {"word": "superseded", "terminal": True, "human_ruled": False},
)
_STATE_BY_WORD = {entry["word"]: entry for entry in STATE_VOCABULARY}


def _validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise JanusError(f"{CANONICALIZER} refuses invalid Unicode at {path}") from exc
        return
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise JanusError(f"{CANONICALIZER} requires a signed 64-bit integer at {path}")
        return
    if isinstance(value, float):
        raise JanusError(f"{CANONICALIZER} refuses floating-point value at {path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise JanusError(f"{CANONICALIZER} requires string key at {path}")
            _validate_json_value(key, f"{path}.<key>")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise JanusError(f"{CANONICALIZER} cannot encode {type(value).__name__} at {path}")


def canonical_json(value: Any) -> bytes:
    """Return the exact bytes defined by ``janus.canonical-json.v1``."""
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _connect_read_only(db_path: Path | None) -> sqlite3.Connection:
    path = core.storage_path(db_path)
    try:
        path.lstat()
    except FileNotFoundError:
        raise JanusError(f"cannot export: no Janus ledger at {path}")
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        raise JanusError(f"cannot export: cannot inspect ledger {path}: {detail}") from exc
    blocker = core.storage_open_blocker(path)
    if blocker:
        raise JanusError(f"cannot export: refusing unsafe ledger path: {blocker}")
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")
        return conn
    except sqlite3.Error as exc:
        raise JanusError(f"cannot export {path} read-only: {exc}") from exc


def _expected_migrations() -> list[dict[str, str]]:
    return [
        {"version": path.stem, "checksum": core._checksum(path.read_text())}
        for path in sorted(core.MIGRATIONS_DIR.glob("*.sql"))
    ]


def _validated_migrations(conn: sqlite3.Connection) -> list[dict[str, str]]:
    expected = _expected_migrations()
    try:
        actual = [
            {"version": row["version"], "checksum": row["checksum"]}
            for row in conn.execute(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            )
        ]
    except sqlite3.Error as exc:
        raise JanusError(
            "cannot export: this ledger has no readable Janus migration history"
        ) from exc
    if actual != expected:
        raise JanusError(
            "cannot export: ledger migrations do not exactly match this Janus build; "
            "run a matching ordinary Janus command to migrate, or use the matching exporter"
        )
    return actual


def _group_rows(
    conn: sqlite3.Connection,
    table: str,
    order_by: str,
    gate_id: str | None,
) -> dict[str, list[dict[str, Any]]]:
    where = " WHERE gate_id = ?" if gate_id else ""
    params = (gate_id,) if gate_id else ()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in conn.execute(f"SELECT * FROM {table}{where} ORDER BY {order_by}", params):
        grouped[row["gate_id"]].append(dict(row))
    return grouped


def _effective_check(
    original: str | None,
    revisions: list[dict[str, Any]],
    kind: str,
) -> str | None:
    matching = [revision for revision in revisions if revision["kind"] == kind]
    return matching[-1]["command"] if matching else original


def _terminal_event(
    row: dict[str, Any] | None, *, gate_is_bound: bool
) -> dict[str, Any] | None:
    if row is None:
        return None
    state = row["state"]
    descriptor = _STATE_BY_WORD.get(state)
    if descriptor is None or state == "open":
        raise JanusError(f"cannot export: unknown terminal state {state!r}")
    human_ruled = bool(descriptor["human_ruled"])
    if not human_ruled and row["option_id"] is not None:
        raise JanusError(
            f"cannot export: non-ruling closure {state!r} names option {row['option_id']!r}"
        )
    bound_sha256 = row["bound_sha256"]
    if human_ruled and gate_is_bound:
        binding_status = "recorded" if bound_sha256 is not None else "invalid_missing"
    else:
        binding_status = "not_applicable"
        bound_sha256 = None
    return {
        "state": state,
        "type": "human_ruling" if human_ruled else "non_ruling_closure",
        "at": row["ruled_at"],
        "actor": row["ruled_by"],
        "reason": row["reason"],
        "option_id": row["option_id"],
        "binding_evidence": {"status": binding_status, "sha256": bound_sha256},
    }


def _record(
    gate: dict[str, Any],
    ruling: dict[str, Any] | None,
    options: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    revisions: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> dict[str, Any]:
    state = ruling["state"] if ruling else "open"
    if state not in _STATE_BY_WORD:
        raise JanusError(f"cannot export: unknown gate state {state!r}")
    if gate["kind"] not in core.KINDS:
        raise JanusError(f"cannot export: unknown gate kind {gate['kind']!r}")
    if gate["binding_kind"] not in (None, *BINDING_KINDS):
        raise JanusError(f"cannot export: unknown binding kind {gate['binding_kind']!r}")

    record = {
        "schema": RECORD_SCHEMA,
        "id": gate["id"],
        "state": state,
        "question": gate["question"],
        "kind": gate["kind"],
        "raised_at": gate["raised_at"],
        "raised_by": gate["raised_by"],
        "decay": gate["decay"],
        "consumer": gate["consumer"],
        "horizon": gate["horizon"],
        "cites": gate["cites"],
        "binding": (
            {
                "kind": gate["binding_kind"],
                "locator": gate["binding_locator"],
                "raised_sha256": gate["binding_sha256"],
            }
            if gate["binding_kind"] is not None
            else None
        ),
        "terminal_event": _terminal_event(
            ruling, gate_is_bound=gate["binding_kind"] is not None
        ),
        "options": [
            {
                "option_id": row["option_id"],
                "position": row["position"],
                "label": row["label"],
                "detail": row["detail"],
                "recommended": bool(row["recommended"]),
            }
            for row in options
        ],
        "checks": {
            "decay": {
                "original": gate["decay_check"],
                "effective": _effective_check(gate["decay_check"], revisions, "decay"),
            },
            "delivery": {
                "original": gate["delivery_check"],
                "effective": _effective_check(
                    gate["delivery_check"], revisions, "delivery"
                ),
            },
            "revisions": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "command": row["command"],
                    "at": row["at"],
                    "revised_by": row["revised_by"],
                    "reason": row["reason"],
                }
                for row in revisions
            ],
        },
        "observations": [
            {
                "id": row["id"],
                "at": row["at"],
                "kind": row["kind"],
                "command": row["command"],
                "exit_code": row["exit_code"],
                "note": row["note"],
            }
            for row in observations
        ],
        "audit_events": [
            {
                "id": row["id"],
                "at": row["at"],
                "actor": row["actor"],
                "verb": row["verb"],
                "detail": row["detail"],
            }
            for row in audit_events
        ],
    }
    _validate_record(record, f"gate {gate['id']}")
    return record


def export_gates(db_path: Path | None = None, gate_id: str | None = None) -> bytes:
    """Export all gates, or one gate, from one read-only database snapshot."""
    conn = _connect_read_only(db_path)
    try:
        conn.execute("BEGIN")
        migrations = _validated_migrations(conn)
        where = " WHERE id = ?" if gate_id else ""
        params = (gate_id,) if gate_id else ()
        gates = [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM gates{where} ORDER BY raised_at, id", params
            )
        ]
        if gate_id and not gates:
            raise JanusError(f"no such gate: {gate_id}")

        rulings = {
            row["gate_id"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM rulings"
                + (" WHERE gate_id = ?" if gate_id else "")
                + " ORDER BY gate_id",
                params,
            )
        }
        options = _group_rows(conn, "gate_options", "gate_id, position, option_id", gate_id)
        observations = _group_rows(conn, "observations", "gate_id, at, id", gate_id)
        revisions = _group_rows(conn, "check_revisions", "gate_id, id", gate_id)
        audit_events = _group_rows(conn, "audit_events", "gate_id, id", gate_id)

        records = []
        for gate in gates:
            gid = gate["id"]
            record = _record(
                gate,
                rulings.get(gid),
                options.get(gid, []),
                observations.get(gid, []),
                revisions.get(gid, []),
                audit_events.get(gid, []),
            )
            records.append(
                {
                    "record": record,
                    "integrity": {
                        "algorithm": "sha256",
                        "canonicalizer": CANONICALIZER,
                        "record_sha256": _sha256(record),
                    },
                }
            )

        document = {
            "schema": DOCUMENT_SCHEMA,
            "source": {"module": "janus", "migrations": migrations},
            "selection": {"gate_id": gate_id},
            "semantics": SEMANTICS,
            "vocabulary": {
                "gate_states": list(STATE_VOCABULARY),
                "gate_kinds": list(core.KINDS),
                "binding_kinds": list(BINDING_KINDS),
            },
            "records": records,
        }
        envelope = {
            "schema": ENVELOPE_SCHEMA,
            "document": document,
            "integrity": {
                "algorithm": "sha256",
                "canonicalizer": CANONICALIZER,
                "document_sha256": _sha256(document),
            },
        }
        return canonical_json(envelope)
    except sqlite3.Error as exc:
        raise JanusError(f"cannot export this ledger: {exc}") from exc
    finally:
        conn.close()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise JanusError(f"cannot verify export: duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_float(value: str) -> None:
    raise JanusError(f"cannot verify export: {CANONICALIZER} refuses float {value}")


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GATE_ID_RE = re.compile(r"^g[0-9a-f]{11}$")
_UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JanusError(f"cannot verify export: {path} must be an object")
    actual = set(value)
    if actual != keys:
        missing = ", ".join(sorted(keys - actual)) or "none"
        extra = ", ".join(sorted(actual - keys)) or "none"
        raise JanusError(
            f"cannot verify export: {path} keys are incompatible "
            f"(missing: {missing}; extra: {extra})"
        )
    return value


def _string(value: Any, path: str, *, nullable: bool = False, nonempty: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or (nonempty and not value):
        suffix = " or null" if nullable else ""
        raise JanusError(f"cannot verify export: {path} must be a string{suffix}")


def _integer(value: Any, path: str, *, minimum: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JanusError(f"cannot verify export: {path} must be an integer")
    if minimum is not None and value < minimum:
        raise JanusError(f"cannot verify export: {path} must be at least {minimum}")


def _sha256_string(value: Any, path: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        suffix = " or null" if nullable else ""
        raise JanusError(f"cannot verify export: {path} must be lowercase SHA-256{suffix}")


def _utc_string(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise JanusError(f"cannot verify export: {path} must be a normalized UTC instant")


def _validate_source(value: Any) -> None:
    source = _exact_object(value, {"module", "migrations"}, "document.source")
    if source["module"] != "janus":
        raise JanusError("cannot verify export: source module must be janus")
    migrations = source["migrations"]
    if not isinstance(migrations, list) or not migrations:
        raise JanusError("cannot verify export: source migrations must be a non-empty array")
    versions: list[str] = []
    for index, item in enumerate(migrations):
        migration = _exact_object(
            item, {"version", "checksum"}, f"document.source.migrations[{index}]"
        )
        _string(migration["version"], f"migration[{index}].version", nonempty=True)
        _sha256_string(migration["checksum"], f"migration[{index}].checksum")
        versions.append(migration["version"])
    if versions != sorted(set(versions)):
        raise JanusError("cannot verify export: source migrations are not unique and ordered")


def _validate_option(value: Any, path: str) -> None:
    option = _exact_object(
        value,
        {"option_id", "position", "label", "detail", "recommended"},
        path,
    )
    _string(option["option_id"], f"{path}.option_id", nonempty=True)
    _integer(option["position"], f"{path}.position", minimum=0)
    _string(option["label"], f"{path}.label", nonempty=True)
    _string(option["detail"], f"{path}.detail", nullable=True)
    if not isinstance(option["recommended"], bool):
        raise JanusError(f"cannot verify export: {path}.recommended must be boolean")


def _validate_checks(value: Any, path: str) -> None:
    checks = _exact_object(value, {"decay", "delivery", "revisions"}, path)
    for kind in ("decay", "delivery"):
        check = _exact_object(checks[kind], {"original", "effective"}, f"{path}.{kind}")
        _string(check["original"], f"{path}.{kind}.original", nullable=True)
        _string(check["effective"], f"{path}.{kind}.effective", nullable=True)
    revisions = checks["revisions"]
    if not isinstance(revisions, list):
        raise JanusError(f"cannot verify export: {path}.revisions must be an array")
    for index, revision_value in enumerate(revisions):
        revision_path = f"{path}.revisions[{index}]"
        revision = _exact_object(
            revision_value,
            {"id", "kind", "command", "at", "revised_by", "reason"},
            revision_path,
        )
        _integer(revision["id"], f"{revision_path}.id", minimum=1)
        if revision["kind"] not in ("decay", "delivery"):
            raise JanusError(f"cannot verify export: {revision_path}.kind is incompatible")
        _string(revision["command"], f"{revision_path}.command", nonempty=True)
        _utc_string(revision["at"], f"{revision_path}.at")
        _string(revision["revised_by"], f"{revision_path}.revised_by", nonempty=True)
        _string(revision["reason"], f"{revision_path}.reason", nonempty=True)


def _validate_observation(value: Any, path: str) -> None:
    observation = _exact_object(
        value, {"id", "at", "kind", "command", "exit_code", "note"}, path
    )
    _integer(observation["id"], f"{path}.id", minimum=1)
    _utc_string(observation["at"], f"{path}.at")
    if observation["kind"] not in ("decay", "delivery"):
        raise JanusError(f"cannot verify export: {path}.kind is incompatible")
    _string(observation["command"], f"{path}.command", nonempty=True)
    _integer(observation["exit_code"], f"{path}.exit_code")
    _string(observation["note"], f"{path}.note", nullable=True)


def _validate_audit_event(value: Any, path: str) -> None:
    event = _exact_object(value, {"id", "at", "actor", "verb", "detail"}, path)
    _integer(event["id"], f"{path}.id", minimum=1)
    _utc_string(event["at"], f"{path}.at")
    _string(event["actor"], f"{path}.actor", nonempty=True)
    _string(event["verb"], f"{path}.verb", nonempty=True)
    _string(event["detail"], f"{path}.detail", nullable=True)


def _validate_binding(value: Any, path: str) -> dict[str, Any] | None:
    if value is None:
        return None
    binding = _exact_object(value, {"kind", "locator", "raised_sha256"}, path)
    if binding["kind"] not in BINDING_KINDS:
        raise JanusError(f"cannot verify export: {path}.kind is incompatible")
    _string(binding["locator"], f"{path}.locator", nonempty=True)
    _sha256_string(binding["raised_sha256"], f"{path}.raised_sha256")
    return binding


def _validate_terminal_event(
    value: Any,
    *,
    state: str,
    human_ruled: bool,
    gate_is_bound: bool,
    path: str,
) -> None:
    if state == "open":
        if value is not None:
            raise JanusError(f"cannot verify export: {path} must be null for an open gate")
        return
    event = _exact_object(
        value,
        {"state", "type", "at", "actor", "reason", "option_id", "binding_evidence"},
        path,
    )
    if event["state"] != state:
        raise JanusError(f"cannot verify export: {path}.state does not match the gate")
    expected_type = "human_ruling" if human_ruled else "non_ruling_closure"
    if event["type"] != expected_type:
        raise JanusError(f"cannot verify export: {path}.type is incompatible")
    _utc_string(event["at"], f"{path}.at")
    _string(event["actor"], f"{path}.actor", nonempty=True)
    _string(event["reason"], f"{path}.reason", nonempty=True)
    _string(event["option_id"], f"{path}.option_id", nullable=True)
    if not human_ruled and event["option_id"] is not None:
        raise JanusError(f"cannot verify export: {path}.option_id must be null")
    evidence = _exact_object(
        event["binding_evidence"], {"status", "sha256"}, f"{path}.binding_evidence"
    )
    expected_status = "not_applicable"
    if human_ruled and gate_is_bound:
        expected_status = "recorded" if evidence["sha256"] is not None else "invalid_missing"
    if evidence["status"] != expected_status:
        raise JanusError(f"cannot verify export: {path}.binding_evidence status is incompatible")
    if expected_status == "recorded":
        _sha256_string(evidence["sha256"], f"{path}.binding_evidence.sha256")
    elif evidence["sha256"] is not None:
        raise JanusError(f"cannot verify export: {path}.binding_evidence.sha256 must be null")


def _validate_record(record: dict[str, Any], path: str) -> tuple[str, str]:
    _exact_object(
        record,
        {
            "schema",
            "id",
            "state",
            "question",
            "kind",
            "raised_at",
            "raised_by",
            "decay",
            "consumer",
            "horizon",
            "cites",
            "binding",
            "terminal_event",
            "options",
            "checks",
            "observations",
            "audit_events",
        },
        path,
    )
    if record["schema"] != RECORD_SCHEMA:
        raise JanusError(f"cannot verify export: expected record {RECORD_SCHEMA}")
    gate_id = record["id"]
    if not isinstance(gate_id, str) or not _GATE_ID_RE.fullmatch(gate_id):
        raise JanusError(f"cannot verify export: {path}.id is incompatible")
    state = record["state"]
    descriptor = _STATE_BY_WORD.get(state)
    if descriptor is None:
        raise JanusError(f"cannot verify export: unknown gate state {state!r}")
    _string(record["question"], f"{path}.question", nonempty=True)
    if record["kind"] not in core.KINDS:
        raise JanusError(f"cannot verify export: gate {gate_id} has unknown kind")
    _utc_string(record["raised_at"], f"{path}.raised_at")
    _string(record["raised_by"], f"{path}.raised_by", nonempty=True)
    _string(record["decay"], f"{path}.decay", nonempty=True)
    _string(record["consumer"], f"{path}.consumer", nonempty=True)
    _string(record["horizon"], f"{path}.horizon", nullable=True)
    _string(record["cites"], f"{path}.cites", nullable=True)
    if record["cites"] is not None and not _GATE_ID_RE.fullmatch(record["cites"]):
        raise JanusError(f"cannot verify export: {path}.cites is incompatible")
    binding = _validate_binding(record["binding"], f"{path}.binding")
    terminal_event = record["terminal_event"]
    _validate_terminal_event(
        terminal_event,
        state=state,
        human_ruled=bool(descriptor["human_ruled"]),
        gate_is_bound=binding is not None,
        path=f"{path}.terminal_event",
    )
    options = record["options"]
    if not isinstance(options, list):
        raise JanusError(f"cannot verify export: {path}.options must be an array")
    option_ids: set[str] = set()
    for index, option in enumerate(options):
        _validate_option(option, f"{path}.options[{index}]")
        if option["position"] != index:
            raise JanusError(
                f"cannot verify export: {path}.options must use contiguous position order"
            )
        if option["option_id"] in option_ids:
            raise JanusError(
                f"cannot verify export: duplicate option id {option['option_id']!r}"
            )
        option_ids.add(option["option_id"])
    if terminal_event is not None and bool(descriptor["human_ruled"]):
        ruled_option = terminal_event["option_id"]
        if ruled_option is not None and ruled_option not in option_ids:
            raise JanusError(
                f"cannot verify export: {path}.terminal_event names an option "
                "the gate does not offer"
            )
        if state == "approved" and options and ruled_option is None:
            raise JanusError(
                f"cannot verify export: {path}.terminal_event must name one offered option"
            )
    _validate_checks(record["checks"], f"{path}.checks")
    observations = record["observations"]
    if not isinstance(observations, list):
        raise JanusError(f"cannot verify export: {path}.observations must be an array")
    for index, observation in enumerate(observations):
        _validate_observation(observation, f"{path}.observations[{index}]")
    audit_events = record["audit_events"]
    if not isinstance(audit_events, list):
        raise JanusError(f"cannot verify export: {path}.audit_events must be an array")
    for index, event in enumerate(audit_events):
        _validate_audit_event(event, f"{path}.audit_events[{index}]")
    return record["raised_at"], gate_id


def verify_export(raw: bytes | str) -> dict[str, Any]:
    """Verify an export's schemas and digests, returning its document.

    Successful verification proves content integrity only. It does not prove
    who produced the bytes, whether they are fresh, or whether any action is
    authorized.
    """
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        envelope = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JanusError(f"cannot verify export: invalid UTF-8 JSON: {exc}") from exc
    envelope = _exact_object(envelope, {"schema", "document", "integrity"}, "envelope")
    if envelope["schema"] != ENVELOPE_SCHEMA:
        raise JanusError(f"cannot verify export: expected schema {ENVELOPE_SCHEMA}")
    document = _exact_object(
        envelope["document"],
        {"schema", "source", "selection", "semantics", "vocabulary", "records"},
        "document",
    )
    integrity = _exact_object(
        envelope["integrity"],
        {"algorithm", "canonicalizer", "document_sha256"},
        "envelope.integrity",
    )
    if document["schema"] != DOCUMENT_SCHEMA:
        raise JanusError(f"cannot verify export: expected document {DOCUMENT_SCHEMA}")
    if integrity["algorithm"] != "sha256" or integrity["canonicalizer"] != CANONICALIZER:
        raise JanusError("cannot verify export: unsupported document integrity method")
    _sha256_string(integrity["document_sha256"], "envelope.integrity.document_sha256")
    if integrity["document_sha256"] != _sha256(document):
        raise JanusError("cannot verify export: document digest does not match")
    _validate_source(document["source"])
    selection = _exact_object(document["selection"], {"gate_id"}, "document.selection")
    selected_gate = selection["gate_id"]
    if selected_gate is not None and (
        not isinstance(selected_gate, str) or not _GATE_ID_RE.fullmatch(selected_gate)
    ):
        raise JanusError("cannot verify export: selected gate id is incompatible")
    if document["semantics"] != SEMANTICS:
        raise JanusError("cannot verify export: authority semantics are incompatible")
    vocabulary = document["vocabulary"]
    expected_vocabulary = {
        "gate_states": list(STATE_VOCABULARY),
        "gate_kinds": list(core.KINDS),
        "binding_kinds": list(BINDING_KINDS),
    }
    if vocabulary != expected_vocabulary:
        raise JanusError("cannot verify export: vocabulary is incompatible")
    records = document["records"]
    if not isinstance(records, list):
        raise JanusError("cannot verify export: records must be an array")
    seen_ids: set[str] = set()
    record_order: list[tuple[str, str]] = []
    for entry in records:
        entry = _exact_object(entry, {"record", "integrity"}, "record envelope")
        record = entry["record"]
        record_integrity = _exact_object(
            entry["integrity"],
            {"algorithm", "canonicalizer", "record_sha256"},
            "record integrity",
        )
        if not isinstance(record, dict):
            raise JanusError("cannot verify export: record must be an object")
        if (
            record_integrity["algorithm"] != "sha256"
            or record_integrity["canonicalizer"] != CANONICALIZER
        ):
            raise JanusError("cannot verify export: unsupported record integrity method")
        _sha256_string(record_integrity["record_sha256"], "record integrity digest")
        if record_integrity["record_sha256"] != _sha256(record):
            raise JanusError(f"cannot verify export: record {record.get('id')} digest mismatch")
        order_key = _validate_record(record, f"record {record.get('id')}")
        gate_id = order_key[1]
        if gate_id in seen_ids:
            raise JanusError(f"cannot verify export: duplicate gate id {gate_id}")
        seen_ids.add(gate_id)
        record_order.append(order_key)
    if record_order != sorted(record_order):
        raise JanusError("cannot verify export: records are not in canonical order")
    if selected_gate is not None and seen_ids != {selected_gate}:
        raise JanusError("cannot verify export: exact-gate selection does not match its record")
    return document
