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

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

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
@pytest.mark.parametrize("table", ["gates", "rulings", "audit_events", "observations"])
def test_update_and_delete_are_refused_on_real_rows(conn, table):
    g = _gate(conn)
    core.close_gate(conn, g, state="approved", reason="ok", actor="kevin")
    core.audit(conn, "tester", "probe", g, "x")
    conn.execute(
        "INSERT INTO observations (gate_id, at, kind, command, exit_code)"
        " VALUES (?,?,?,?,?)", (g, core.now(), "decay", "true", 0))
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
    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(tmp_path / "d.db"), "doctor"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "HOME": str(tmp_path), "USER": "tester"},
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

    _board(db, "--check")

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
