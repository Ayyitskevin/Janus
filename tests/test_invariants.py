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

import json
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


def test_an_approved_resource_gate_with_no_check_is_counted_not_listed(tmp_path):
    """A row nothing can ever clear would train the reader to skip the section.

    It cannot be shown to have landed and it cannot be shown not to have, so it
    is counted in a sentence instead of parked in the list forever — but it is
    never silently dropped, because unknown is not the same as fine.
    """
    db = tmp_path / "b.db"
    conn = core.connect(db)
    g = _gate(conn, kind="resource")            # no delivery_check
    core.close_gate(conn, g, state="approved", reason="yes", actor="kevin")

    out = _board(db)
    assert "1 approved resource gate(s) carry no delivery check" in out
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
    _gate(conn, kind="resource")                       # no signal at all
    landed = _gate(conn, kind="resource", delivery_check="true")
    core.close_gate(conn, landed, state="approved", reason="yes", actor="kevin")
    core.observe(conn, landed, "delivery", "tester")

    d = json.loads(_stats(db, "--json"))["consumer_acted"]
    assert d == {"measurable": 1, "confirmed": 1, "unknown": 1}, d


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
    r = subprocess.run(
        [sys.executable, "-m", "janus.cli", "--db", str(tmp_path / "d.db"), "doctor"],
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


def test_a_resource_gate_without_a_delivery_check_is_told_it_cannot_be_tracked(tmp_path):
    out = _raise(tmp_path, "--kind", "resource")
    assert "no delivery check" in out and "promise, not a delivery" in out
    assert "no delivery check" not in _raise(tmp_path, "--kind", "resource",
                                             "--delivery-check", "true")
    # Only resource gates promise a thing; the others must not be nagged.
    assert "no delivery check" not in _raise(tmp_path, "--kind", "taste")


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
    assert "1 approved resource gate(s) carry no delivery check" in before
    assert g not in before

    core.revise_check(conn, g, "delivery", "false", "tester",
                      "it shipped with no way to tell whether it landed")

    after = _board(db)
    assert "carry no delivery check" not in after, after
    assert g in after and "PROMISED, NOT DELIVERED" in after
