"""A persisted work-packet key survives retries, races and lost responses."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from janus import cli, core


PACKET = "gallery:proof/1"
REQUEST = dict(question="Use the wide margin?", kind="taste", decay="The proof waits.",
               consumer="Print the selected proof.")
OPTIONS = [{"id": "wide", "label": "Wide", "recommended": True},
           {"id": "thin", "label": "Thin", "detail": "Narrow border"}]


def _raise(conn, *, seat="first", key=PACKET, **changes):
    return core.raise_gate(conn, actor=seat, request_id=key, **(REQUEST | changes))


def _cli(db, capsys, seat, key, *extra):
    args = ["--db", str(db), "--seat", seat, "raise", REQUEST["question"],
            "--kind", REQUEST["kind"], "--decay", REQUEST["decay"],
            "--consumer", REQUEST["consumer"], f"--request-id={key}", *extra]
    try:
        rc = cli.main(args)
    except SystemExit as exc:  # argparse validation exits before command dispatch
        rc = exc.code
    return rc, capsys.readouterr()


def test_lost_reply_then_another_seat_retries_a_closed_gate(tmp_path, capsys):
    db = tmp_path / "janus.db"
    rc, reply = _cli(db, capsys, "first", PACKET, "--quiet")
    assert rc == 0
    gid = reply.out.strip()
    with closing(core.connect(db)) as conn:
        core.revise_check(conn, gid, "delivery", "false", "tester", "first measurement")
        original = core.close_gate(conn, gid, state="refused", reason="Different margin",
                                   actor="owner")
        before = list(conn.iterdump())
    rc, reply = _cli(db, capsys, "next", PACKET, "--json")
    assert rc == 0
    assert json.loads(reply.out) == original
    assert original["raised_by"] == core.seat_actor("first")
    assert "request_id" not in original
    rc, reply = _cli(db, capsys, "next", PACKET)
    assert rc == 0 and gid in reply.out
    assert core.seat_actor("next") not in reply.out
    assert "refused" in reply.out and core.seat_actor("first") in reply.out
    with closing(core.connect(db)) as conn:
        assert list(conn.iterdump()) == before


def test_original_inputs_determine_whether_a_request_is_a_retry(tmp_path):
    with closing(core.connect(tmp_path / "janus.db")) as conn:
        citation = _raise(conn, key="earlier")
        gid = _raise(conn, question="   Use  the wide margin?\n", options=OPTIONS)
        assert _raise(conn, seat="replacement", options=OPTIONS) == gid
        changes = [
            {"question": "Use the thin margin?"}, {"kind": "resource"},
            {"decay": "Another consequence"}, {"consumer": "Another seat"},
            {"decay_check": "check-cost"}, {"delivery_check": "check-print"},
            {"horizon": "2027-03-01"}, {"cites": citation}, {"options": []},
            {"options": list(reversed(OPTIONS))},
            {"options": [OPTIONS[0] | {"id": "wider"}, OPTIONS[1]]},
            {"options": [OPTIONS[0] | {"label": "Wider"}, OPTIONS[1]]},
            {"options": [OPTIONS[0], OPTIONS[1] | {"detail": "Changed border"}]},
            {"options": [OPTIONS[0] | {"recommended": False},
                         OPTIONS[1] | {"recommended": True}]},
            {"binding": core.resolve_binding("text", "bind the proof")},
        ]
        before = list(conn.iterdump())
        for change in changes:
            with pytest.raises(core.JanusError):
                _raise(conn, **({"options": OPTIONS} | change))
        assert list(conn.iterdump()) == before


def test_binding_conflict_compares_raise_time_bytes_and_locator(tmp_path, capsys):
    db = tmp_path / "janus.db"
    first = tmp_path / "first-proof"
    copy = tmp_path / "second-proof"
    for path in (first, copy):
        path.write_bytes(b"initial bytes")
    bind = ["--bind-kind", "file", "--bind"]
    rc, reply = _cli(db, capsys, "first", PACKET, *bind, str(first), "--quiet")
    assert rc == 0
    gid = reply.out.strip()
    first.write_bytes(b"changed bytes")
    with closing(core.connect(db)) as conn:
        core.close_gate(conn, gid, state="approved", reason="approve revised proof", actor="owner")
    for path in (first, copy):
        rc, reply = _cli(db, capsys, "next", PACKET, *bind, str(path), "--quiet")
        assert rc != 0 and not reply.out and reply.err
        assert str(path) not in reply.err and "changed bytes" not in reply.err
    first.write_bytes(b"initial bytes")
    rc, reply = _cli(db, capsys, "next", PACKET, *bind, str(first), "--quiet")
    assert rc == 0 and reply.out.strip() == gid
    first.unlink()
    rc, reply = _cli(db, capsys, "next", PACKET, *bind, str(first), "--quiet")
    assert rc != 0 and not reply.out


def test_request_id_grammar_and_case_sensitivity(tmp_path, capsys):
    db = tmp_path / "janus.db"
    ids = []
    for key in ("a", "A", "1" + "x" * 127, "0.a_B-c:d/e"):
        rc, reply = _cli(db, capsys, "first", key, "--quiet")
        assert rc == 0
        ids.append(reply.out.strip())
    assert len(set(ids)) == 4
    for key in ("", "x" * 129, "/x", "x y", "x\n", "é"):
        rc, reply = _cli(db, capsys, "first", key, "--quiet")
        assert rc != 0 and not reply.out and reply.err
    with closing(core.connect(db)) as conn:
        assert {g["id"] for g in core.list_gates(conn)} == set(ids)


@pytest.mark.parametrize("different_inputs", [False, True])
def test_concurrent_first_writers_share_only_matching_requests(tmp_path, different_inputs):
    db = tmp_path / "janus.db"
    core.connect(db).close()
    start = Barrier(2, timeout=10)

    def submit(seat):
        with closing(core.connect(db)) as conn:
            start.wait()
            consumer = seat if different_inputs else "both"
            try:
                return _raise(conn, seat=seat, consumer=consumer)
            except core.JanusError:
                # Conflicts, unlike lock/setup failures, leave the winning payload readable.
                if not different_inputs:
                    raise
                rows = core.list_gates(conn)
                assert len(rows) == 1 and rows[0]["consumer"] != consumer
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(submit, ("east", "west")))
    with closing(core.connect(db)) as conn:
        [gate] = core.list_gates(conn)
        assert {gid for gid in replies if gid is not None} == {gate["id"]}
        assert replies.count(None) == int(different_inputs)
        assert [tuple(r) for r in conn.execute("SELECT verb,actor FROM audit_events")] == [
            ("raise", gate["raised_by"])]


def test_failed_option_write_leaves_key_reusable_on_same_connection(tmp_path):
    with closing(core.connect(tmp_path / "janus.db")) as conn:
        duplicate_options = [OPTIONS[0], OPTIONS[0]]
        with pytest.raises((core.JanusError, sqlite3.IntegrityError)):
            _raise(conn, options=duplicate_options)
        for table in ("gates", "gate_options", "audit_events"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        gid = _raise(conn, options=OPTIONS)
        assert core.get_gate(conn, gid)["state"] == "open"


def test_migration_keeps_legacy_and_unkeyed_raises_independent(tmp_path, monkeypatch):
    db = tmp_path / "janus.db"
    legacy = tmp_path / "pre-0006"
    legacy.mkdir()
    for migration in core.MIGRATIONS_DIR.glob("*.sql"):
        if migration.name[:4] <= "0005":
            (legacy / migration.name).write_bytes(migration.read_bytes())
    with monkeypatch.context() as patch:
        patch.setattr(core, "MIGRATIONS_DIR", legacy)
        with closing(core.connect(db)) as conn:
            assert "request_id" not in {r[1] for r in conn.execute("PRAGMA table_info(gates)")}
            conn.execute(
                "INSERT INTO gates(id,raised_at,raised_by,question,kind,decay,consumer)"
                " VALUES('g123456789ab','2026-04-01','first',:question,:kind,:decay,:consumer)",
                REQUEST,
            )
            conn.commit()
    with closing(core.connect(db)) as conn:
        loose = [core.raise_gate(conn, actor="first", **REQUEST) for _ in range(2)]
        keyed = _raise(conn)
        assert _raise(conn) == keyed
        assert len({"g123456789ab", *loose, keyed}) == 4
        assert [r[0] for r in conn.execute(
            "SELECT request_id FROM gates WHERE request_id IS NOT NULL"
        )] == [PACKET]
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute(
                "INSERT INTO gates(id,raised_at,raised_by,question,kind,decay,consumer,request_id)"
                " SELECT 'g23456789abc',raised_at,raised_by,question,kind,decay,consumer,request_id"
                " FROM gates WHERE id=?", (keyed,),
            )
