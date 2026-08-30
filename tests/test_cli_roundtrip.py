"""Black-box acceptance test for Janus's installed command-line lifecycle."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from janus import export as stable_export


def test_installed_cli_preserves_the_evidence_not_authority_boundary(tmp_path: Path):
    """The public executable carries one gate through its primary lifecycle."""
    executable = Path(sys.executable).with_name("janus")
    if os.name == "nt":
        executable = executable.with_suffix(".exe")
    assert executable.is_file(), "the installed janus console script is missing"

    db = tmp_path / "janus.db"
    artifact = tmp_path / "candidate.txt"
    artifact.write_text("candidate-v1")
    marker = tmp_path / "STORED_CHECK_RAN"
    marker_program = (
        "from pathlib import Path; "
        f"Path({str(marker)!r}).write_text('stored check ran')"
    )
    stored_check = (
        f"{shlex.quote(sys.executable)} -c {shlex.quote(marker_program)}"
    )
    env = os.environ.copy()
    env.update({"HOME": str(tmp_path), "USER": "tester", "PYTHONNOUSERSITE": "1"})
    env.pop("PYTHONPATH", None)

    def run(*args: str) -> str:
        result = subprocess.run(
            [
                str(executable),
                "--db",
                str(db),
                "--seat",
                "codex",
                *args,
            ],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    raised = run(
        "raise",
        "Ship this exact candidate?",
        "--kind",
        "authority",
        "--decay",
        "the candidate diverges from main",
        "--consumer",
        "the release runner re-verifies the ruled bytes before acting",
        "--decay-check",
        stored_check,
        "--delivery-check",
        stored_check,
        "--bind-kind",
        "file",
        "--bind",
        str(artifact),
    )
    gate_id = raised.split()[1]
    assert gate_id.startswith("g")
    assert not marker.exists(), "raising a gate executed stored command text"

    gates = json.loads(run("list", "--state", "all", "--json"))
    assert [(gate["id"], gate["state"]) for gate in gates] == [(gate_id, "open")]
    assert gates[0]["question"] == "Ship this exact candidate?"
    assert gates[0]["raised_by"] == "tester+codex"
    assert not marker.exists(), "listing gates executed stored command text"

    default_board = run()
    explicit_board = run("board")
    assert default_board == explicit_board
    assert gate_id in default_board
    assert "Reading this board is not authority to act." in default_board
    assert not marker.exists(), "the default board executed stored command text"

    before = run("show", gate_id)
    assert "current raise-time bytes match" in before
    assert not marker.exists(), "showing an open gate executed stored command text"

    context = run(
        "context",
        gate_id,
        "--project",
        "janus",
        "--action-class",
        "merge",
        "--environment",
        "test",
        "--fact",
        "tests_passed=yes",
        "--fact",
        "security_sensitive=no",
        "--evidence-ref",
        "ci:run-123",
    )
    assert "recorded decision context" in context
    assert "unknown never means safe" in context

    ruled = run(
        "decide",
        gate_id,
        "--approve",
        "--reason",
        "the exact candidate passed human review",
        "--reason-code",
        "tests.pass",
        "--counterfactual",
        "A failed required check would change this decision.",
    )
    assert "This records that a human ruled. It grants nothing" in ruled
    assert "ruled on bytes @" in ruled
    assert not marker.exists(), "recording a ruling executed stored command text"

    after = run("show", gate_id)
    assert "current ruling-time bytes match" in after
    assert "Reading this ruling is not authority" in after
    assert "feedback: tests.pass" in after
    assert "would change if: A failed required check" in after
    assert not marker.exists(), "showing a ruling executed stored command text"

    board = run("board")
    assert "PROMISED, NOT DELIVERED" in board
    assert gate_id in board
    assert not marker.exists(), "the read-only board executed stored command text"

    stats = json.loads(run("stats", "--json"))
    assert (stats["raised"], stats["closed"], stats["open"], stats["ruled"]) == (
        1,
        1,
        0,
        1,
    )
    assert stats["consumer_acted"] == {
        "eligible": 1,
        "measurable": 1,
        "confirmed": 0,
        "unknown": 0,
    }
    assert stats["observations"] == 0
    assert not marker.exists(), "reading statistics executed stored command text"

    doctor = run("doctor")
    assert "Janus records pending authority; it does not grant authority." in doctor
    assert not marker.exists(), "diagnostics executed stored command text"

    document = stable_export.verify_export(run("export", gate_id))
    assert document["semantics"]["rulings"] == "evidence_not_authority"
    record = document["records"][0]["record"]
    assert record["id"] == gate_id
    assert record["state"] == "approved"
    assert record["terminal_event"]["type"] == "human_ruling"
    assert record["terminal_event"]["binding_evidence"]["status"] == "recorded"
    learning_events = [
        event for event in record["audit_events"]
        if event["verb"] in {"decision_context", "decision_feedback"}
    ]
    assert [event["verb"] for event in learning_events] == [
        "decision_context",
        "decision_feedback",
    ]
    assert not marker.exists(), "exporting a gate executed stored command text"
