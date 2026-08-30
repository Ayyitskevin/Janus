"""Invariant tests for shadow-only prediction and chronological evaluation."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from janus import cli, core, decision_engine  # noqa: E402
from janus.core import JanusError  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    return core.connect(tmp_path / "janus.db")


def _gate(conn: sqlite3.Connection, **overrides) -> str:
    values = {
        "question": "Merge this exact candidate?",
        "kind": "authority",
        "decay": "the branch diverges",
        "consumer": "test: observe only; never execute from a prediction",
        "actor": "tester",
    }
    values.update(overrides)
    return core.raise_gate(conn, **values)


def _safe_facts() -> dict[str, bool]:
    return {
        "security_sensitive": False,
        "money": False,
        "legal": False,
        "live_data": False,
        "public_effect": False,
        "infrastructure": False,
        "reversible": True,
        "rollback_verified": True,
        "tests_passed": True,
        "non_author_reviewed": True,
    }


def _context(conn: sqlite3.Connection, gate_id: str, **overrides) -> dict:
    values = {
        "project": "janus",
        "action_class": "merge",
        "environment": "test",
        "facts": _safe_facts(),
        "evidence_refs": ["ci:run-123"],
        "actor": "tester",
    }
    values.update(overrides)
    return core.record_decision_context(conn, gate_id, **values)


def _output(verdict: str, code: str) -> str:
    return json.dumps(
        {"verdict": verdict, "reason_codes": [code], "summary": f"fixture {verdict}"}
    )


def test_ineligible_context_abstains_without_calling_the_model(conn):
    gate_id = _gate(conn)
    _context(conn, gate_id, facts={})
    adapter = decision_engine.InMemoryAdapter(_output("approve", "must.not.run"))

    prediction = decision_engine.record_shadow_prediction(
        conn,
        gate_id,
        engine=decision_engine.DecisionEngine(adapter),
        actor="tester+codex",
    )

    assert prediction["verdict"] == "abstain"
    assert "missing.security_sensitive" in prediction["reason_codes"]
    assert prediction["inference"]["attempted"] is False
    assert adapter.calls == []
    assert core.get_gate(conn, gate_id)["state"] == "open"


def test_valid_shadow_prediction_is_append_only_non_terminal_and_exported(conn, tmp_path):
    gate_id = _gate(conn)
    context = _context(conn, gate_id)
    adapter = decision_engine.InMemoryAdapter(_output("approve", "history.match"))

    prediction = decision_engine.record_shadow_prediction(
        conn,
        gate_id,
        engine=decision_engine.DecisionEngine(adapter),
        actor="tester+codex",
    )

    assert prediction["verdict"] == "approve"
    assert prediction["context_event_id"] == context["event_id"]
    assert prediction["inference"]["attempted"] is True
    assert core.get_gate(conn, gate_id)["state"] == "open"
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute(
            "UPDATE audit_events SET detail = '{}' WHERE id = ?", (prediction["event_id"],)
        )
    conn.rollback()

    from janus import export as stable_export

    db = tmp_path / "janus.db"
    conn.close()
    record = stable_export.verify_export(stable_export.export_gates(db, gate_id))["records"][
        0
    ]["record"]
    event = next(item for item in record["audit_events"] if item["id"] == prediction["event_id"])
    assert event["verb"] == "decision_prediction"
    assert json.loads(event["detail"])["verdict"] == "approve"


@pytest.mark.parametrize("content", ["", "not json", '{"verdict":"approve"}'])
def test_invalid_model_output_fails_closed_to_recorded_abstention(conn, content):
    gate_id = _gate(conn)
    _context(conn, gate_id)

    prediction = decision_engine.record_shadow_prediction(
        conn,
        gate_id,
        engine=decision_engine.DecisionEngine(decision_engine.InMemoryAdapter(content)),
        actor="tester",
    )

    assert prediction["verdict"] == "abstain"
    assert prediction["reason_codes"] == ["output.invalid"]
    assert prediction["inference"]["failure"] == "output.invalid"
    assert core.get_gate(conn, gate_id)["state"] == "open"


def test_inference_failure_never_retries_or_reroutes(conn):
    gate_id = _gate(conn)
    _context(conn, gate_id)
    adapter = decision_engine.InMemoryAdapter(failure="gateway.timeout")

    prediction = decision_engine.record_shadow_prediction(
        conn,
        gate_id,
        engine=decision_engine.DecisionEngine(adapter),
        actor="tester",
    )

    assert len(adapter.calls) == 1
    assert prediction["verdict"] == "abstain"
    assert prediction["reason_codes"] == ["inference.gateway.timeout"]


def test_new_context_arriving_during_inference_voids_the_prediction(conn):
    gate_id = _gate(conn)
    first = _context(conn, gate_id)

    class DriftingAdapter(decision_engine.InMemoryAdapter):
        def complete(self, **kwargs):
            _context(conn, gate_id, environment="local", evidence_refs=["ci:run-456"])
            return super().complete(**kwargs)

    prediction = decision_engine.record_shadow_prediction(
        conn,
        gate_id,
        engine=decision_engine.DecisionEngine(
            DriftingAdapter(_output("approve", "history.match"))
        ),
        actor="tester",
    )

    assert prediction["context_event_id"] == first["event_id"]
    assert prediction["verdict"] == "abstain"
    assert prediction["reason_codes"] == ["context.drift"]


def test_gate_closing_during_inference_records_no_prediction(conn):
    gate_id = _gate(conn)
    _context(conn, gate_id)

    class ClosingAdapter(decision_engine.InMemoryAdapter):
        def complete(self, **kwargs):
            core.close_gate(conn, gate_id, state="approved", reason="human won race", actor="kevin")
            return super().complete(**kwargs)

    with pytest.raises(JanusError, match="closed during inference"):
        decision_engine.record_shadow_prediction(
            conn,
            gate_id,
            engine=decision_engine.DecisionEngine(
                ClosingAdapter(_output("deny", "history.mismatch"))
            ),
            actor="tester",
        )
    assert decision_engine.list_shadow_predictions(conn, gate_id) == []


def test_database_refuses_a_prediction_after_the_human_ruling(conn):
    gate_id = _gate(conn)
    context = _context(conn, gate_id)
    core.close_gate(conn, gate_id, state="refused", reason="no", actor="kevin")
    payload = {
        "schema": decision_engine.PREDICTION_SCHEMA,
        "mode": "shadow",
        "verdict": "abstain",
        "reason_codes": ["late"],
        "summary": "too late",
        "context_event_id": context["event_id"],
        "context_sha256": context["context_sha256"],
        "input_sha256": "0" * 64,
        "policy_sha256": "0" * 64,
        "prompt_sha256": "0" * 64,
        "inference": {
            "attempted": False,
            "adapter": "memory",
            "model_alias": "fixture",
            "provider": None,
            "catalog_sha256": None,
            "request_sha256": None,
            "response_sha256": None,
            "failure": None,
        },
    }

    with pytest.raises(sqlite3.IntegrityError, match="existing open gate"):
        conn.execute(
            "INSERT INTO audit_events (at, actor, verb, gate_id, detail) VALUES (?,?,?,?,?)",
            (
                core.now(),
                "tester",
                "decision_prediction",
                gate_id,
                decision_engine.canonical_json(payload).decode(),
            ),
        )
    conn.rollback()


def test_chronological_evaluation_reports_every_denominator(conn):
    cases = [
        ("approve", "refused"),
        ("deny", "approved"),
        ("abstain", "approved"),
    ]
    for predicted, ruled in cases:
        gate_id = _gate(conn, question=f"case {predicted} then {ruled}")
        _context(conn, gate_id)
        decision_engine.record_shadow_prediction(
            conn,
            gate_id,
            engine=decision_engine.DecisionEngine(
                decision_engine.InMemoryAdapter(_output(predicted, f"fixture.{predicted}"))
            ),
            actor="tester",
        )
        core.close_gate(conn, gate_id, state=ruled, reason="human label", actor="kevin")
    no_prediction = _gate(conn, question="human ruling without prediction")
    core.close_gate(conn, no_prediction, state="approved", reason="legacy", actor="kevin")

    report = decision_engine.chronological_evaluation(conn)

    assert report["human_rulings"] == 4
    assert report["human_approvals"] == 3
    assert report["human_refusals"] == 1
    assert report["predictions_recorded"] == 3
    assert report["labeled_predictions"] == 3
    assert report["not_evaluated_predictions"] == 0
    assert report["selection"] == "latest_pre_ruling_prediction_per_gate"
    assert report["abstentions"] == {"count": 1, "denominator": 3}
    assert report["coverage"] == {"count": 2, "denominator": 3}
    assert report["agreement"] == {"count": 0, "denominator": 2}
    assert report["unsafe_false_approvals"] == {"count": 1, "denominator": 1}
    assert report["incorrect_denials"] == {"count": 1, "denominator": 2}
    assert all(item["predicted_at"] <= item["ruled_at"] for item in report["entries"])


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8140",
        "http://localhost:8140",
        "http://example.com:8140",
        "http://127.0.0.1:8140/path",
        "http://user@127.0.0.1:8140",
    ],
)
def test_vulcan_adapter_refuses_every_non_exact_loopback_origin(url):
    with pytest.raises(JanusError, match="loopback"):
        decision_engine.VulcanAdapter("simple", seat="codex", base_url=url)


def test_vulcan_adapter_refuses_hosted_alias_before_sending_prompt():
    adapter = decision_engine.VulcanAdapter("claude", seat="codex")
    paths = []

    def fake_request(path, payload=None):
        paths.append((path, payload))
        return {
            "id": "claude",
            "provider": "anthropic",
            "provider_type": "anthropic",
            "capabilities": ["chat"],
            "availability": "unchecked",
        }

    adapter._request = fake_request
    with pytest.raises(decision_engine.InferenceFailure, match="model.hosted_refused"):
        adapter.complete(
            system_prompt="secret prompt sentinel",
            input_document={"secret": "context sentinel"},
            response_schema=decision_engine.RESPONSE_SCHEMA,
        )
    assert paths == [("/v1/models/claude", None)]


def test_cli_requires_explicit_shadow_acknowledgement(conn, tmp_path, capsys):
    gate_id = _gate(conn)
    _context(conn, gate_id)
    db = tmp_path / "janus.db"
    conn.close()

    assert cli.main(["--db", str(db), "predict", gate_id]) == 2
    output = capsys.readouterr()
    assert "only shadow prediction exists" in output.err
    assert decision_engine.list_shadow_predictions(core.connect(db), gate_id) == []
