"""Invariant-level regression tests.

AGENTS.md: "Treat the gate ledger, ruling records, binding digests, and
migration history as high-integrity surfaces. Smallest reviewed change, plus an
invariant-level regression test."

These cover the three non-negotiable invariants and the two traps the corpus
found. Each test is written so it CANNOT pass vacuously — a check that only
passes because nothing was there is the failure mode this project has already
paid for elsewhere in the fleet.
"""

from __future__ import annotations

import errno
import json
import os
import stat
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from janus import core  # noqa: E402
from janus.core import JanusError  # noqa: E402


@pytest.fixture()
def conn(tmp_path):
    return core.connect(tmp_path / "t.db")


def _gate(conn, **kw):
    args = dict(
        question="Ship it?", kind="taste", decay="momentum is lost",
        consumer="claude-code: proceeds on approve", actor="tester",
    )
    args.update(kw)
    return core.raise_gate(conn, **args)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


# --------------------------------------- ledger filesystem trust boundary --
@pytest.mark.parametrize("mask", [0o000, 0o777])
def test_new_ledger_family_is_private_independent_of_umask(tmp_path, mask):
    """The OS-user boundary must not depend on the caller's ambient umask."""
    db = tmp_path / "new" / "nested" / "janus.db"
    previous = os.umask(mask)
    try:
        conn = core.connect(db)
    finally:
        os.umask(previous)

    assert _mode(db.parent.parent) == 0o700
    assert _mode(db.parent) == 0o700
    assert _mode(db) == 0o600
    family = [path for suffix in ("-wal", "-shm") if (path := Path(f"{db}{suffix}")).exists()]
    assert family, "WAL mode was not exercised, so sidecar privacy was not tested"
    assert {_mode(path) for path in family} == {0o600}
    conn.close()


