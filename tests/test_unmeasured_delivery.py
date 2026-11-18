"""Public CLI contract for finding approvals with no effective delivery check."""

import json
import shlex
import sqlite3
from contextlib import closing

import pytest

from janus import cli, core
from janus import export as stable_export


FILTER = "--unmeasured-delivery"
COMMAND = "janus list --unmeasured-delivery"


def _gate(conn, question, *, state="approved", option_id=None, **fields):
    values = dict(kind="authority", decay="the slot closes", consumer="release desk")
    values.update(fields)
    gid = core.raise_gate(conn, question=question, actor="builder", **values)
    if state != "open":
        core.close_gate(conn, gid, state=state, reason="reviewed", actor="reviewer",
                        option_id=option_id)
    return gid


def _read(db, capsys, *args):
    assert cli.main(["--db", str(db), *args]) == 0
    result = capsys.readouterr()
    assert not result.err
    return result.out


def test_one_read_identifies_decisions_and_matches_the_approved_json_projection(
    tmp_path, capsys, monkeypatch
):
    db = tmp_path / "janus.db"
    marker = tmp_path / "executed"
    check = f"touch {shlex.quote(str(marker))}"
    artifact = tmp_path / "release.txt"
    artifact.write_text("version selected at ruling time")
    question = "Which release should the weekend shift prepare for the gallery reopening on Monday?"
    with closing(core.connect(db)) as conn:
        choice = _gate(
            conn, question, kind="taste", consumer="weekend print crew", decay_check=check,
            binding=core.resolve_binding("file", str(artifact)),
            options=[{"id": "expedite", "label": "Rush prints", "recommended": True},
                     {"id": "reserve", "label": "Hold the slot"}],
            option_id="reserve",
        )
        basic = _gate(conn, "Assign a runner?", kind="resource", consumer="dispatch desk")
        revised = _gate(conn, "Did the corrected check run?")
        core.revise_check(conn, revised, "delivery", check, "reviewer", "added after ruling")
        _gate(conn, "Did the original check run?", delivery_check=check)
        for command in ("true", "false", "exit 2"):
            observed = _gate(conn, f"Observed using {command}?", delivery_check=command)
            core.observe(conn, observed, "delivery", "observer")
        for state in ("open", "refused", "withdrawn", "expired", "superseded"):
            _gate(conn, f"Other state {state}?", state=state)
    artifact.unlink()
    approved = json.loads(_read(db, capsys, "list", "--state", "approved", "--json"))
    before = stable_export.export_gates(db)

    def unexpected(*args, **kwargs):
        pytest.fail("the filtered read tried to open for writing or verify a live artifact")

    monkeypatch.setattr(core, "connect", unexpected)
    monkeypatch.setattr(core, "digest_of_live", unexpected)
    filtered = json.loads(_read(db, capsys, "list", FILTER, "--json"))
    assert {row["id"] for row in filtered} == {basic, choice}
    assert filtered == [row for row in approved if row["id"] in {basic, choice}]
    out = _read(db, capsys, "list", FILTER)
    for value in (choice, question, "reserve", "Hold the slot", "weekend print crew",
                  basic, "Assign a runner?", "dispatch desk"):
        assert value in out
    assert "Rush prints" not in out
    assert revised not in out
    assert not marker.exists()
    assert stable_export.export_gates(db) == before


def test_board_hint_fits_both_render_paths_and_disappears_at_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("LINES", "12")
    db = tmp_path / "janus.db"
    with closing(core.connect(db)) as conn:
        missing = _gate(conn, "Assign the reviewer?")
        single = _read(db, capsys, "board")
        for i in range(8):
            _gate(conn, f"Pending decision {i}?", state="open")
            _gate(conn, f"Pending delivery {i}?", delivery_check="false")
        folded = _read(db, capsys, "board")
        full = _read(db, capsys, "board", "--all")
        core.revise_check(conn, missing, "delivery", "true", "reviewer", "consumer now reports")

    for board in (single, folded, full):
        hints = [line for line in board.splitlines() if COMMAND in line]
        assert len(hints) == 1
        assert "1" in hints[0] and len(hints[0]) <= 80
        assert missing not in board
    assert len(folded.splitlines()) <= 12 and "below the fold" in folded
    assert "PROMISED, NOT DELIVERED" in folded
    assert COMMAND not in _read(db, capsys, "board")
    assert json.loads(_read(db, capsys, "list", FILTER, "--json")) == []
    empty = _read(db, capsys, "list", FILTER).lower()
    assert "delivery" in empty and ("no " in empty or "0 " in empty)


