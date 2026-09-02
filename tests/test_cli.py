"""Focused tests for process-wide command-line behavior."""

from __future__ import annotations

import json
import pytest

from janus import __version__, cli


def test_version_reports_the_importable_package_version(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--version"])

    assert stopped.value.code == 0
    assert capsys.readouterr().out == f"janus {__version__}\n"


def _raise(db, capsys, *extra):
    rc = cli.main([
        "--db", str(db), "--seat", "tester",
        "raise", "Ship this exact candidate?",
        "--kind", "authority",
        "--decay", "the candidate diverges from main",
        "--consumer", "the release runner re-verifies the ruled bytes before acting",
        *extra,
    ])
    assert rc == 0
    return capsys.readouterr()


def test_raise_json_returns_the_gate_as_one_object_on_stdout(tmp_path, capsys):
    # The wrong answer this fails against: an agent has to regex "raised gXXXX" out of prose
    # to learn the id of the gate it just raised — this repository's own round-trip test does
    # exactly that (`raised.split()[1]`). Machine output on stdout, advice on stderr.
    out = _raise(tmp_path / "janus.db", capsys, "--json")
    gate = json.loads(out.out)
    assert gate["id"].startswith("g") and gate["state"] == "open"
    assert gate["kind"] == "authority"
    assert "no decay check" in out.err and "no decay check" not in out.out


def test_raise_json_matches_show_json(tmp_path, capsys):
    db = tmp_path / "janus.db"
    raised = json.loads(_raise(db, capsys, "--json").out)
    assert cli.main(["--db", str(db), "show", raised["id"], "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown == raised


def test_decide_and_supersede_json_return_the_closed_gate(tmp_path, capsys):
    db = tmp_path / "janus.db"
    gate_id = json.loads(_raise(db, capsys, "--json").out)["id"]
    rc = cli.main(["--db", str(db), "--seat", "tester", "decide", gate_id,
                   "--approve", "--reason", "evidence attached", "--json"])
    assert rc == 0
    out = capsys.readouterr()
    closed = json.loads(out.out)
    assert closed["id"] == gate_id and closed["state"] == "approved"
    assert "human ruled" in out.err and out.out.count("{") >= 1

    other = json.loads(_raise(db, capsys, "--json").out)["id"]
    rc = cli.main(["--db", str(db), "--seat", "tester", "supersede", other,
                   "--reason", "the world moved past it", "--yes", "--json"])
    assert rc == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["state"] == "superseded"
    assert "NOBODY RULED" in out.err and "NOBODY RULED" not in out.out


def test_raise_without_json_still_prints_the_prose_and_advice_on_stdout(tmp_path, capsys):
    out = _raise(tmp_path / "janus.db", capsys)
    assert out.out.startswith("raised g") and "no decay check" in out.out