def test_storage_privacy_reports_broad_modes_symlinks_and_hardlinks(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    db = private / "janus.db"
    conn = core.connect(db)

    db.chmod(0o644)
    private.chmod(0o775)
    for suffix in ("-wal", "-shm"):
        Path(f"{db}{suffix}").chmod(0o644)
    hardlink = private / "second-name.db"
    os.link(db, hardlink)

    findings = core.storage_privacy_findings(db)
    joined = "\n".join(findings)
    assert "directory mode 0775" in joined
    assert "database mode 0644" in joined
    assert "database has 2 hard links" in joined
    assert "WAL mode 0644" in joined
    assert "shared memory mode 0644" in joined
    with pytest.raises(JanusError, match="database has 2 hard links"):
        core.connect(db)

    link = tmp_path / "ledger-link.db"
    link.symlink_to(db)
    assert any(
        "database is a symbolic link" in item
        for item in core.storage_privacy_findings(link)
    )
    with pytest.raises(JanusError, match="database is a symbolic link"):
        core.connect(link)
    conn.close()


def test_storage_privacy_reports_missing_owner_type_and_rollback_journal(
    tmp_path, monkeypatch
):
    missing = tmp_path / "missing" / "janus.db"
    missing_findings = "\n".join(core.storage_privacy_findings(missing))
    assert "directory does not exist" in missing_findings
    assert "database is missing" in missing_findings

    db = tmp_path / "private" / "janus.db"
    conn = core.connect(db)
    conn.close()
    journal = Path(f"{db}-journal")
    journal.touch(mode=0o600)
    journal.chmod(0o644)
    actual_uid = os.geteuid()
    monkeypatch.setattr(core.os, "geteuid", lambda: actual_uid + 1)
    findings = "\n".join(core.storage_privacy_findings(db))
    assert "directory owner uid" in findings
    assert "database owner uid" in findings
    assert "rollback journal owner uid" in findings
    assert "rollback journal mode 0644" in findings

    monkeypatch.undo()
    db.unlink()
    db.mkdir()
    assert any(
        "database is not a regular file" in item
        for item in core.storage_privacy_findings(db)
    )


@pytest.mark.parametrize("mask", [0o000, 0o777])
def test_dangling_database_symlink_never_creates_its_target(tmp_path, mask):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    target = tmp_path / "target.db"
    link = directory / "janus.db"
    link.symlink_to(target)
    previous = os.umask(mask)
    try:
        with pytest.raises(JanusError, match="database is a symbolic link"):
            core.connect(link)
    finally:
        os.umask(previous)
    assert not target.exists()


def test_new_ledger_refuses_replaceable_existing_directory(tmp_path):
    broad = tmp_path / "broad"
    broad.mkdir(mode=0o700)
    broad.chmod(0o777)
    with pytest.raises(JanusError, match=r"ledger directory mode 0777 \(expected 0700\)"):
        core.connect(broad / "janus.db")

    with pytest.raises(
        core.StorageBoundaryError,
        match="permits another OS user to replace a child entry",
    ):
        core.connect(broad / "not-there-yet" / "janus.db")

    outer = tmp_path / "outer"
    outer.mkdir(mode=0o700)
    private = outer / "private"
    private.mkdir(mode=0o700)
    outer.chmod(0o777)
    with pytest.raises(JanusError, match="permits another OS user to replace"):
        core.connect(private / "janus.db")

    existing = tmp_path / "existing"
    existing_db = existing / "janus.db"
    conn = core.connect(existing_db)
    conn.close()
    existing.chmod(0o777)
    with pytest.raises(JanusError, match=r"directory mode 0777 \(expected 0700\)"):
        core.connect(existing_db)

    # Sticky protection covers an existing database name, but not absent
    # WAL/SHM/journal names SQLite may create next.
    existing.chmod(0o1777)
    with pytest.raises(JanusError, match=r"directory mode 1777 \(expected 0700\)"):
        core.connect(existing_db)


def test_existing_ledger_refuses_broad_database_family_modes(tmp_path):
    db = tmp_path / "private" / "janus.db"
    conn = core.connect(db)
    conn.close()

    db.chmod(0o666)
    with pytest.raises(JanusError, match="database mode 0666"):
        core.connect(db)

    db.chmod(0o600)
    journal = Path(f"{db}-journal")
    journal.touch(mode=0o600)
    journal.chmod(0o666)
    with pytest.raises(JanusError, match="rollback journal mode 0666"):
        core.connect(db)


def test_new_ledger_refuses_symbolic_link_in_directory_chain(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "alias"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(JanusError, match="directory path contains a symbolic link"):
        core.connect(link / "janus.db")
    assert not (target / "janus.db").exists()


def test_failed_permission_application_removes_the_exact_new_file(tmp_path, monkeypatch):
    db = tmp_path / "private" / "janus.db"

    def refuse_fchmod(descriptor, mode):
        raise PermissionError("test refusal")

    monkeypatch.setattr(core.os, "fchmod", refuse_fchmod)
    with pytest.raises(JanusError, match="cannot secure new ledger"):
        core.connect(db)
    assert not db.exists()


def test_cleanup_inspection_failure_preserves_the_primary_hardening_refusal(
    tmp_path, monkeypatch
):
    db = tmp_path / "uncertain-cleanup.db"

    def refuse_fchmod(descriptor, mode):
        raise PermissionError(errno.EACCES, "test fchmod refusal")

    def refuse_lstat(path):
        raise core.StorageBoundaryError("test cleanup inspection refusal")

    monkeypatch.setattr(core.os, "fchmod", refuse_fchmod)
    monkeypatch.setattr(core, "_lstat", refuse_lstat)
    with pytest.raises(
        core.StorageBoundaryError,
        match="cannot secure new ledger.*could not be safely removed",
    ):
        core._create_private_database(db)

    assert db.exists(), "uncertain cleanup must retain the owner-only entry"
    assert _mode(db) & 0o077 == 0


def test_descriptor_close_errors_are_structured_and_do_not_skip_cleanup(
    tmp_path, monkeypatch
):
    normal = tmp_path / "normal-close.db"
    real_close = os.close

    def close_then_report_error(descriptor):
        real_close(descriptor)
        raise OSError(errno.EIO, "test close refusal")

    with monkeypatch.context() as patch:
        patch.setattr(core.os, "close", close_then_report_error)
        with pytest.raises(JanusError, match="cannot finalize new ledger"):
            core._create_private_database(normal)
    assert normal.exists()
    normal.unlink()

    combined = tmp_path / "combined-failure.db"

    def refuse_fchmod(descriptor, mode):
        raise PermissionError(errno.EACCES, "test fchmod refusal")

    with monkeypatch.context() as patch:
        patch.setattr(core.os, "fchmod", refuse_fchmod)
        patch.setattr(core.os, "close", close_then_report_error)
        with pytest.raises(
            JanusError,
            match="cannot secure new ledger.*descriptor close also failed",
        ):
            core._create_private_database(combined)
    assert not combined.exists()


def test_fstat_failure_retains_a_private_file_and_returns_a_storage_refusal(
    tmp_path, monkeypatch
):
    db = tmp_path / "fstat.db"

    def refuse_fstat(descriptor):
        raise OSError(errno.EIO, "test fstat refusal")

    monkeypatch.setattr(core.os, "fstat", refuse_fstat)
    with pytest.raises(
        core.StorageBoundaryError,
        match="cannot inspect newly created ledger.*retained for operator inspection",
    ):
        core._create_private_database(db)

    assert db.exists()
    assert _mode(db) & 0o077 == 0


def test_failed_post_create_identity_check_refuses_to_unlink_an_uncertain_entry(
    tmp_path, monkeypatch
):
    db = tmp_path / "identity.db"
    real_lstat = core._lstat

    def report_an_extra_link(path):
        info = real_lstat(path)
        if info is None:
            return None
        values = list(info)
        values[3] = 2
        return os.stat_result(values)

    monkeypatch.setattr(core, "_lstat", report_an_extra_link)
    with pytest.raises(
        core.StorageBoundaryError,
        match="failed its identity check.*could not be safely removed",
    ):
        core._create_private_database(db)

    assert db.exists(), "an entry with uncertain identity must not be unlinked"
    assert _mode(db) == 0o600


def test_missing_database_cannot_appear_between_preflight_and_creation(tmp_path, monkeypatch):
    db = tmp_path / "private" / "janus.db"
    target = tmp_path / "broad-target.db"
    create_parents = core._create_private_parents

    def create_parents_then_substitute(parent):
        create_parents(parent)
        db.symlink_to(target)

    monkeypatch.setattr(core, "_create_private_parents", create_parents_then_substitute)
    with pytest.raises(JanusError, match="appeared during private creation"):
        core.connect(db)
    assert not target.exists()


def test_unsafe_directory_cannot_appear_during_private_parent_creation(
    tmp_path, monkeypatch
):
    db = tmp_path / "raced" / "janus.db"
    raced_directory = db.parent
    real_mkdir = Path.mkdir

    def insert_broad_directory(path, mode=0o777, parents=False, exist_ok=False):
        if path == raced_directory:
            real_mkdir(path, mode=0o700, parents=parents, exist_ok=exist_ok)
            path.chmod(0o777)
            raise FileExistsError(errno.EEXIST, "test competing mkdir", str(path))
        return real_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(core.Path, "mkdir", insert_broad_directory)
    with pytest.raises(
        core.StorageBoundaryError,
        match="unsafe directory appeared",
    ):
        core.connect(db)

    assert not db.exists()


def test_existing_database_cannot_be_recreated_if_it_disappears_after_preflight(
    tmp_path, monkeypatch
):
    db = tmp_path / "private" / "janus.db"
    conn = core.connect(db)
    conn.close()
    real_blocker = core.storage_open_blocker

    def approve_then_remove(path):
        blocker = real_blocker(path)
        assert blocker is None
        db.unlink()
        return None

    monkeypatch.setattr(core, "storage_open_blocker", approve_then_remove)
    with pytest.raises(core.StorageBoundaryError, match="cannot open ledger"):
        core.connect(db)

    assert not db.exists(), "mode=rw must refuse rather than recreate a vanished ledger"


def test_storage_path_normalizes_dot_dot_lexically_without_following_a_link(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    link = directory / "unused"
    link.symlink_to(elsewhere, target_is_directory=True)
    db_with_dot_dot = link / ".." / "janus.db"

    conn = core.connect(db_with_dot_dot)
    conn.close()

    assert (directory / "janus.db").exists()
    assert link.is_symlink(), "the path must exercise lexical, not resolved, normalization"
    assert not (tmp_path / "janus.db").exists()
    assert core.storage_path(db_with_dot_dot) == directory / "janus.db"


def test_storage_creation_os_errors_are_structured_cli_refusals(
    tmp_path, monkeypatch, capsys
):
    from janus import cli

    def refuse_mkdir(path, *args, **kwargs):
        raise PermissionError(errno.EACCES, "test mkdir refusal", str(path))

    with monkeypatch.context() as patch:
        patch.setattr(core.Path, "mkdir", refuse_mkdir)
        assert cli.main(["--db", str(tmp_path / "mkdir" / "janus.db"), "list"]) == 2
    mkdir_error = capsys.readouterr().err
    assert "janus: cannot create ledger directory" in mkdir_error
    assert "Traceback" not in mkdir_error

    parent = tmp_path / "open"
    parent.mkdir(mode=0o700)

    def refuse_open(*args, **kwargs):
        raise OSError(errno.ENOSPC, "test open refusal")

    with monkeypatch.context() as patch:
        patch.setattr(core.os, "open", refuse_open)
        assert cli.main(["--db", str(parent / "janus.db"), "list"]) == 2
    open_error = capsys.readouterr().err
    assert "janus: cannot create ledger file" in open_error
    assert "Traceback" not in open_error


def test_doctor_fails_loudly_without_repairing_existing_broad_modes(tmp_path):
    directory = tmp_path / "existing"
    directory.mkdir(mode=0o700)
    db = directory / "janus.db"
    conn = core.connect(db)
    conn.close()
    directory.chmod(0o775)
    db.chmod(0o644)
    before = (_mode(directory), _mode(db))

    result = _cli(db, "doctor")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "storage     FAILED" in result.stdout
    assert "no permissions were changed" in result.stdout
    assert result.stdout.count("database mode 0644") == 1
    assert (_mode(directory), _mode(db)) == before


def test_doctor_reports_a_missing_ledger_without_creating_it(tmp_path):
    db = tmp_path / "missing.db"

    result = _cli(db, "doctor")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "database is missing" in result.stdout
    assert "checks skipped" in result.stdout
    assert not db.exists()


def test_doctor_cannot_recreate_a_ledger_deleted_during_open(
    tmp_path, monkeypatch, capsys
):
    from janus import cli

    db = tmp_path / "janus.db"
    conn = core.connect(db)
    conn.close()
    real_blocker = core.storage_open_blocker
    blocker_calls = 0

    def approve_then_remove(path):
        nonlocal blocker_calls
        blocker_calls += 1
        blocker = real_blocker(path)
        assert blocker is None
        db.unlink()
        return None

    monkeypatch.setattr(core, "storage_open_blocker", approve_then_remove)
    assert cli.main(["--db", str(db), "doctor"]) == 1
    output = capsys.readouterr()

    assert blocker_calls == 1
    assert "database is missing" in output.out
    assert "checks skipped" in output.out
    assert not db.exists(), "doctor must preserve absence even across its open race"


def test_doctor_reports_a_directory_mode_finding_once(tmp_path):
    directory = tmp_path / "private"
    db = directory / "janus.db"
    conn = core.connect(db)
    conn.close()
    directory.chmod(0o775)

    result = _cli(db, "doctor")

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count("directory mode 0775 (expected 0700)") == 1


def test_doctor_does_not_mislabel_migration_integrity_as_storage(tmp_path, capsys):
    from janus import cli

    db = tmp_path / "janus.db"
    conn = core.connect(db)
    conn.execute("UPDATE schema_migrations SET checksum = 'tampered'")
    conn.commit()
    conn.close()

    assert cli.main(["--db", str(db), "doctor"]) == 2
    output = capsys.readouterr()
    assert "migration" in output.err
    assert "has changed since it was applied" in output.err
    assert "storage     FAILED" not in output.out
    assert "restrict or relocate" not in output.out


def test_doctor_accepts_a_private_ledger_family(tmp_path):
    db = tmp_path / "private" / "janus.db"
    conn = core.connect(db)
    conn.close()

    result = _cli(db, "doctor")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "storage     private" in result.stdout


def test_doctor_reports_wrong_type_before_opening_sqlite(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    db = directory / "janus.db"
    db.mkdir()

    result = _cli(db, "doctor")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "database is not a regular file" in result.stdout
    assert "checks skipped" in result.stdout
    assert "Traceback" not in result.stderr

    db.rmdir()
    conn = core.connect(db)
    conn.close()
    journal = Path(f"{db}-journal")
    journal.mkdir()

    sidecar_result = _cli(db, "doctor")

    assert sidecar_result.returncode == 1
    assert "rollback journal is not a regular file" in sidecar_result.stdout
    assert "checks skipped" in sidecar_result.stdout


def test_doctor_reports_inaccessible_storage_without_a_traceback(tmp_path):
    directory = tmp_path / "private"
    db = directory / "janus.db"
    conn = core.connect(db)
    conn.close()
    directory.chmod(0o000)
    try:
        result = _cli(db, "doctor")
    finally:
        directory.chmod(0o700)

    assert result.returncode == 1
    assert result.stdout.count(f"cannot inspect storage path {db}:") == 1
    assert "checks skipped" in result.stdout
    assert "Traceback" not in result.stderr


# ------------------------- invariant 1: open or closed, never both ----------
def test_a_gate_cannot_hold_two_terminal_states(conn):
    g = _gate(conn)
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    with pytest.raises(JanusError, match="already approved"):
        core.close_gate(conn, g, state="refused", reason="no", actor="kevin")
    assert core.get_gate(conn, g)["state"] == "approved"


def test_the_database_itself_refuses_a_second_terminal_row(conn):
    """Not just the Python guard — the PK must make it impossible."""
    g = _gate(conn)
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO rulings (gate_id, state, ruled_at, ruled_by, reason)"
            " VALUES (?,?,?,?,?)", (g, "refused", core.now(), "sneaky", "bypass"))


# ------------------------------ invariant 2: a ruling binds bytes -----------
def test_binding_detects_drift(conn, tmp_path):
    art = tmp_path / "a.txt"
    art.write_text("original")
    b = core.resolve_binding("file", str(art))
    g = _gate(conn, binding=b)
    ok, _ = core.verify_binding("file", str(art), core.get_gate(conn, g)["binding_sha256"])
    assert ok is True
    art.write_text("changed")
    ok, sentence = core.verify_binding("file", str(art), core.get_gate(conn, g)["binding_sha256"])
    assert ok is False and "NO LONGER MATCHES" in sentence


def test_unverifiable_binding_is_not_reported_as_fine(conn, tmp_path):
    """A missing artifact must read as 'cannot tell', never as a match."""
    art = tmp_path / "gone.txt"
    art.write_text("x")
    b = core.resolve_binding("file", str(art))
    art.unlink()
    ok, sentence = core.verify_binding("file", str(art), b.sha256)
    assert ok is None and "CANNOT VERIFY" in sentence


def test_ruling_records_the_digest_observed_at_ruling_time(conn, tmp_path):
    art = tmp_path / "a.txt"
    art.write_text("v1")
    g = _gate(conn, binding=core.resolve_binding("file", str(art)))
    art.write_text("v2")
    core.close_gate(conn, g, state="approved", reason="ok", actor="kevin")
    gate = core.get_gate(conn, g)
    # The ruling pins what was ruled on, which differs from what was raised.
    assert gate["ruling"]["bound_sha256"] != gate["binding_sha256"]
    assert gate["ruling"]["bound_sha256"] == core.digest_file(art)


def test_show_checks_current_bytes_against_the_ruling_not_the_raise(tmp_path):
    """The artifact may legitimately change while the gate is still open.

    The ruling rebinds to what the human actually saw.  Once ruled, current
    applicability must compare with those bytes rather than the older bytes
    present when the question was raised.
    """
    db = tmp_path / "ruling-basis.db"
    conn = core.connect(db)
    art = tmp_path / "proposal.txt"
    art.write_text("raised bytes")
    g = _gate(conn, binding=core.resolve_binding("file", str(art)))
    art.write_text("ruled bytes")
    core.close_gate(conn, g, state="approved", reason="approve current bytes",
                    actor="kevin")

    shown = _cli(db, "show", g).stdout
    assert "current ruling-time bytes match" in shown, shown
    assert "BINDING CHANGED SINCE THE RULING" not in shown, shown


def test_show_keeps_a_drifted_ruling_as_historical_evidence(tmp_path):
    db = tmp_path / "delivered.db"
    conn = core.connect(db)
    art = tmp_path / "consumer.txt"
    art.write_text("before")
    g = _gate(
        conn,
        kind="authority",
        binding=core.resolve_binding("file", str(art)),
        delivery_check=f"test \"$(cat {art})\" = after",
    )
    core.close_gate(conn, g, state="approved", reason="make the edit", actor="kevin")
    art.write_text("after")
    core.observe(conn, g, "delivery", "tester")

    shown = _cli(db, "show", g).stdout
    assert "BINDING CHANGED SINCE THE RULING" in shown, shown
    assert "delivery check later reported success" in shown, shown
    assert "does not identify today's bytes" in shown, shown
    assert "ruling remains evidence about its recorded bytes" in shown, shown
    assert "ruling on this gate as void" not in shown, shown
    assert "raise a new gate" not in shown, shown


def test_show_keeps_unexplained_post_ruling_drift_loud(tmp_path):
    db = tmp_path / "unexplained.db"
    conn = core.connect(db)
    art = tmp_path / "consumer.txt"
    art.write_text("before")
    g = _gate(conn, kind="authority",
              binding=core.resolve_binding("file", str(art)))
    core.close_gate(conn, g, state="approved", reason="make the edit", actor="kevin")
    art.write_text("mystery")

    shown = _cli(db, "show", g).stdout
    assert "BINDING CHANGED SINCE THE RULING" in shown, shown
    assert "cannot tell approved delivery from unrelated drift" in shown, shown
    assert "ruling remains evidence about its recorded bytes" in shown, shown


def test_show_keeps_delivery_evidence_separate_when_ruled_bytes_still_live(tmp_path):
    db = tmp_path / "separate-evidence.db"
    conn = core.connect(db)
    art = tmp_path / "unchanged.txt"
    art.write_text("same bytes")
    g = _gate(conn, binding=core.resolve_binding("file", str(art)),
              delivery_check="true")
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    core.observe(conn, g, "delivery", "tester")

    shown = _cli(db, "show", g).stdout
    assert "current ruling-time bytes match" in shown, shown
    assert "check reported success" in shown, shown
    assert "does not identify live artifact bytes, prove causality, or grant authority" in shown
    assert "occurred/landed" not in shown, shown


@pytest.mark.parametrize("state", core.RULED_STATES)
def test_a_bound_gate_cannot_be_ruled_on_when_its_bytes_cannot_be_read(
        conn, tmp_path, state):
    """Record validation is not downstream authorization.

    An approval or refusal on a bound gate must carry the bytes observed at
    ruling time. If those bytes cannot be read, persisting NULL would create a
    terminal record nobody can ever re-check.
    """
    art = tmp_path / "gone-at-ruling.txt"
    art.write_text("reviewed bytes")
    g = _gate(conn, binding=core.resolve_binding("file", str(art)))
    art.unlink()

    with pytest.raises(JanusError, match=f"cannot record {state}"):
        core.close_gate(conn, g, state=state, reason="rule anyway", actor="kevin")

    gate = core.get_gate(conn, g)
    assert gate["state"] == "open"
    assert gate["ruling"] is None


def test_a_bound_ruling_cannot_disable_rebinding(conn, tmp_path):
    """A deliberate no-rebind call must refuse without blaming the filesystem."""
    art = tmp_path / "readable.txt"
    art.write_text("reviewed bytes")
    g = _gate(conn, binding=core.resolve_binding("file", str(art)))

    with pytest.raises(JanusError, match="ruling-time rebinding was disabled"):
        core.close_gate(conn, g, state="approved", reason="rule anyway",
                        actor="kevin", rebind=False)

    assert core.get_gate(conn, g)["state"] == "open"


@pytest.mark.parametrize("state", core.RULED_STATES)
def test_the_database_refuses_a_digestless_ruling_on_a_bound_gate(
        conn, tmp_path, state):
    """The invariant must hold even when a caller bypasses Python."""
    art = tmp_path / "bound.txt"
    art.write_text("reviewed bytes")
    g = _gate(conn, binding=core.resolve_binding("file", str(art)))

    with pytest.raises(sqlite3.IntegrityError, match="bound ruling must record"):
        conn.execute(
            "INSERT INTO rulings "
            "(gate_id, state, ruled_at, ruled_by, reason, bound_sha256) "
            "VALUES (?,?,?,?,?,?)",
            (g, state, core.now(), "kevin", "rule anyway", None),
        )
    conn.rollback()
    assert core.get_gate(conn, g)["state"] == "open"


# --------------------- invariant 3: reading is not authority ----------------
def test_janus_exposes_no_authorization_verb():
    """Guard against a future 'is_authorized' creeping in.

    Janus must never answer 'may I act?'. If this fails, something added a
    permission-path surface and that is wrong even when convenient.
    """
    banned = ("is_authorized", "authorize", "permit", "allow_action", "can_act")
    surface = set(dir(core))
    assert not surface & set(banned)


# ------------------------------------- append-only is real, not documented --
@pytest.mark.parametrize(
    "table", ["gates", "rulings", "audit_events", "observations", "check_revisions"])
def test_update_and_delete_are_refused_on_real_rows(conn, table):
    g = _gate(conn)
    core.close_gate(conn, g, state="approved", reason="ok", actor="kevin")
    core.audit(conn, "tester", "probe", g, "x")
    conn.execute(
        "INSERT INTO observations (gate_id, at, kind, command, exit_code)"
        " VALUES (?,?,?,?,?)", (g, core.now(), "decay", "true", 0))
    core.revise_check(conn, g, "decay", "true", "tester", "the old one read an env var")
    conn.commit()
    rows = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
    assert rows > 0, f"{table} is empty — this test would pass vacuously"
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute(f"UPDATE {table} SET rowid = rowid")
    conn.rollback()
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute(f"DELETE FROM {table}")
    conn.rollback()


# ------------------------------------------- corpus-driven requirements -----
def test_a_gate_with_options_cannot_be_approved_without_choosing_one(conn):
    g = _gate(conn, options=[{"id": "a", "label": "A", "recommended": True},
                             {"id": "b", "label": "B"}])
    with pytest.raises(JanusError):
        core.close_gate(conn, g, state="approved", reason="ok", actor="kevin")


def test_an_approval_cannot_name_an_option_the_gate_does_not_offer(conn):
    g = _gate(conn, options=[{"id": "a", "label": "A", "recommended": True}])
    with pytest.raises(JanusError):
        core.close_gate(conn, g, state="approved", reason="ok", actor="kevin",
                        option_id="ghost")


def test_superseded_is_available_as_a_terminal_state(conn):
    """The corpus' most common ending: the world moved past the question."""
    g = _gate(conn)
    core.close_gate(conn, g, state="superseded", reason="PR merged without it",
                    actor="observer")
    assert core.get_gate(conn, g)["state"] == "superseded"


def test_kind_enum_has_no_escape_hatch(conn):
    with pytest.raises(JanusError, match="kind must be one of"):
        _gate(conn, kind="other")


def test_a_question_too_long_to_answer_is_refused(conn):
    with pytest.raises(JanusError, match="280"):
        _gate(conn, question="x " * 200)


# ------------------------------------------------------ seat attribution ----
def test_seat_is_appended_to_the_os_user_never_replaces_it(monkeypatch):
    monkeypatch.setenv("USER", "kevin-lee")
    assert core.seat_actor(None) == "kevin-lee"
    assert core.seat_actor("codex") == "kevin-lee+codex"


def test_a_seat_label_cannot_smuggle_another_identity(monkeypatch):
    monkeypatch.setenv("USER", "kevin-lee")
    for bad in ("root/../admin", "kevin lee", "SEAT!", "a" * 40):
        with pytest.raises(JanusError):
            core.seat_actor(bad)


# ------------------------------------------------------------- migrations ---
def test_an_edited_applied_migration_is_refused(conn, tmp_path, monkeypatch):
    conn.execute("UPDATE schema_migrations SET checksum = 'tampered'")
    conn.commit()
    with pytest.raises(JanusError, match="has changed since it was applied"):
        core.migrate(conn)


# ------------------------------------------------------------ observations --
def test_an_observation_never_changes_state(conn):
    g = _gate(conn, decay_check="true")
    core.observe(conn, g, "decay", "tester")
    assert core.get_gate(conn, g)["state"] == "open"


def test_doctor_exits_zero_on_a_healthy_ledger(tmp_path):
    db = tmp_path / "d.db"
    conn = core.connect(db)
    conn.close()
    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "doctor"],
        capture_output=True, text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "HOME": str(tmp_path),
            "USER": "tester",
        },
    )
    assert r.returncode == 0, r.stderr
    assert "append-only enforced (UPDATE" in r.stdout
    assert "append-only enforced (DELETE" in r.stdout