def test_board_fits_a_three_digit_unmeasured_count_at_minimum_width(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    db = tmp_path / "janus.db"
    with closing(core.connect(db)) as conn:
        for i in range(100):
            _gate(conn, f"Assign reviewer {i}?")

    for flags in ([], ["--all"]):
        lines = _read(db, capsys, "board", *flags).splitlines()
        assert any("100" in line and COMMAND in line for line in lines)
        assert max(map(len, lines)) <= 80


@pytest.mark.parametrize("state", ["open", "approved"])
@pytest.mark.parametrize("filter_first", [True, False])
def test_explicit_state_and_unmeasured_delivery_are_mutually_exclusive(
    tmp_path, capsys, state, filter_first
):
    db = tmp_path / "untouched.db"
    args = [FILTER, "--state", state] if filter_first else ["--state", state, FILTER]
    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(db), "list", *args])
    assert exc.value.code == 2
    diagnostic = capsys.readouterr().err
    assert "--state" in diagnostic and FILTER in diagnostic and "not allowed with" in diagnostic
    assert not db.exists()


def test_lookup_neither_creates_a_ledger_nor_repairs_migration_history(tmp_path, capsys):
    db = tmp_path / "janus.db"
    args = ["--db", str(db), "list", FILTER, "--json"]
    assert cli.main(args) != 0
    result = capsys.readouterr()
    assert result.out == "" and "no Janus ledger" in result.err
    assert not db.exists()

    with closing(core.connect(db)) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = '0002_check_revisions'")
        conn.commit()
    assert cli.main(args) != 0
    result = capsys.readouterr()
    assert result.out == "" and "migration" in result.err
    with closing(sqlite3.connect(db)) as conn:
        assert not conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = '0002_check_revisions'"
        ).fetchone()


def test_storage_postcheck_completes_before_any_records_are_printed(tmp_path, capsys, monkeypatch):
    db = tmp_path / "janus.db"
    with closing(core.connect(db)) as conn:
        _gate(conn, "Return this approval?")
    real_blocker = core.storage_open_blocker
    calls = 0

    def broaden_sidecar_on_postcheck(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            db.with_name(db.name + "-wal").chmod(0o644)
        return real_blocker(path)

    monkeypatch.setattr(core, "storage_open_blocker", broaden_sidecar_on_postcheck)
    assert cli.main(["--db", str(db), "list", FILTER, "--json"]) != 0
    result = capsys.readouterr()
    assert calls == 2 and result.out == ""
    assert "WAL mode 0644" in result.err


def test_lookup_observes_one_snapshot_when_a_check_is_added_concurrently(
    tmp_path, capsys, monkeypatch
):
    db = tmp_path / "janus.db"
    with closing(core.connect(db)) as writer:
        missing = _gate(writer, "Who takes the shift?")
        validate = stable_export._validated_migrations

        def revise_after_first_read(reader):
            migrations = validate(reader)
            core.revise_check(writer, missing, "delivery", "true", "reviewer", "check now exists")
            return migrations

        with monkeypatch.context() as patch:
            patch.setattr(stable_export, "_validated_migrations", revise_after_first_read)
            first = json.loads(_read(db, capsys, "list", FILTER, "--json"))
        assert [gate["id"] for gate in first] == [missing]
        assert first[0]["effective_delivery_check"] is None
        assert json.loads(_read(db, capsys, "list", FILTER, "--json")) == []