def test_a_file_binding_is_stored_absolute(conn, tmp_path, monkeypatch):
    """A relative locator is meaningless to every reader but the raiser.

    Found by real adoption: the first gate another seat raised bound
    "docs/adr/0054-....md" and reads CANNOT VERIFY to anyone whose cwd differs.
    A binding exists so someone ELSE can re-check it later.
    """
    art = tmp_path / "sub" / "a.txt"
    art.parent.mkdir()
    art.write_text("x")
    monkeypatch.chdir(tmp_path)
    b = core.resolve_binding("file", "sub/a.txt")
    assert Path(b.locator).is_absolute(), b.locator
    # And it must still verify from a different working directory.
    monkeypatch.chdir(art.parent)
    ok, _ = core.verify_binding("file", b.locator, b.sha256)
    assert ok is True


@pytest.fixture()
def git_repo(tmp_path):
    """A throwaway repo with one commit, isolated from the caller's git config."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*argv):
        r = subprocess.run(["git", "-C", str(repo), *argv],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("v1")
    git("add", "a.txt")
    git("commit", "-qm", "one")
    return repo, git


def test_a_git_binding_pins_the_commit_and_does_not_follow_the_ref(git_repo):
    """A gate bound to "<repo>@HEAD" must not go void because HEAD moved.

    Found by adoption: g55daf244a78 bound this repo at HEAD and read BINDING NO
    LONGER MATCHES one unrelated commit later. Invariant 2 says a ruling binds a
    digest, not a name — so the name is resolved when the gate is raised.
    """
    repo, git = git_repo
    b = core.resolve_binding("git", f"{repo}@HEAD")
    head = git("rev-parse", "HEAD")
    assert b.locator.endswith(f"@{head}"), b.locator
    assert "HEAD" not in b.locator

    (repo / "b.txt").write_text("unrelated")
    git("add", "b.txt")
    git("commit", "-qm", "two")
    assert git("rev-parse", "HEAD") != head, "HEAD did not move — test is vacuous"

    ok, sentence = core.verify_binding("git", b.locator, b.sha256)
    assert ok is True, sentence


def test_a_locator_stored_before_this_fix_is_still_checked_correctly(git_repo):
    """Rows already in the live ledger hold symbolic locators; they must still work.

    `~/.janus/janus.db` contains gates raised before this change, whose locators
    are `<repo>@HEAD`. Resolving at raise time does nothing for those rows, and
    `verify_binding` must go on reporting drift for them rather than quietly
    treating an unrecognised shape as fine. This deliberately does NOT exercise
    the new pinning behaviour — a non-author review flagged that it must not be
    counted as proof of it.
    """
    repo, git = git_repo
    b = core.resolve_binding("git", f"{repo}@HEAD")
    git("commit", "-q", "--amend", "-m", "one, reworded")
    ok, sentence = core.verify_binding("git", f"{repo}@HEAD", b.sha256)
    assert ok is False and "NO LONGER MATCHES" in sentence


def test_a_pinned_git_binding_can_only_match_or_become_unverifiable(git_repo):
    """The consequence of pinning, stated as a test rather than left to be found.

    A commit id names immutable bytes, so once the revision is resolved a git
    binding CANNOT drift — the check can now only answer "matches" or "cannot
    verify". That is the intended semantics and it differs from `file` bindings,
    which still genuinely drift because a path's contents change in place.

    What must not happen is the third answer: an unreachable repository reading
    as fine. Asserted here for the git branch the way it already is for files.
    """
    repo, _ = git_repo
    b = core.resolve_binding("git", f"{repo}@HEAD")
    ok, _ = core.verify_binding("git", b.locator, b.sha256)
    assert ok is True, "the pinned binding should match before the repo goes away"

    shutil.rmtree(repo)
    ok, sentence = core.verify_binding("git", b.locator, b.sha256)
    assert ok is None and "CANNOT VERIFY" in sentence


def test_a_git_binding_repo_path_is_stored_absolute(git_repo, monkeypatch):
    repo, _ = git_repo
    monkeypatch.chdir(repo.parent)
    b = core.resolve_binding("git", "repo@HEAD")
    assert Path(b.locator.rsplit("@", 1)[0]).is_absolute(), b.locator
    monkeypatch.chdir(Path(__file__).parent)
    ok, sentence = core.verify_binding("git", b.locator, b.sha256)
    assert ok is True, sentence

# ------------------------------------------------------------------ board ---
def _board(db: Path, *argv, lines: int = 40, cols: int = 110):
    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "board", *argv],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(db.parent), "USER": "tester",
             "COLUMNS": str(cols), "LINES": str(lines)},
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_the_board_sorts_by_observed_decay_not_by_age(tmp_path):
    """ADR 0001: a board sorted by observed decay is sorted by risk of loss.

    The older gate is unmeasured; the newer one has a decay check that fired.
    Age-ordering or insertion-ordering would both put the older one first, so
    this can only pass because the observation moved it.
    """
    db = tmp_path / "b.db"
    conn = core.connect(db)
    old = _gate(conn, question="OLD unmeasured one")
    new = _gate(conn, question="NEW one whose decay landed", decay_check="true")
    core.observe(conn, new, "decay", "tester")
    assert core.get_gate(conn, old)["raised_at"] <= core.get_gate(conn, new)["raised_at"]

    out = _board(db)
    assert out.index(new) < out.index(old), out


def test_the_board_never_renders_an_unchecked_claim_as_an_observation(tmp_path):
    """A decay sentence nobody can re-run is a claim. It must not look measured."""
    db = tmp_path / "b.db"
    conn = core.connect(db)
    _gate(conn, question="no check at all")
    measured = _gate(conn, question="checked and there is still time", decay_check="false")
    core.observe(conn, measured, "decay", "tester")

    out = _board(db)
    assert "unmeasured" in out
    assert "not yet" in out
    assert "never been checked" in out


def test_the_board_discloses_what_the_one_screen_fold_hid(tmp_path):
    """No silent caps. A board that quietly drops rows is the surface it replaces."""
    db = tmp_path / "b.db"
    conn = core.connect(db)
    ids = [_gate(conn, question=f"gate number {i}") for i in range(8)]

    folded = _board(db, lines=12)          # (12 - 8) // 2 == 2 gates fit
    assert "more below the fold" in folded
    hidden = [g for g in ids if g not in folded]
    assert len(hidden) == 6, folded
    assert f"{len(hidden)} more below the fold" in folded

    everything = _board(db, "--all", lines=12)
    assert all(g in everything for g in ids)
    assert "more below the fold" not in everything


def test_board_check_records_observations_without_changing_state(tmp_path):
    db = tmp_path / "b.db"
    conn = core.connect(db)
    g = _gate(conn, decay_check="true")
    before = conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
    assert before == 0

    _board(db, "--check", "--yes")

    fresh = core.connect(db)
    after = fresh.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"]
    assert after == 1, "the check did not run — this test would pass vacuously"
    assert core.get_gate(fresh, g)["state"] == "open"


def test_the_board_states_that_reading_it_is_not_authority(tmp_path):
    """Invariant 3, on the surface most likely to be mistaken for a permission list."""
    db = tmp_path / "b.db"
    conn = core.connect(db)
    _gate(conn)
    assert "not authority to act" in _board(db)


def test_the_board_header_cannot_contradict_its_own_rows(tmp_path):
    """"longest wait" is computed from seconds, not from the rendered age.

    max("6m", "1h") is a string comparison answering "6m", so the header claimed
    a shorter longest-wait than a row it was printed above. Both gates are
    inserted with controlled timestamps because the bug only shows when the
    minutes value sorts above the hours value as text.
    """
    from datetime import datetime, timedelta, timezone

    db = tmp_path / "b.db"
    conn = core.connect(db)

    def at(minutes_ago: int, gid: str):
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
                 ).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO gates (id, raised_at, raised_by, question, kind, decay,"
            " consumer) VALUES (?,?,?,?,?,?,?)",
            (gid, stamp, "tester", f"raised {minutes_ago}m ago", "taste",
             "momentum", "tester: acts"))

    at(6, "gsixminutes0")
    at(60, "gonehour0000")
    conn.commit()

    out = _board(db)
    assert "longest wait 1h" in out, out.splitlines()[0]


def test_doctor_does_not_report_drift_on_gates_nobody_will_act_on(tmp_path):
    """A superseded gate drifting is noise, and it printed as if it were open.

    Drift matters where it can still mislead someone into acting: a gate still
    waiting, or one a human ruled on whose consumer may yet act. `doctor` listed
    every gate in the ledger, indented under the "open gates" heading, so a gate
    closed hours ago read as a live problem. A doctor that cries wolf stops
    being read.
    """
    db = tmp_path / "d.db"
    conn = core.connect(db)
    art = tmp_path / "a.txt"
    art.write_text("v1")

    stale = _gate(conn, question="closed and irrelevant",
                  binding=core.resolve_binding("file", str(art)))
    live = _gate(conn, question="still waiting",
                 binding=core.resolve_binding("file", str(art)))
    core.close_gate(conn, stale, state="superseded", reason="the world moved on",
                    actor="tester")
    art.write_text("v2")            # both bindings now drift

    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "doctor"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(tmp_path), "USER": "tester"},
    )
    assert r.returncode == 0, r.stderr
    assert live in r.stdout, "the open drifted gate must still be reported"
    assert stale not in r.stdout, r.stdout


def test_doctor_still_reports_drift_on_a_gate_a_human_ruled_on(tmp_path):
    """The counterpart: a ruling whose bytes moved is exactly what to shout about."""
    db = tmp_path / "d.db"
    conn = core.connect(db)
    art = tmp_path / "a.txt"
    art.write_text("v1")
    g = _gate(conn, binding=core.resolve_binding("file", str(art)))
    core.close_gate(conn, g, state="approved", reason="ship it", actor="kevin")
    art.write_text("v2")

    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "doctor"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(tmp_path), "USER": "tester"},
    )
    assert g in r.stdout and "approved" in r.stdout, r.stdout


# -------------------------------------------------- board: promised/delivered ---
def test_a_delivery_observation_requires_an_approved_ruling(conn, tmp_path):
    marker = tmp_path / "MUST_NOT_RUN"
    g = _gate(conn, delivery_check=f"touch {marker}")

    with pytest.raises(JanusError, match="only after an approved ruling"):
        core.observe(conn, g, "delivery", "tester")

    assert not marker.exists(), "the delivery command ran before approval"
    assert core.latest_observation(conn, g, "delivery") is None


def test_a_legacy_pre_ruling_delivery_observation_never_counts(conn):
    """Second-resolution timestamps cannot decide ordering on their own.

    Reproduce the old API's legal sequence in one second, including its audit
    event, then prove only the observation appended after approval is eligible.
    """
    g = _gate(conn, delivery_check="true")
    stamp = core.now()
    conn.execute(
        "INSERT INTO observations (gate_id, at, kind, command, exit_code, note)"
        " VALUES (?,?,?,?,?,?)", (g, stamp, "delivery", "true", 0, "legacy"))
    core.audit(conn, "legacy", "observe:delivery", g, "exit=0")
    conn.commit()
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")

    assert core.latest_delivery_observation(conn, g) is None

    core.observe(conn, g, "delivery", "tester")
    assert core.latest_delivery_observation(conn, g)["exit_code"] == 0


def test_delivery_status_uses_append_order_not_wall_clock(conn):
    """A clock jump must not let an older pass mask a later failure."""
    g = _gate(conn, delivery_check="false")
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    conn.execute(
        "INSERT INTO observations (gate_id, at, kind, command, exit_code, note)"
        " VALUES (?,?,?,?,?,?)",
        (g, "2099-01-01T00:00:00Z", "delivery", "false", 0, "future clock"),
    )
    core.audit(conn, "tester", "observe:delivery", g, "exit=0")
    conn.commit()

    core.observe(conn, g, "delivery", "tester")

    assert core.latest_observation(conn, g, "delivery")["exit_code"] == 0, \
        "the generic historical view still sorts its timestamps"
    assert core.latest_delivery_observation(conn, g)["exit_code"] == 1, \
        "derived delivery status must follow append order"


def test_a_delivery_result_is_bound_to_the_effective_check(tmp_path):
    """Correcting a bad check must not inherit the old check's success.

    The observation remains in append-only history, but board and scorecard
    status become unknown until the replacement command is actually run.
    """
    db = tmp_path / "revised-delivery.db"
    conn = core.connect(db)
    g = _gate(conn, delivery_check="true")
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    core.observe(conn, g, "delivery", "tester")
    assert core.latest_delivery_observation(conn, g)["exit_code"] == 0

    core.revise_check(conn, g, "delivery", "false", "tester",
                      "the old command did not measure the delivered effect")

    assert core.latest_observation(conn, g, "delivery")["exit_code"] == 0, \
        "the old fact must remain in history"
    assert core.latest_delivery_observation(conn, g) is None
    board = _board(db)
    assert g in board and "unchecked" in board
    acted = json.loads(_stats(db, "--json"))["consumer_acted"]
    assert acted["measurable"] == 1 and acted["confirmed"] == 0, acted

    core.observe(conn, g, "delivery", "tester")
    assert core.latest_delivery_observation(conn, g)["exit_code"] == 1


def test_an_approved_promise_that_has_not_landed_gets_its_own_heading(tmp_path):
    """ADR 0001: an approved resource gate is a promise, not a delivery.

    The decision has left the queue while the thing it promised may never have
    arrived, and until this section existed nothing in the fleet watched that gap.
    """
    db = tmp_path / "b.db"
    conn = core.connect(db)
    landed_file = tmp_path / "arrived.env"
    landed_file.write_text("token")

    waiting = _gate(conn, question="Mint the Athena admin token", kind="resource",
                    delivery_check=f"test -f {tmp_path / 'never.env'}")
    arrived = _gate(conn, question="Buy the NAS drives", kind="resource",
                    delivery_check=f"test -f {landed_file}")
    for g in (waiting, arrived):
        core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    core.observe(conn, waiting, "delivery", "tester")
    core.observe(conn, arrived, "delivery", "tester")

    out = _board(db)
    assert "PROMISED, NOT DELIVERED" in out
    assert waiting in out, out
    assert arrived not in out, "a promise that landed must drop off the board"


def test_an_unrun_delivery_check_never_reads_as_delivered(tmp_path):
    db = tmp_path / "b.db"
    conn = core.connect(db)
    g = _gate(conn, kind="resource", delivery_check="true")
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")

    out = _board(db)
    assert g in out and "unchecked" in out, out


def test_a_refused_gate_is_never_a_promise(tmp_path):
    """"I won't" is not a promise; it must not sit in the delivery queue."""
    db = tmp_path / "b.db"
    conn = core.connect(db)
    g = _gate(conn, kind="resource", delivery_check="false")
    core.close_gate(conn, g, state="refused", reason="not buying it", actor="kevin")

    out = _board(db)
    assert g not in out and "PROMISED" not in out, out


def test_any_approved_gate_with_no_delivery_check_is_counted_not_listed(tmp_path):
    """A row nothing can ever clear would train the reader to skip the section.

    It cannot be shown to have landed and it cannot be shown not to have, so it
    is counted in a sentence instead of parked in the list forever — but it is
    never silently dropped, because unknown is not the same as fine.
    """
    db = tmp_path / "b.db"
    conn = core.connect(db)
    g = _gate(conn, kind="authority")           # no delivery_check
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")

    out = _board(db)
    assert "1 approved gate(s) carry no delivery check" in out
    assert g not in out, "it must be counted, not listed as a clearable row"


def test_board_check_runs_delivery_checks_on_approved_gates(tmp_path):
    db = tmp_path / "b.db"
    conn = core.connect(db)
    g = _gate(conn, kind="resource", delivery_check="true")
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    assert conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"] == 0

    _board(db, "--check", "--yes")

    fresh = core.connect(db)
    obs = fresh.execute(
        "SELECT * FROM observations WHERE kind = 'delivery'").fetchall()
    assert len(obs) == 1, "the delivery check did not run — this would pass vacuously"
    assert obs[0]["exit_code"] == 0
    assert core.get_gate(fresh, g)["state"] == "approved"


def test_a_delivery_verdict_survives_being_pushed_past_the_observation_limit(tmp_path):
    """`get_gate` attaches only the last five observations, of any kind.

    Six decay checks push an older delivery result out of that window, and a
    status derived from the attached list would then report a promise that HAS
    landed as never checked — putting a delivered gate back on the board. The
    verdict comes from a query that cannot be truncated instead.
    """
    db = tmp_path / "b.db"
    conn = core.connect(db)
    g = _gate(conn, kind="resource", decay_check="true", delivery_check="true")
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    core.observe(conn, g, "delivery", "tester")          # it landed
    for _ in range(6):
        core.observe(conn, g, "decay", "tester")         # ...then buried

    assert all(o["kind"] == "decay" for o in core.get_gate(conn, g)["observations"]), \
        "the delivery row is still inside the window — this test would pass vacuously"
    assert core.latest_observation(conn, g, "delivery")["exit_code"] == 0
    assert g not in _board(db), "a delivered promise came back onto the board"


# ------------------------------------------- closing a gate is not always a ruling ---
def test_every_terminal_state_has_its_own_closing_sentence():
    """A sixth terminal state must fail here, not inherit a wrong sentence."""
    from janus import cli
    assert set(cli._CLOSING_NOTE) == set(core.TERMINAL_STATES)


def test_only_a_ruling_is_described_as_a_human_ruling():
    """Janus records that a HUMAN RULED, on which bytes, and when.

    `expired`, `withdrawn` and `superseded` are terminal because nobody ruled.
    Saying "a human ruled" over them is wrong on the one distinction the whole
    project exists to hold, and it shipped that way.
    """
    from janus import cli
    for state in core.TERMINAL_STATES:
        note = cli._CLOSING_NOTE[state]
        if state in core.RULED_STATES:
            assert "a human ruled" in note, state
        else:
            assert "NOBODY RULED" in note, state
            assert "human ruled" not in note, state


def test_superseding_a_gate_does_not_claim_anyone_ruled(tmp_path):
    """End to end, because the bug was in what the command actually printed."""
    db = tmp_path / "c.db"
    conn = core.connect(db)
    g = _gate(conn)
    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "supersede", g,
         "--reason", "the PR merged without it"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(tmp_path), "USER": "tester"},
    )
    assert r.returncode == 0, r.stderr
    assert "is now superseded" in r.stdout
    assert "human ruled" not in r.stdout, r.stdout
    assert "NOBODY RULED" in r.stdout, r.stdout


def test_approving_a_gate_still_says_a_human_ruled(tmp_path):
    """The counterpart, so the fix cannot be "delete the sentence everywhere"."""
    db = tmp_path / "c.db"
    conn = core.connect(db)
    g = _gate(conn)
    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "decide", g,
         "--approve", "--reason", "ship it"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(tmp_path), "USER": "tester"},
    )
    assert r.returncode == 0, r.stderr
    assert "This records that a human ruled" in r.stdout, r.stdout


# ------------------------------------------------------ M4: the scorecard ---
def _stats(db: Path, *argv):
    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "stats", *argv],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(db.parent), "USER": "tester"},
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _populated(tmp_path):
    conn = core.connect(tmp_path / "s.db")
    ruled = _gate(conn, question="ruled one")
    gone = _gate(conn, question="superseded one")
    _gate(conn, question="still open")
    core.close_gate(conn, ruled, state="approved", reason="yes", actor="kevin")
    core.close_gate(conn, gone, state="superseded", reason="moved on", actor="tester")
    return tmp_path / "s.db", conn


def test_a_supersede_is_never_counted_as_a_ruling(tmp_path):
    """The same distinction the closing sentence got wrong, now in a metric.

    A scorecard that counts "the world moved past it" as a decision reports a
    fleet that rules on everything. Half of this ledger's closures were
    supersedes; that is the finding, and averaging it away destroys it.
    """
    db, _ = _populated(tmp_path)
    d = json.loads(_stats(db, "--json"))
    assert d["closed"] == 2
    assert d["ruled"] == 1, d["closed_by_state"]
    assert d["closed_by_state"]["superseded"] == 1
    assert len(d["time_to_ruling_seconds"]) == 1, "a supersede leaked into time-to-ruling"


def test_the_scorecard_has_no_blank_measures(tmp_path):
    """M4's exit is 'a dated scorecard with no blank measures'.

    The easy way to avoid an embarrassing number is to omit the measure, which
    is the one thing this milestone forbids.
    """
    db, _ = _populated(tmp_path)
    out = _stats(db)
    for measure in ("RAISED", "CLOSED", "TIME TO RULING", "CONSUMER ACTED",
                    "decay check", "horizon", "CHECKS RUN"):
        line = next(ln for ln in out.splitlines() if measure in ln)
        assert any(ch.isdigit() for ch in line), f"{measure} printed no number: {line}"
    for blank in ("n/a", "None", "TBD", "unknown)"):
        assert blank not in out, blank


def test_every_rate_carries_its_denominator(tmp_path):
    """A percentage over n=2 is a lie with a decimal point."""
    db, _ = _populated(tmp_path)
    for line in _stats(db).splitlines():
        if "%" in line:
            assert " of " in line, f"bare percentage: {line}"


def test_the_scorecard_refuses_to_extrapolate_a_rate_it_cannot_measure(tmp_path):
    """A per-week figure over a minutes-old ledger is invention.

    Refused BY NAME rather than omitted, so the absence reads as a decision
    someone made instead of a measure someone forgot.
    """
    db, _ = _populated(tmp_path)
    out = _stats(db)
    line = next(ln for ln in out.splitlines() if "per week" in ln)
    assert "not reported" in line and "shorter than the week" in line


def test_a_gate_with_no_delivery_check_is_unknown_never_acted(tmp_path):
    db = tmp_path / "s.db"
    conn = core.connect(db)
    unknown = _gate(conn, kind="authority")             # approved, no signal
    core.close_gate(conn, unknown, state="approved", reason="yes", actor="kevin")
    landed = _gate(conn, kind="resource", delivery_check="true")
    core.close_gate(conn, landed, state="approved", reason="yes", actor="kevin")
    core.observe(conn, landed, "delivery", "tester")

    stale = _gate(conn, kind="resource", delivery_check="true")
    core.close_gate(conn, stale, state="superseded", reason="world moved",
                    actor="tester")

    d = json.loads(_stats(db, "--json"))["consumer_acted"]
    assert d == {"eligible": 2, "measurable": 1, "confirmed": 1, "unknown": 1}, d


def test_the_scorecard_on_an_empty_ledger_invents_nothing(tmp_path):
    out = _stats(tmp_path / "empty.db")
    assert "0 gates" in out
    assert "%" not in out, "a percentage was computed over an empty ledger"


# ------------------------------------- where the running code actually lives ---
def test_an_installed_copy_and_a_working_tree_are_told_apart(git_repo, tmp_path):
    """The fleet's `janus` was an editable install of a working tree.

    A reviewer's `git switch` in that tree silently changed the binary every
    other seat on the host was running. Reporting it is not the fix, but an
    undiagnosable hazard is worse than a diagnosed one.
    """
    from janus import cli
    repo, git = git_repo

    installed = cli._code_origin(
        tmp_path / "venv" / "lib" / "python3.12" / "site-packages" / "janus")
    assert installed["installed"] is True
    assert installed["branch"] is None, "an installed copy has no branch to report"

    live = cli._code_origin(repo)
    assert live["installed"] is False
    assert live["branch"] == "main", live
    assert live["dirty"] is False

    (repo / "scratch.txt").write_text("uncommitted")
    assert cli._code_origin(repo)["dirty"] is True


def test_doctor_says_where_its_code_came_from(tmp_path):
    db = tmp_path / "d.db"
    conn = core.connect(db)
    conn.close()
    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "doctor"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(tmp_path), "USER": "tester"},
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("code        "), r.stdout.splitlines()[:2]


# --------------------------------- the nudge at the moment it can be acted on ---
def _raise(tmp_path, *argv):
    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(tmp_path / "n.db"),
         "--seat", "tester", "raise", "Does this gate carry a check?",
         "--decay", "unclear", "--consumer", "tester: acts", *argv],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(tmp_path), "USER": "tester"},
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_raising_without_a_decay_check_says_so_while_it_can_still_be_fixed(tmp_path):
    """7 of the first 8 gates carried no decay check, so the board's whole sort
    ran on one data point — and every one was raised by an agent that had just
    read a skill telling it to add one. The habit was not being lost in the docs.
    """
    assert "no decay check" in _raise(tmp_path, "--kind", "taste")
    assert "no decay check" not in _raise(tmp_path, "--kind", "taste",
                                          "--decay-check", "true")


def test_every_gate_without_a_delivery_check_is_told_it_cannot_be_tracked(tmp_path):
    out = _raise(tmp_path, "--kind", "authority")
    assert "no delivery check" in out and "consumer outcome will remain unmeasured" in out
    assert "no delivery check" not in _raise(tmp_path, "--kind", "resource",
                                             "--delivery-check", "true")


# ------------------------------------- stored checks are executable text ------
def test_board_check_refuses_to_run_stored_commands_unattended(tmp_path):
    """THREAT_MODEL: a check "must be visible in full before it is invoked".

    A gate's check is text someone else wrote. The first build of --check ran
    every stored command without the operator seeing one, which broke the rule
    the threat model set before the code existed.
    """
    db = tmp_path / "b.db"
    conn = core.connect(db)
    _gate(conn, decay_check="touch " + str(tmp_path / "SHOULD_NOT_EXIST"))

    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "board", "--check"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(tmp_path), "USER": "tester", "COLUMNS": "110", "LINES": "40"},
    )
    assert r.returncode != 0, r.stdout
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists(), "it ran the command anyway"
    assert conn.execute("SELECT COUNT(*) c FROM observations").fetchone()["c"] == 0


def test_board_check_prints_each_command_before_running_it(tmp_path):
    db = tmp_path / "b.db"
    conn = core.connect(db)
    _gate(conn, decay_check="true # a distinctive marker")
    out = _board(db, "--check", "--yes")
    assert "about to run 1 command(s)" in out
    assert "a distinctive marker" in out, "the command was run without being shown"


def test_a_check_that_hangs_is_recorded_not_raised(tmp_path):
    """`TimeoutExpired` is not an `OSError`, so the handlers around observe()
    never caught it: a hanging check crashed the caller AND wrote nothing,
    leaving it indistinguishable from a check that was never run.
    """
    conn = core.connect(tmp_path / "h.db")
    g = _gate(conn, decay_check="sleep 5")
    result = core.observe(conn, g, "decay", "tester", timeout=1)
    assert result["exit_code"] == core.TIMEOUT_EXIT
    assert "killed, not answered" in result["output"]
    row = core.latest_observation(conn, g, "decay")
    assert row is not None and row["exit_code"] == core.TIMEOUT_EXIT


def test_a_broken_check_is_never_read_as_evidence_of_slack(tmp_path):
    """"not yet" is the one status meaning MEASURED, and there is time.

    A check that timed out or does not exist measured nothing. Ranking it as
    slack turns a broken check into evidence of safety, which is the most
    expensive way to be wrong on this board.
    """
    db = tmp_path / "b.db"
    conn = core.connect(db)
    missing = _gate(conn, question="check does not exist",
                    decay_check="janus-no-such-command-xyz")
    real = _gate(conn, question="check ran and there is time", decay_check="false")
    core.observe(conn, missing, "decay", "tester")
    core.observe(conn, real, "decay", "tester")
    assert core.latest_observation(conn, missing, "decay")["exit_code"] == 127

    out = _board(db)
    assert "broken" in out
    # and it must outrank the gate that proved it can wait
    assert out.index(missing) < out.index(real), out


# ---------------------------- the drift guard, which nothing was testing ------
def _cli(db: Path, *argv):
    return subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), *argv],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(db.parent), "USER": "tester"},
    )


def test_single_check_refuses_an_unattended_stored_command_before_execution(tmp_path):
    """The one-gate path has the same executable-text boundary as the board.

    The full command is deliberately longer than the normal board width. If it
    is clipped, hidden, or invoked before consent, this test fails on a real
    side effect and on the append-only observation record.
    """
    db = tmp_path / "single.db"
    conn = core.connect(db)
    marker = tmp_path / ("SHOULD_NOT_EXIST_" + "x" * 120)
    command = f"touch {marker}"
    g = _gate(conn, decay_check=command)

    r = _cli(db, "check", g)

    assert r.returncode != 0, r.stdout
    assert command in r.stdout + r.stderr, "the stored command was not shown in full"
    assert "--yes" in r.stdout + r.stderr
    assert not marker.exists(), "the command ran without explicit unattended consent"
    assert core.latest_observation(conn, g, "decay") is None


def test_single_check_yes_previews_then_records_the_effective_command(tmp_path):
    db = tmp_path / "single.db"
    conn = core.connect(db)
    original = tmp_path / "ORIGINAL_MUST_NOT_RUN"
    effective = tmp_path / "effective-ran"
    g = _gate(conn, decay_check=f"touch {original}")
    core.revise_check(
        conn,
        g,
        "decay",
        f"touch {effective}",
        "tester",
        "the original is a sentinel; this revision is the command under review",
    )

    r = _cli(db, "check", g, "--yes")

    assert r.returncode == 0, r.stderr
    assert f"touch {effective}" in r.stdout
    assert f"touch {original}" not in r.stdout
    assert effective.exists()
    assert not original.exists()
    observation = core.latest_observation(conn, g, "decay")
    assert observation is not None and observation["command"] == f"touch {effective}"


def test_single_check_escapes_terminal_controls_without_changing_execution(tmp_path):
    """A preview must not let stored text repaint what the operator consents to.

    Carriage return is enough to move the cursor back over the gate id, kind,
    and hidden command prefix in a real terminal.  The displayed form must be
    terminal-safe and unambiguous while the exact stored string still crosses
    the already-confirmed execution boundary.
    """
    db = tmp_path / "single.db"
    conn = core.connect(db)
    marker = tmp_path / "CONTROL_PREFIX_RAN"
    command = f"touch {marker} #\r\x1b[2K\u202e  echo benign-status-check"
    g = _gate(conn, decay_check=command)

    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(db), "check", g, "--yes"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "HOME": str(tmp_path),
            "USER": "tester",
        },
    )

    assert r.returncode == 0, r.stderr
    assert b"\r" not in r.stdout, "stored carriage return reached the terminal"
    assert b"\x1b" not in r.stdout, "stored ANSI escape reached the terminal"
    assert "\u202e".encode() not in r.stdout, "stored bidi override reached the terminal"
    assert ascii(command).encode() in r.stdout
    assert marker.exists()
    observation = core.latest_observation(conn, g, "decay")
    assert observation is not None and observation["command"] == command


def test_single_check_prints_the_command_before_calling_observe(conn, capsys, monkeypatch):
    from janus import cli

    command = "true # visible before the execution boundary"
    g = _gate(conn, decay_check=command)

    def observe_after_preview(*_args, **_kwargs):
        assert command in capsys.readouterr().out
        return {"exit_code": 0, "output": ""}

    monkeypatch.setattr(core, "observe", observe_after_preview)
    args = SimpleNamespace(gate_id=g, kind="decay", seat="tester", yes=True)

    assert cli.cmd_check(args, conn) == 0


def test_single_check_flushes_the_preview_before_calling_observe(conn, monkeypatch):
    from janus import cli

    command = "true # flush this before execution"
    g = _gate(conn, decay_check=command)
    events = []

    monkeypatch.setattr(sys.stdout, "flush", lambda: events.append("flush"))

    def observe_after_flush(*_args, **_kwargs):
        events.append("observe")
        return {"exit_code": 0, "output": ""}

    monkeypatch.setattr(core, "observe", observe_after_flush)
    args = SimpleNamespace(gate_id=g, kind="decay", seat="tester", yes=True)

    assert cli.cmd_check(args, conn) == 0
    assert events.index("flush") < events.index("observe")


def test_board_binds_execution_to_each_previewed_command(conn, monkeypatch):
    from janus import cli

    command = "true # board command identity"
    g = _gate(conn, decay_check=command)
    calls = []

    def observe_previewed(_conn, gate_id, kind, actor, *, expected_command=None):
        calls.append((gate_id, kind, actor, expected_command))
        return {"exit_code": 0, "output": ""}

    monkeypatch.setattr(core, "observe", observe_previewed)
    args = SimpleNamespace(check=True, all=True, yes=True, seat="tester")

    assert cli.cmd_board(args, conn) == 0
    assert len(calls) == 1
    assert calls[0][:2] == (g, "decay")
    assert calls[0][3] == command


def test_single_check_interactive_decline_runs_and_records_nothing(
    tmp_path, capsys, monkeypatch
):
    from janus import cli

    db = tmp_path / "single.db"
    conn = core.connect(db)
    marker = tmp_path / "DECLINED_MUST_NOT_RUN"
    command = f"touch {marker}"
    g = _gate(conn, delivery_check=command)
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    args = SimpleNamespace(gate_id=g, kind="delivery", seat="tester", yes=False)

    assert cli.cmd_check(args, conn) == 0

    out = capsys.readouterr().out
    assert command in out and "nothing run" in out
    assert not marker.exists()
    assert core.latest_observation(conn, g, "delivery") is None


def test_single_check_refuses_if_the_command_changes_after_preview(tmp_path, monkeypatch):
    """A revision visible at the final command load invalidates consent."""
    from janus import cli

    db = tmp_path / "single.db"
    conn = core.connect(db)
    previewed = tmp_path / "PREVIEWED_MUST_NOT_RUN"
    replacement = tmp_path / "REPLACEMENT_MUST_NOT_RUN"
    g = _gate(conn, decay_check=f"touch {previewed}")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def revise_then_accept(_prompt):
        other = core.connect(db)
        core.revise_check(
            other,
            g,
            "decay",
            f"touch {replacement}",
            "other-seat",
            "the command changed while the operator was reading the preview",
        )
        other.close()
        return "yes"

    monkeypatch.setattr("builtins.input", revise_then_accept)
    args = SimpleNamespace(gate_id=g, kind="decay", seat="tester", yes=False)

    with pytest.raises(JanusError, match="changed after preview"):
        cli.cmd_check(args, conn)

    assert not previewed.exists() and not replacement.exists()
    assert core.latest_observation(conn, g, "decay") is None


def test_revision_after_final_load_cannot_substitute_unseen_command(tmp_path, monkeypatch):
    """No database lock is needed when the displayed bytes are held locally."""
    db = tmp_path / "single.db"
    conn = core.connect(db)
    displayed = "true # displayed and held locally"
    unseen = "false # becomes effective only for the next run"
    g = _gate(conn, decay_check=displayed)

    def revise_after_load(command, **_kwargs):
        assert command == displayed
        other = core.connect(db)
        core.revise_check(
            other,
            g,
            "decay",
            unseen,
            "other-seat",
            "the revision committed after observe loaded the displayed bytes",
        )
        other.close()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(core.subprocess, "run", revise_after_load)

    result = core.observe(
        conn, g, "decay", "tester", expected_command=displayed
    )

    assert result["command"] == displayed and result["exit_code"] == 0
    assert core.latest_observation(conn, g, "decay")["command"] == displayed
    assert core.get_gate(conn, g)["effective_decay_check"] == unseen


def test_decide_refuses_to_rule_on_drifted_bytes_without_yes(tmp_path):
    """README and the skill both advertise this, and nothing covered it.

    Found by a mutation aimed somewhere else: disabling this guard left all 57
    tests green. It is the enforcement point of invariant 2 — a ruling approves
    the bytes in front of the human, not whatever the name points at later.
    """
    db = tmp_path / "d.db"
    conn = core.connect(db)
    art = tmp_path / "artifact.txt"
    art.write_text("what the human read")
    g = _gate(conn, binding=core.resolve_binding("file", str(art)))
    art.write_text("something else entirely")

    r = _cli(db, "decide", g, "--approve", "--reason", "looks fine")
    assert r.returncode != 0, r.stdout
    assert "drifted" in (r.stdout + r.stderr).lower()
    assert core.get_gate(core.connect(db), g)["state"] == "open", \
        "it ruled anyway — the gate is closed"


def test_decide_still_rules_on_drifted_bytes_when_told_to(tmp_path):
    """The guard is a speed bump for a human, not a lock. Janus never enforces."""
    db = tmp_path / "d.db"
    conn = core.connect(db)
    art = tmp_path / "artifact.txt"
    art.write_text("v1")
    g = _gate(conn, binding=core.resolve_binding("file", str(art)))
    art.write_text("v2")

    r = _cli(db, "decide", g, "--approve", "--reason", "I re-read it", "--yes")
    assert r.returncode == 0, r.stderr
    gate = core.get_gate(core.connect(db), g)
    assert gate["state"] == "approved"
    # and it records the bytes actually ruled on, not the ones raised against
    assert gate["ruling"]["bound_sha256"] == core.digest_file(art)


# ------------------------------------------- a check can be corrected (0002) ---
def test_a_revision_replaces_the_effective_check_without_touching_the_original(conn):
    g = _gate(conn, decay_check="test -n \"$SOME_AMBIENT_VAR\"")
    core.revise_check(conn, g, "decay", "test -s /etc/hostname", "tester",
                      "the old check read the environment of whoever ran the board, "
                      "not any durable fact")
    gate = core.get_gate(conn, g)
    assert gate["effective_decay_check"] == "test -s /etc/hostname"
    assert gate["decay_check"] == "test -n \"$SOME_AMBIENT_VAR\"", \
        "the original was rewritten — this is an edit, not a revision"
    assert len(gate["check_revisions"]) == 1
    assert gate["check_revisions"][0]["revised_by"] == "tester"


def test_observe_runs_the_revised_check_not_the_original(conn):
    """If `observe` kept reading the raw field the whole fix would be cosmetic."""
    g = _gate(conn, decay_check="false")
    assert core.observe(conn, g, "decay", "tester")["exit_code"] != 0
    core.revise_check(conn, g, "decay", "true", "tester",
                      "the original could never pass")
    assert core.observe(conn, g, "decay", "tester")["exit_code"] == 0


def test_a_check_can_be_corrected_on_a_gate_that_is_already_closed(conn):
    """The case that forced this: an APPROVED resource gate whose delivery check
    could never pass, so the board reported a delivered promise as outstanding
    forever. Correcting it must not require the gate to be open."""
    g = _gate(conn, kind="resource", delivery_check="test -n \"$NEVER_SET_HERE\"")
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")
    core.revise_check(conn, g, "delivery", "true", "tester",
                      "measured the ambient environment, not whether it landed")
    gate = core.get_gate(conn, g)
    assert gate["state"] == "approved", "a revision changed the gate's state"
    assert gate["ruling"]["reason"] == "yes", "a revision touched the ruling"
    assert gate["effective_delivery_check"] == "true"


def test_a_revision_demands_a_reason(conn):
    g = _gate(conn, decay_check="false")
    with pytest.raises(JanusError, match="reason"):
        core.revise_check(conn, g, "decay", "true", "tester", "   ")


def test_a_gate_with_no_check_can_gain_one(conn):
    g = _gate(conn)
    assert core.get_gate(conn, g)["effective_decay_check"] is None
    core.revise_check(conn, g, "decay", "true", "tester", "it never had one to begin with")
    assert core.get_gate(conn, g)["effective_decay_check"] == "true"


def test_correcting_a_check_clears_a_delivered_promise_off_the_board(tmp_path):
    """End to end, on the exact shape that forced migration 0002.

    An approved resource gate whose delivery check can never pass sits under
    PROMISED, NOT DELIVERED forever while the thing IS delivered. A board that
    lies once stops being read.
    """
    db = tmp_path / "b.db"
    conn = core.connect(db)
    landed = tmp_path / "token"
    landed.write_text("delivered")
    g = _gate(conn, kind="resource", question="did the credential arrive?",
              delivery_check="test -n \"$NOT_SET_ANYWHERE\"")
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")

    before = _board(db, "--check", "--yes")
    assert "PROMISED, NOT DELIVERED" in before and g in before

    core.revise_check(conn, g, "delivery", f"test -s {landed}", "tester",
                      "the original measured the ambient environment of whoever "
                      "ran the board, not whether the file exists")

    after = _board(db, "--check", "--yes")
    # NB: the gate id still appears in --check's "about to run" preamble, which
    # is the threat-model requirement that commands be visible before they run.
    # What must be gone is the gate's ROW under the heading.
    assert "PROMISED, NOT DELIVERED" not in after, after
    promised_section = after.split("PROMISED, NOT DELIVERED")[1:]
    assert not promised_section
    assert core.latest_observation(core.connect(db), g, "delivery")["exit_code"] == 0


def test_a_gate_that_gains_a_delivery_check_stops_being_counted_as_unwatchable(tmp_path):
    """An approved gate with no check is COUNTED, not listed — a row nothing can
    clear would sit there forever. Giving it a check by revision must move it
    into the tracked list, or the revision bought nothing on the surface that
    matters. Found by a surviving mutation: reading the raw field here behaves
    identically for every gate that already had a check.
    """
    db = tmp_path / "b.db"
    conn = core.connect(db)
    g = _gate(conn, kind="resource", question="did the credential arrive?")
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")

    before = _board(db)
    assert "1 approved gate(s) carry no delivery check" in before
    assert g not in before

    core.revise_check(conn, g, "delivery", "false", "tester",
                      "it shipped with no way to tell whether it landed")

    after = _board(db)
    assert "carry no delivery check" not in after, after
    assert g in after and "PROMISED, NOT DELIVERED" in after


def test_a_second_revision_reports_the_command_it_actually_replaced(tmp_path):
    """The receipt must name its real predecessor, not the immutable original.

    The gate's base check never changes, so reading it after a revision reports
    the wrong `was` from the second revision onward. Found by a non-author
    review (codex, PR #2) in the one command whose purpose is correcting a check
    that measured the wrong thing.
    """
    db = tmp_path / "r.db"
    conn = core.connect(db)
    g = _gate(conn, kind="resource", delivery_check="false")

    first = _cli(db, "--seat", "tester", "revise-check", g, "--kind", "delivery",
                 "--command", "true", "--reason", "the original could never pass")
    assert first.returncode == 0, first.stderr
    assert "now: true" in first.stdout and "was: false" in first.stdout

    second = _cli(db, "--seat", "tester", "revise-check", g, "--kind", "delivery",
                  "--command", "test -e /", "--reason", "narrower and honest")
    assert second.returncode == 0, second.stderr
    assert "now: test -e /" in second.stdout
    assert "was: true" in second.stdout, second.stdout
    assert "was: false" not in second.stdout, "it reported the immutable original"


# ------------- tri-state: "cannot verify" is not "fine" (CLI layer) ---------
# `verify_binding` is deliberately tri-state and its docstring already says None
# "must never read as fine". core-level test_unverifiable_binding_is_not_reported_as_fine
# proves the reader returns None. These prove the three places a HUMAN reads it
# do not flatten that None back into silence — which all three did.


def test_doctor_reports_a_binding_it_cannot_verify(tmp_path):
    """A binding Janus cannot read printed NO doctor line at all.

    `doctor` counted only `ok is False` (the artifact changed). A gate whose
    artifact is missing, unreadable, or bound to a relative path from another
    process's cwd returned None and fell through both branches, so its output
    was byte-identical to a gate that verified clean. The live instance:
    g3410c2cff0f, open four days, reading CANNOT VERIFY in `show` while
    `doctor` said nothing about it.
    """
    db = tmp_path / "d.db"
    conn = core.connect(db)
    art = tmp_path / "gone.md"
    art.write_text("v1")
    unreadable = _gate(conn, question="bound to something that vanished",
                       binding=core.resolve_binding("file", str(art)))
    clean_art = tmp_path / "here.md"
    clean_art.write_text("still here")
    clean = _gate(conn, question="bound to something readable",
                  binding=core.resolve_binding("file", str(clean_art)))
    art.unlink()

    r = _cli(db, "doctor")
    assert r.returncode == 0, r.stderr
    assert unreadable in r.stdout, "an unverifiable binding must be named"
    assert "unverifiable" in r.stdout, r.stdout
    # and it must not be silently upgraded into the drift count, which means
    # something different and actionable
    assert "bound artifact no longer matches" not in r.stdout, r.stdout
    # a readable binding still says nothing: this is a report of problems only
    assert f"unverifiable {clean}" not in r.stdout, r.stdout


def test_doctor_reports_a_legacy_bound_ruling_with_no_digest(tmp_path):
    """Readable raised bytes must not hide an invalid historical ruling."""
    db = tmp_path / "d.db"
    conn = core.connect(db)
    art = tmp_path / "still-readable.md"
    art.write_text("v1")
    g = _gate(conn, question="legacy digestless ruling",
              binding=core.resolve_binding("file", str(art)))
    conn.execute("DROP TRIGGER ruling_bound_gate_requires_digest")
    conn.execute(
        "INSERT INTO rulings "
        "(gate_id, state, ruled_at, ruled_by, reason, bound_sha256) "
        "VALUES (?,?,?,?,?,?)",
        (g, "approved", core.now(), "kevin", "legacy ruling", None),
    )
    conn.commit()

    r = _cli(db, "doctor")
    assert r.returncode == 1, r.stdout
    assert f"integrity   {g} (approved)" in r.stdout, r.stdout
    assert "bound ruling has no ruling-time digest" in r.stdout, r.stdout

    shown = _cli(db, "show", g).stdout
    assert "binding matches: the bound bytes are the live bytes" in shown, shown
    assert "binding matches: the ruled bytes are the live bytes" not in shown, shown
    assert "ruled on bytes: NONE RECORDED" in shown, shown


def test_doctor_does_not_call_inline_text_unverifiable(tmp_path):
    """Inline text is self-contained; there is no external read to retry."""
    db = tmp_path / "d.db"
    conn = core.connect(db)
    g = _gate(conn, question="inline binding",
              binding=core.resolve_binding("text", "the exact proposal"))
    core.close_gate(conn, g, state="approved", reason="these bytes", actor="kevin")

    r = _cli(db, "doctor")
    assert r.returncode == 0, r.stdout
    assert f"unverifiable {g}" not in r.stdout, r.stdout


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="requires an unprivileged POSIX process")
def test_permission_denied_binding_is_a_structured_refusal(tmp_path):
    """Unreadable means CANNOT VERIFY, never a raw filesystem traceback."""
    db = tmp_path / "d.db"
    conn = core.connect(db)
    art = tmp_path / "permission-denied.md"
    art.write_text("v1")
    g = _gate(conn, question="binding loses read permission",
              binding=core.resolve_binding("file", str(art)))
    art.chmod(0)
    try:
        r = _cli(db, "decide", g, "--approve", "--reason", "ruling anyway")
    finally:
        art.chmod(0o600)

    assert r.returncode != 0, r.stdout
    assert "CANNOT VERIFY" in r.stderr, r.stderr
    assert "Permission denied" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert core.get_gate(core.connect(db), g)["state"] == "open"


@pytest.mark.parametrize("decision", ("--approve", "--refuse"))
def test_ruling_on_an_unverifiable_binding_is_refused(tmp_path, decision):
    """A missing artifact cannot produce a re-checkable bound ruling."""
    db = tmp_path / "d.db"
    conn = core.connect(db)
    art = tmp_path / "gone.md"
    art.write_text("v1")
    g = _gate(conn, question="rule me with no readable artifact",
              binding=core.resolve_binding("file", str(art)))
    conn.commit()
    art.unlink()

    r = _cli(db, "decide", g, decision, "--reason", "ruling anyway")
    assert r.returncode != 0, r.stdout
    assert "CANNOT VERIFY" in r.stderr, r.stderr
    assert "A ruling must record the bytes ruled on" in r.stderr, r.stderr
    gate = core.get_gate(core.connect(db), g)
    assert gate["state"] == "open"
    assert gate["ruling"] is None


def test_inline_text_binding_can_still_be_ruled_on(tmp_path):
    """Inline text has no external artifact that can disappear between reads."""
    db = tmp_path / "d.db"
    conn = core.connect(db)
    g = _gate(conn, question="rule on these inline bytes",
              binding=core.resolve_binding("text", "the exact proposal"))

    r = _cli(db, "decide", g, "--approve", "--reason", "these bytes are right")
    assert r.returncode == 0, r.stderr
    gate = core.get_gate(core.connect(db), g)
    assert gate["state"] == "approved"
    assert gate["ruling"]["bound_sha256"] == gate["binding_sha256"]


def test_show_says_when_a_ruling_bound_nothing(tmp_path):
    """Legacy invalid rows stay visible after new invalid writes are refused.

    So a ruling that bound nothing rendered identically to one on a gate that
    never had a binding at all — absent reading as fine, one layer further on.
    """
    db = tmp_path / "d.db"
    conn = core.connect(db)
    art = tmp_path / "gone.md"
    art.write_text("v1")
    g = _gate(conn, question="ruled while unreadable",
              binding=core.resolve_binding("file", str(art)))
    art.unlink()
    # Simulate a row written before migration 0003 installed the database-level
    # guard. Append-only keeps such history; the reader must state it plainly.
    conn.execute("DROP TRIGGER ruling_bound_gate_requires_digest")
    conn.execute(
        "INSERT INTO rulings "
        "(gate_id, state, ruled_at, ruled_by, reason, bound_sha256) "
        "VALUES (?,?,?,?,?,?)",
        (g, "approved", core.now(), "kevin", "legacy ruling", None),
    )
    conn.commit()

    out = _cli(db, "show", g).stdout
    assert "NONE RECORDED" in out, out
    assert "could not read the artifact" in out, out
