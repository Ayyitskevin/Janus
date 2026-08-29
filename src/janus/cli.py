"""janus — the CLI. Every gate, both faces.

Design note: refusals are sentences, not tracebacks. This tool exists to be
reached for by a tired human at 23:00 and by an agent mid-task; both need to be
told what is wrong and what to do instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from . import core
from . import export as stable_export
from .core import JanusError

KIND_HELP = (
    "why a human is needed: irreversible (cannot be undone) | authority (only "
    "the owner may rule) | taste (design call with no dominant answer) | "
    "resource (only the human can supply it)"
)


def _fmt_gate_line(g: dict) -> str:
    mark = {"open": "○", "approved": "✔", "refused": "✘", "expired": "⌛",
            "withdrawn": "↩", "superseded": "⇒"}.get(g["state"], "?")
    opts = f" [{len(g['options'])} options]" if g["options"] else ""
    return f"{mark} {g['id']}  {g['kind']:<12} {g['question'][:66]}{opts}"


def cmd_raise(a, conn) -> int:
    actor = core.seat_actor(a.seat)
    binding = core.resolve_binding(a.bind_kind, a.bind)
    options = []
    for i, raw in enumerate(a.option or []):
        # "id:label" or "id:label:detail", with a trailing '*' on id to recommend
        parts = raw.split(":", 2)
        if len(parts) < 2:
            raise JanusError(f"--option must be 'id:label[:detail]', got {raw!r}")
        oid = parts[0].strip()
        rec = oid.endswith("*")
        options.append({
            "id": oid.rstrip("*"),
            "label": parts[1].strip(),
            "detail": parts[2].strip() if len(parts) > 2 else None,
            "recommended": rec,
        })
    if options and not any(o["recommended"] for o in options):
        raise JanusError(
            "this gate offers options but recommends none — mark one with a "
            "trailing '*' on its id. A queue that offers choices without a "
            "recommendation just moves the work to the operator."
        )
    gid = core.raise_gate(
        conn, question=a.question, kind=a.kind, decay=a.decay, consumer=a.consumer,
        actor=actor, decay_check=a.decay_check, horizon=a.horizon,
        delivery_check=a.delivery_check, binding=binding, options=options,
        cites=a.cites,
    )
    print(f"raised {gid}  ({a.kind}, by {actor})")
    if binding:
        print(f"  bound to {binding.kind}:{binding.locator} @ {binding.sha256[:12]}")
    print(f"  consumer: {a.consumer}")

    # Said at the one moment it can still be acted on. The scorecard's worst
    # number is that 7 of 8 gates carry no decay check, which makes the board's
    # entire sort run on one data point — and every one of those gates was
    # raised by an agent that had just read a skill telling it to add one.
    # Documentation was not where that habit was being lost.
    if not a.decay_check:
        print("  no decay check — the board will print this gate as 'unmeasured' and "
              "sort it\n              below every measured one. "
              "--decay-check '<command>' (exit 0 = the cost arrived).")
    if a.kind == "resource" and not a.delivery_check:
        print("  no delivery check — an approved resource gate is a promise, not a "
              "delivery.\n              Without one, nothing can tell you whether it "
              "ever landed.")
    return 0


def cmd_list(a, conn) -> int:
    gates = core.list_gates(conn, state=a.state)
    if a.json:
        print(json.dumps(gates, indent=2, default=str))
        return 0
    if not gates:
        print(f"no {a.state} gates")
        return 0
    print(f"{len(gates)} {a.state} gate(s)\n")
    for g in gates:
        print(_fmt_gate_line(g))
        print(f"    waiting on: {g['consumer']}   raised {g['raised_at'][:10]} by {g['raised_by']}")
        if g["horizon"]:
            print(f"    horizon: {g['horizon']}")
    return 0


def cmd_show(a, conn) -> int:
    g = core.get_gate(conn, a.gate_id)
    if g is None:
        raise JanusError(f"no such gate: {a.gate_id}")
    if a.json:
        print(json.dumps(g, indent=2, default=str))
        return 0
    print(f"{g['id']}  [{g['state']}]  kind={g['kind']}")
    print(f"\n  {g['question']}\n")
    print(f"  raised    {g['raised_at']} by {g['raised_by']}")
    print(f"  consumer  {g['consumer']}")
    print(f"  decay     {g['decay']}")
    if g["effective_decay_check"]:
        print(f"            check: {g['effective_decay_check']}")
    if g["horizon"]:
        print(f"  horizon   {g['horizon']}")
    if g["effective_delivery_check"]:
        print(f"  delivery  check: {g['effective_delivery_check']}")
    for r in g["check_revisions"]:
        print(f"  revised   {r['kind']} check on {r['at']} by {r['revised_by']}")
        print(f"            was measuring the wrong thing: {r['reason']}")
    if g["cites"]:
        print(f"  cites     {g['cites']}")
    if g["options"]:
        print("\n  options:")
        for o in g["options"]:
            star = " (recommended)" if o["recommended"] else ""
            print(f"    {o['option_id']}: {o['label']}{star}")
            if o["detail"]:
                print(f"        {o['detail']}")
    if g["binding_sha256"]:
        print(f"\n  binding   {g['binding_kind']}:{g['binding_locator']}")
        print(f"            raised @ {g['binding_sha256'][:16]}")
        ok, sentence = core.verify_binding(
            g["binding_kind"], g["binding_locator"], g["binding_sha256"])
        print(f"            {sentence}")
    if g["ruling"]:
        r = g["ruling"]
        print(f"\n  RULED     {r['state']} by {r['ruled_by']} at {r['ruled_at']}")
        print(f"            reason: {r['reason']}")
        if r["option_id"]:
            print(f"            chose:  {r['option_id']}")
        if r["bound_sha256"]:
            print(f"            ruled on bytes @ {r['bound_sha256'][:16]}")
            if g["binding_sha256"] and r["bound_sha256"] != g["binding_sha256"]:
                print("            note: artifact had already changed between "
                      "raise and ruling")
        print("\n  Reading this ruling is not authority. Re-verify the digest "
              "against the live artifact before acting.")
    if g["observations"]:
        print("\n  recent observations:")
        for o in g["observations"]:
            verdict = "occurred/landed" if o["exit_code"] == 0 else "not yet"
            print(f"    {o['at']} {o['kind']}: exit={o['exit_code']} ({verdict})")
    return 0


def cmd_export(a, _conn=None) -> int:
    """Emit the stable interchange artifact without opening a writable ledger."""
    sys.stdout.buffer.write(stable_export.export_gates(a.db, a.gate_id))
    sys.stdout.buffer.write(b"\n")
    return 0


# Only `approved` and `refused` are rulings. The other three are terminal because
# NOBODY ruled — the corpus that forced `superseded` says so outright — and the
# close path printed "This records that a human ruled" over all five. That is
# wrong on the exact distinction this project exists to hold: Janus records that
# a human ruled, on which bytes, and when. It shipped that way, and the first
# supersede in the live ledger printed it about a gate no human had answered.
#
# Keyed by state and asserted against TERMINAL_STATES in the tests, so a sixth
# terminal state fails a test instead of quietly inheriting the wrong sentence.
_CLOSING_NOTE = {
    "approved": "This records that a human ruled. It grants nothing: the consumer "
                "re-verifies before acting.",
    "refused": "This records that a human ruled. It grants nothing: the consumer "
               "re-verifies before acting.",
    "withdrawn": "NOBODY RULED — the raiser took the question back. Nothing was decided "
                 "here, so nothing here can be cited as a decision.",
    "expired": "NOBODY RULED — time ran out. A closed gate is not an answer; if the "
               "question still matters it has to be raised again.",
    "superseded": "NOBODY RULED — the world moved past the question. The reason records "
                  "WHAT overtook it, never that anything was decided.",
}


def _close(a, conn, state: str, reason: str) -> int:
    actor = core.seat_actor(a.seat)
    g = core.get_gate(conn, a.gate_id)
    if g is None:
        raise JanusError(f"no such gate: {a.gate_id}")
    # Warn BEFORE writing when the bytes drifted — the human should know what
    # they are actually ruling on.
    if state in core.RULED_STATES and g["binding_sha256"]:
        ok, sentence = core.verify_binding(
            g["binding_kind"], g["binding_locator"], g["binding_sha256"])
        if ok is False:
            print(f"warning: {sentence}", file=sys.stderr)
            if not a.yes:
                raise JanusError(
                    "refusing to rule on drifted bytes without --yes. The gate "
                    "was raised against different content; confirm you have "
                    "reviewed what is there now."
                )
    g = core.close_gate(conn, a.gate_id, state=state, reason=reason, actor=actor,
                        option_id=getattr(a, "option", None))
    print(f"{a.gate_id} is now {g['state']} (by {actor})")
    if g["ruling"] and g["ruling"]["bound_sha256"]:
        print(f"  ruled on bytes @ {g['ruling']['bound_sha256'][:16]}")
    print("  " + _CLOSING_NOTE[g["state"]])
    return 0


def cmd_decide(a, conn) -> int:
    return _close(a, conn, "approved" if a.approve else "refused", a.reason)


def cmd_withdraw(a, conn) -> int:
    return _close(a, conn, "withdrawn", a.reason)


def cmd_expire(a, conn) -> int:
    return _close(a, conn, "expired", a.reason)


def cmd_supersede(a, conn) -> int:
    return _close(a, conn, "superseded", a.reason)


def _effective_command(gate: dict, kind: str) -> str | None:
    return gate["effective_decay_check" if kind == "decay" else "effective_delivery_check"]


def _preview_and_confirm_checks(pending: list[tuple[dict, str]], *, yes: bool) -> bool:
    """Display executable ledger text in full, then obtain consent to run it."""
    print(f"about to run {len(pending)} command(s) stored in the ledger:")
    for gate, kind in pending:
        # Stored text may contain carriage returns, ANSI escapes, bidi marks,
        # or other terminal controls.  ascii() is reversible for the Python
        # string and emits only inert ASCII, including escaped backslashes, so
        # the preview cannot repaint a benign command over a hidden prefix.
        print(f"  {gate['id']}  {kind:<8}  {ascii(_effective_command(gate, kind))}")
    # In a terminal, newlines are normally enough. An unattended caller may be
    # streaming stdout, though, so make the preview observable before crossing
    # the execution boundary rather than merely ordering two buffered writes.
    sys.stdout.flush()
    if not yes:
        if not sys.stdin.isatty():
            raise JanusError(
                "refusing to run stored commands unattended. Re-run with --yes "
                "once you have read the list above; a gate's check is text "
                "someone else wrote."
            )
        noun = "it" if len(pending) == 1 else "them"
        if input(f"run {noun}? [y/N] ").strip().lower() not in ("y", "yes"):
            print("nothing run.")
            return False
    print()
    return True


def cmd_check(a, conn) -> int:
    gate = core.get_gate(conn, a.gate_id)
    if gate is None:
        raise JanusError(f"no such gate: {a.gate_id}")
    command = _effective_command(gate, a.kind)
    if not command:
        raise JanusError(f"gate {a.gate_id} carries no {a.kind} check")
    if not _preview_and_confirm_checks([(gate, a.kind)], yes=a.yes):
        return 0
    result = core.observe(
        conn,
        a.gate_id,
        a.kind,
        core.seat_actor(a.seat),
        expected_command=command,
    )
    verdict = "occurred/landed" if result["exit_code"] == 0 else "not yet"
    print(f"{a.kind} check: exit={result['exit_code']} ({verdict})")
    if result["output"]:
        print(f"  {result['output'].strip()[:180]}")
    print("  Recorded as an observation. Observations never change a gate's state.")
    return 0


# ---------------------------------------------------------------- board ----
# M2. The one screen answering: what is waiting, how long, and what worsens.
#
# ADR 0001: "A board sorted by observed decay is sorted by risk of loss." Every
# term in the sort is therefore something OBSERVED — a decay check that fired,
# a horizon the clock has passed, how long the gate has actually waited. None of
# it is a priority field, which this project rejects at every maturity level.

# Rank by what is KNOWN, and note that evidence of slack demotes while absence
# of evidence does not promote. "not yet" is the only row with a measurement
# saying there is time; unmeasured is unknown, and unknown is not the same as
# fine — so it sorts above the gate that proved it can wait.
_DECAY_RANK = {"landed": 0, "broken": 1, "unchecked": 1, "unmeasured": 1, "not yet": 2}

# A check that timed out or could not be found did not measure anything. Both
# would otherwise land in "not yet", the ONE tier that means "measured, and
# there is still slack" — turning a broken check into evidence of safety, which
# is the most expensive possible way to be wrong here. 127 is the shell's
# "command not found"; 124 is core.TIMEOUT_EXIT.
_BROKEN_EXITS = (core.TIMEOUT_EXIT, 127)


def _decay_status(conn, g: dict) -> str:
    """What is actually known about this gate's decay — never a guess.

    `unmeasured` and `unchecked` are printed rather than left blank on purpose.
    A decay sentence with no re-runnable check is a claim, and a board that
    renders a claim identically to an observation flatters the prose.
    """
    obs = core.latest_observation(conn, g["id"], "decay")
    if obs:
        if obs["exit_code"] == 0:
            return "landed"
        return "broken" if obs["exit_code"] in _BROKEN_EXITS else "not yet"
    return "unchecked" if g["effective_decay_check"] else "unmeasured"


def _delivery_status(conn, g: dict) -> str:
    """Whether an approved promise has actually landed.

    ADR 0001: "an `approved` resource gate is a promise, not a delivery, and the
    consumer still has to check that the thing arrived." A ruling closes the
    DECISION; it does not make a token exist. `unchecked` is therefore a real
    answer and must not be rendered as delivered.
    """
    obs = core.latest_observation(conn, g["id"], "delivery")
    if obs:
        if obs["exit_code"] == 0:
            return "delivered"
        return "broken" if obs["exit_code"] in _BROKEN_EXITS else "not landed"
    return "unchecked"


def _age(iso: str) -> tuple[int, str]:
    t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    secs = max(int((datetime.now(timezone.utc) - t).total_seconds()), 0)
    if secs < 3600:
        return secs, f"{secs // 60}m"
    if secs < 86400:
        return secs, f"{secs // 3600}h"
    return secs, f"{secs // 86400}d"


def _overdue(g: dict) -> bool:
    """A horizon is the raiser's own stated deadline; the clock observes it."""
    if not g["horizon"]:
        return False
    return g["horizon"][:10] < core.now()[:10]


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def cmd_board(a, conn) -> int:
    seat = core.seat_actor(a.seat)

    def run(gate, kind):
        try:
            core.observe(
                conn,
                gate["id"],
                kind,
                seat,
                expected_command=_effective_command(gate, kind),
            )
        except (JanusError, OSError) as e:
            print(f"{kind} check failed to run for {gate['id']}: {e}", file=sys.stderr)

    if a.check:
        # On demand, never on a timer (M2). An observation records an exit
        # status; it never changes a gate's state — which is exactly why it is
        # safe to run these against gates that are already closed.
        #
        # THREAT_MODEL: "A gate that ships a `check` ships executable text...
        # Checks must never run automatically on write, never run as part of
        # listing gates, and must be visible in full before they are invoked."
        # The first build of this flag broke the third clause: it ran every
        # stored command without the operator ever seeing one. They are printed
        # in full first now, and a non-interactive caller must pass --yes, so
        # nothing here executes text nobody chose to run.
        pending = [(g, "decay") for g in core.list_gates(conn, state="open")
                   if g["effective_decay_check"]]
        pending += [(g, "delivery") for g in core.list_gates(conn, state="approved")
                    if g["effective_delivery_check"]]
        if not pending:
            print("no checks to run — no open gate carries one.\n")
        else:
            if not _preview_and_confirm_checks(pending, yes=a.yes):
                return 0
            for g, kind in pending:
                run(g, kind)

    rows = []
    for g in core.list_gates(conn, state="open"):
        secs, age = _age(g["raised_at"])
        rows.append({"g": g, "secs": secs, "age": age,
                     "status": _decay_status(conn, g), "overdue": _overdue(g)})
    rows.sort(key=lambda r: (_DECAY_RANK[r["status"]], 0 if r["overdue"] else 1, -r["secs"]))

    # ADR 0001 promised this section and the first build of the board did not
    # ship it: "The board surfaces approved resource gates whose delivery check
    # still fails, under their own heading." An approved gate has left the
    # decision queue while the thing it promised may never have arrived, and
    # nothing else in the fleet was watching that gap.
    promised, unwatched = [], 0
    for g in core.list_gates(conn, state="approved"):
        if not g["effective_delivery_check"]:
            # A resource gate approved without a check cannot ever be shown to
            # have landed. Counted, not listed: a row nothing can clear would sit
            # there forever and train the reader to skip the section.
            unwatched += 1 if g["kind"] == "resource" else 0
            continue
        status = _delivery_status(conn, g)
        if status == "delivered":
            continue
        secs, age = _age(g["ruling"]["ruled_at"])
        promised.append({"g": g, "secs": secs, "age": age, "status": status})
    promised.sort(key=lambda r: (r["status"] != "not landed", -r["secs"]))

    if not rows and not promised:
        print("Nothing is waiting on a human." if not unwatched else
              "No decision is waiting on a human.")
        if unwatched:
            print(f"\n  {unwatched} approved resource gate(s) carry no delivery check —"
                  " nobody can tell whether they landed.")
        return 0

    term = shutil.get_terminal_size(fallback=(100, 24))
    width = min(max(term.columns, 80), 120)
    head = 2 + 10 + 2 + 5 + 2 + 12 + 2 + 12 + 2      # indent + the five columns
    body = width - head

    if rows:
        landed = sum(1 for r in rows if r["status"] == "landed")
        unmeasured = sum(1 for r in rows if r["status"] in ("unmeasured", "unchecked"))
        # Longest wait comes from the SECONDS, not the rendered age. Taking max() of
        # "6m" and "1h" is a string comparison that answers "6m", which is how the
        # first build of this line shipped a header that contradicted its own rows.
        longest = max(rows, key=lambda r: r["secs"])["age"]
        summary = f"{len(rows)} waiting on a human — longest wait {longest}"
        if landed:
            summary += f" · {landed} with decay observed to have landed"
        if unmeasured:
            summary += f" · {unmeasured} whose decay has never been checked"
    else:
        summary = "No decision is waiting on a human."
    print(summary + "\n")

    # One screen or it is wrong (ROADMAP M2). When it does not fit, say so —
    # a board that silently drops rows is exactly the surface it replaces. The
    # two sections share the budget; promises take at most half so a long
    # decision queue can never hide them entirely, or the reverse.
    lines = max(term.lines, 12) - 8
    promised_lines = min(len(promised) * 2 + 2, max(lines // 2, 4)) if promised else 0
    shown = len(rows) if a.all else max((lines - promised_lines) // 2, 1)
    for r in rows[:shown]:
        g = r["g"]
        age = r["age"] + ("!" if r["overdue"] else "")
        print(f"  {r['status']:<10}  {age:<5}  {g['id']:<12}  {g['kind']:<12}  "
              f"{_clip(g['question'], body)}")
        print(f"  {'':<10}  {'':<5}  {'':<12}  {'worsens →':<12}  "
              f"{_clip(g['decay'], body)}")

    hidden = len(rows) - shown
    if hidden > 0:
        print(f"\n  {hidden} more below the fold. The queue no longer fits one screen,")
        print("  which is the finding, not a display bug. `janus board --all` shows every one.")

    if promised:
        shown_p = len(promised) if a.all else max((promised_lines - 2) // 2, 1)
        print(f"\n  PROMISED, NOT DELIVERED — {len(promised)} approved, still waiting to land")
        for r in promised[:shown_p]:
            g = r["g"]
            print(f"  {r['status']:<10}  {r['age']:<5}  {g['id']:<12}  {g['kind']:<12}  "
                  f"{_clip(g['question'], body)}")
            print(f"  {'':<10}  {'':<5}  {'':<12}  {'check →':<12}  "
                  f"{_clip(g['effective_delivery_check'], body)}")
        hidden_p = len(promised) - shown_p
        if hidden_p > 0:
            print(f"\n  {hidden_p} more promise(s) below the fold — `janus board --all`.")

    if unwatched:
        print(f"\n  {unwatched} approved resource gate(s) carry no delivery check —"
              " nobody can tell whether they landed.")

    print(textwrap.dedent("""
        Sorted by observed decay, then a passed horizon (!), then the longest wait.
        'unmeasured' means the decay sentence carries no re-runnable check — unknown,
        which is not the same as fine. `janus board --check` runs the checks that exist.
        A ruling closes a decision; it does not make the promised thing exist, so an
        approved gate stays under PROMISED until its own check says it landed.
        `janus show <id>` for the full question and its options.
        Reading this board is not authority to act.""").rstrip())
    return 0


# ---------------------------------------------------------------- stats ----
# M4. "Publish the numbers even when they are bad. A pillar nobody uses is a
# finding." The exit is a dated scorecard with NO BLANK MEASURES, which is the
# whole difficulty: the easy way to avoid an embarrassing number is to leave the
# measure out, and that is the one thing this milestone forbids.
#
# Three rules hold the scorecard honest, and each has a test:
#   - every measure prints a number, including zero;
#   - every rate prints its denominator, because a percentage over n=2 is a lie
#     with a decimal point;
#   - nothing is extrapolated. A per-week rate over a 54-minute ledger is
#     invention, so the window is stated and the rate is refused by name.


def _fmt_duration(secs: int) -> str:
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _pct(part: int, whole: int) -> str:
    """A share always carries its denominator. Never a bare percentage."""
    if whole == 0:
        return f"{part} of 0"
    return f"{part} of {whole} ({round(100 * part / whole)}%)"


def _scorecard(conn) -> dict:
    gates = core.list_gates(conn, state="all")
    total = len(gates)
    rows = conn.execute(
        "SELECT MIN(raised_at) lo, MAX(raised_at) hi FROM gates").fetchone()
    window = 0
    if rows["lo"]:
        window = int(
            (datetime.strptime(rows["hi"], "%Y-%m-%dT%H:%M:%SZ")
             - datetime.strptime(rows["lo"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds())

    by_seat = {r["raised_by"]: r["n"] for r in conn.execute(
        "SELECT raised_by, COUNT(*) n FROM gates GROUP BY raised_by ORDER BY n DESC")}
    by_state = {r["state"]: r["n"] for r in conn.execute(
        "SELECT state, COUNT(*) n FROM rulings GROUP BY state")}
    closed = sum(by_state.values())

    to_ruling = []
    for g in gates:
        r = g["ruling"]
        if r and r["state"] in core.RULED_STATES:
            to_ruling.append(int(
                (datetime.strptime(r["ruled_at"], "%Y-%m-%dT%H:%M:%SZ")
                 - datetime.strptime(g["raised_at"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds()))

    # "The share of gates whose consumer actually acted" is the measure this
    # milestone cannot take directly: nothing records that a consumer acted. The
    # only observable proxy is a delivery check, so the number is split into what
    # is measurable and what is not, and UNKNOWN IS NEVER COUNTED AS ACTED.
    measurable = [g for g in gates if g["effective_delivery_check"]]
    confirmed = sum(
        1 for g in measurable
        if (o := core.latest_observation(conn, g["id"], "delivery")) and o["exit_code"] == 0)

    fields = {
        "decay check": sum(1 for g in gates if g["effective_decay_check"]),
        "delivery check": len(measurable),
        "binding": sum(1 for g in gates if g["binding_sha256"]),
        "options": sum(1 for g in gates if g["options"]),
        "horizon": sum(1 for g in gates if g["horizon"]),
    }
    checked = conn.execute(
        "SELECT COUNT(*) obs, COUNT(DISTINCT gate_id) g FROM observations").fetchone()

    return {
        "generated_at": core.now(),
        "window_seconds": window,
        "window_from": rows["lo"], "window_to": rows["hi"],
        "raised": total, "raised_by_seat": by_seat,
        "closed": closed, "open": total - closed, "closed_by_state": by_state,
        "ruled": sum(by_state.get(k, 0) for k in core.RULED_STATES),
        "time_to_ruling_seconds": sorted(to_ruling),
        "consumer_acted": {"measurable": len(measurable), "confirmed": confirmed,
                           "unknown": total - len(measurable)},
        "fields": fields,
        "observations": checked["obs"], "gates_ever_checked": checked["g"],
    }


def cmd_stats(a, conn) -> int:
    d = _scorecard(conn)
    if a.json:
        print(json.dumps(d, indent=2, default=str))
        return 0

    print(f"JANUS SCORECARD — {d['generated_at']}")
    if d["raised"] == 0:
        print("\nraised      0 gates. The ledger is empty, so every measure below "
              "would be 0 of 0.")
        print("            That is the honest scorecard, not a missing one.")
        return 0

    print(f"window      {d['window_from']} → {d['window_to']}"
          f"  ({_fmt_duration(d['window_seconds'])} of ledger)")
    print(f"\nRAISED      {d['raised']}")
    for seat, n in d["raised_by_seat"].items():
        print(f"  {seat:<24}{_pct(n, d['raised'])}")
    # No extrapolation. Refused BY NAME so the absence reads as a decision.
    if d["window_seconds"] < 7 * 86400:
        print("  per week                not reported — the ledger is "
              f"{_fmt_duration(d['window_seconds'])} old, shorter than the week "
              "this measure is defined over")

    print(f"\nCLOSED      {_pct(d['closed'], d['raised'])}   ·   open {d['open']}")
    for state in core.TERMINAL_STATES:
        n = d["closed_by_state"].get(state, 0)
        tail = "  ← a human actually ruled" if state in core.RULED_STATES and n else ""
        print(f"  {state:<24}{_pct(n, d['closed'])}{tail}")

    t = d["time_to_ruling_seconds"]
    if t:
        print(f"\nTIME TO RULING            n={len(t)} · median "
              f"{_fmt_duration(int(statistics.median(t)))} · fastest "
              f"{_fmt_duration(t[0])} · slowest {_fmt_duration(t[-1])}")
    else:
        print("\nTIME TO RULING            n=0 — no gate has been ruled on yet")
    print("  Rulings only. A superseded or expired gate was never ruled and is not "
          "a fast ruling.")

    c = d["consumer_acted"]
    print(f"\nCONSUMER ACTED            measurable {_pct(c['measurable'], d['raised'])}"
          f" · confirmed {c['confirmed']} · unknown {c['unknown']}")
    print("  A gate states what its consumer will do; only a delivery check reports "
          "whether it happened.")
    print("  Unknown is never counted as acted.")

    print("\nFIELDS THE BOARD DEPENDS ON")
    for name, n in d["fields"].items():
        note = ""
        if name == "decay check" and n < d["raised"]:
            note = f"  ← the board reads 'unmeasured' for the other {d['raised'] - n}"
        if name == "horizon" and n == 0:
            note = "  ← the overdue marker has never fired"
        print(f"  {name:<24}{_pct(n, d['raised'])}{note}")

    print(f"\nCHECKS RUN                {d['observations']} observation(s) across "
          f"{_pct(d['gates_ever_checked'], d['raised'])} gates")
    return 0


def _code_origin(module_path: Path) -> dict:
    """Where the running code lives, and whether it can move under the fleet.

    The `janus` on every seat's PATH was an EDITABLE install of a working tree,
    so whatever branch that tree happened to be checked out to was, silently,
    the fleet's production CLI — a reviewer's `git switch` changed the binary
    every other seat was running. Reporting it is not the fix, but an
    undiagnosable hazard is worse than a diagnosed one, and `doctor` is where a
    reader already goes to ask whether the tool can be trusted.
    """
    parts = set(module_path.parts)
    installed = bool(parts & {"site-packages", "dist-packages"})
    info = {"path": str(module_path), "installed": installed,
            "branch": None, "dirty": None}
    if installed:
        return info
    for cmd, key in ((["rev-parse", "--abbrev-ref", "HEAD"], "branch"),
                     (["status", "--porcelain"], "dirty")):
        r = subprocess.run(["git", "-C", str(module_path), *cmd],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            info[key] = r.stdout.strip() if key == "branch" else bool(r.stdout.strip())
    return info


def cmd_revise_check(a, conn) -> int:
    # Read the predecessor BEFORE inserting, and read the EFFECTIVE one. The
    # gate's base `decay_check`/`delivery_check` is immutable, so after the first
    # revision it is no longer what this revision replaced — a second revision
    # reported the original as `was` and lied about its own predecessor. Found by
    # a non-author review, in the one command whose entire purpose is correcting
    # a check that measured the wrong thing.
    before = core.get_gate(conn, a.gate_id)
    if before is None:
        raise JanusError(f"no such gate: {a.gate_id}")
    prior = before[f"effective_{a.kind}_check"]
    g = core.revise_check(conn, a.gate_id, a.kind, a.command,
                          core.seat_actor(a.seat), a.reason)
    print(f"{a.gate_id}: {a.kind} check revised (by {core.seat_actor(a.seat)})")
    print(f"  now: {g[f'effective_{a.kind}_check']}")
    print(f"  was: {prior or '(none — this gate had no check)'}")
    print("  Nothing was overwritten. The original stays on the gate, this "
          "revision is a new row,\n  and `janus show` prints both. A revision "
          "changes no state and touches no ruling.")
    return 0


def cmd_doctor(a, conn, *, open_blocker: str | None = None) -> int:
    problems = 0
    origin = _code_origin(Path(core.__file__).resolve().parent)
    if origin["installed"]:
        print(f"code        {origin['path']} (installed copy)")
    else:
        where = f" on branch {origin['branch']}" if origin["branch"] else ""
        dirty = ", with uncommitted changes" if origin["dirty"] else ""
        print(f"code        {origin['path']} (EDITABLE working tree{where}{dirty})")
        print("            whatever is checked out here is what every seat on this "
              "host runs")
    print(f"db          {core.DEFAULT_DB if not a.db else a.db}")
    storage_findings = core.storage_privacy_findings(a.db)
    if open_blocker and open_blocker not in storage_findings:
        storage_findings.append(open_blocker)
    if storage_findings:
        problems += 1
        for finding in storage_findings:
            print(f"storage     FAILED — {finding}")
        print("storage     no permissions were changed; restrict or relocate the "
              "ledger deliberately")
    else:
        print("storage     private (directory 0700; database family 0600; owner only)")
    if conn is None:
        print("ledger      checks skipped — storage identity is unsafe to open")
        return 1
    versions = [r["version"] for r in conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version")]
    print(f"migrations  {', '.join(versions) or 'none'}")
    # Append-only must be PROVEN, not documented — and the proof must not be
    # able to pass vacuously. An UPDATE matching zero rows never fires a BEFORE
    # UPDATE trigger, so on an empty ledger the naive check "no exception means
    # broken" silently proves nothing. Write a probe row first, then attempt to
    # mutate that exact row.
    core.audit(conn, core.seat_actor(getattr(a, "seat", None)), "doctor",
               None, "append-only probe")
    conn.commit()
    probe_id = conn.execute("SELECT MAX(id) AS id FROM audit_events").fetchone()["id"]
    try:
        conn.execute("UPDATE audit_events SET verb = 'tampered' WHERE id = ?", (probe_id,))
        conn.commit()
        print("append-only FAILED — an UPDATE on audit_events was accepted")
        problems += 1
    except Exception:
        conn.rollback()
        print("append-only enforced (UPDATE on a real row refused by trigger)")
    try:
        conn.execute("DELETE FROM audit_events WHERE id = ?", (probe_id,))
        conn.commit()
        print("append-only FAILED — a DELETE on audit_events was accepted")
        problems += 1
    except Exception:
        conn.rollback()
        print("append-only enforced (DELETE on a real row refused by trigger)")
    open_gates = core.list_gates(conn, state="open")
    print(f"open gates  {len(open_gates)}")
    drifted = 0
    # Drift is only worth reporting where it can still mislead someone into
    # acting: a gate still waiting, or one a human actually RULED on, whose
    # consumer may yet act on that ruling. A superseded, withdrawn or expired
    # gate drifting is noise — nobody ruled and nobody will act — and printing it
    # indented under "open gates" read as though it were still open, which is
    # how this line first shipped. A doctor that cries wolf stops being read.
    actionable = open_gates + [g for st in core.RULED_STATES
                               for g in core.list_gates(conn, state=st)]
    for g in actionable:
        if g["binding_sha256"]:
            ok, _ = core.verify_binding(
                g["binding_kind"], g["binding_locator"], g["binding_sha256"])
            if ok is False:
                drifted += 1
                print(f"drift       {g['id']} ({g['state']}) — bound artifact no longer matches")
    if drifted:
        print(f"drift       {drifted} actionable gate(s) bound to changed artifacts")
    seat = core.seat_actor(getattr(a, "seat", None))
    print(f"attribution writes will be attributed to {seat}")
    print("\nJanus records pending authority; it does not grant authority.")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="janus",
        description="A local-first ledger of decisions waiting on a human. "
                    "Janus records pending authority; it does not grant authority.")
    p.add_argument("--db", type=Path, help="ledger path (default ~/.janus/janus.db)")
    p.add_argument("--seat", help="declared seat, appended to your OS user")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("raise", help="raise a gate")
    r.add_argument("question")
    r.add_argument("--kind", required=True, choices=core.KINDS, help=KIND_HELP)
    r.add_argument("--decay", required=True, help="what worsens while this waits")
    r.add_argument("--consumer", required=True, help="who acts on the answer, and how")
    r.add_argument("--decay-check", help="re-runnable command; exit 0 = decay occurred")
    r.add_argument("--delivery-check", help="for resource gates: did the thing land")
    r.add_argument("--horizon", help="ISO date it expires; omit rather than invent one")
    r.add_argument("--bind-kind", choices=("file", "git", "text"))
    r.add_argument("--bind", help="file path | <repo>@<rev> | inline text")
    r.add_argument("--option", action="append",
                   help="id:label[:detail]; suffix id with * to recommend. Repeatable")
    r.add_argument("--cites", help="prior gate id this re-raises")
    r.set_defaults(fn=cmd_raise)

    ls = sub.add_parser("list", help="list gates")
    ls.add_argument("--state", default="open",
                    choices=("open", "all", *core.TERMINAL_STATES))
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(fn=cmd_list)

    sh = sub.add_parser("show", help="show one gate, and whether its binding still holds")
    sh.add_argument("gate_id")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(fn=cmd_show)

    ex = sub.add_parser(
        "export",
        help="stable digest-verified JSON (read-only; evidence, never authority)",
    )
    ex.add_argument("gate_id", nargs="?", help="one gate; omit for the complete ledger")
    ex.set_defaults(fn=cmd_export)

    d = sub.add_parser("decide", help="rule on a gate (the human's verb)")
    d.add_argument("gate_id")
    g = d.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", action="store_true")
    g.add_argument("--refuse", action="store_true")
    d.add_argument("--reason", required=True)
    d.add_argument("--option", help="required when the gate offers options")
    d.add_argument("--yes", action="store_true", help="rule even though bytes drifted")
    d.set_defaults(fn=cmd_decide)

    for name, fn, helptext in (
        ("withdraw", cmd_withdraw, "the raiser retracts the question"),
        ("expire", cmd_expire, "time ran out; nobody ruled"),
        ("supersede", cmd_supersede, "the world moved past it"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("gate_id")
        s.add_argument("--reason", required=True)
        s.add_argument("--yes", action="store_true")
        s.set_defaults(fn=fn)

    c = sub.add_parser("check", help="run a decay or delivery check (observation only)")
    c.add_argument("gate_id")
    c.add_argument("--kind", default="decay", choices=("decay", "delivery"))
    c.add_argument("--yes", action="store_true",
                   help="run the displayed command without confirming")
    c.set_defaults(fn=cmd_check)

    b = sub.add_parser("board", help="the one screen: what is waiting, how long, what worsens")
    b.add_argument("--check", action="store_true",
                   help="run every decay check first (on demand; observations only)")
    b.add_argument("--all", action="store_true", help="show every gate, past the one-screen fold")
    b.add_argument("--yes", action="store_true",
                   help="with --check: run the listed commands without confirming")
    b.set_defaults(fn=cmd_board)

    st = sub.add_parser("stats", help="the dated scorecard: is anyone actually using this")
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=cmd_stats)

    rv = sub.add_parser("revise-check",
                        help="correct a decay/delivery check that measured the wrong thing")
    rv.add_argument("gate_id")
    rv.add_argument("--kind", required=True, choices=("decay", "delivery"))
    rv.add_argument("--command", required=True, help="the check that measures the actual question")
    rv.add_argument("--reason", required=True,
                    help="what the old check measured INSTEAD of the question")
    rv.set_defaults(fn=cmd_revise_check)

    doc = sub.add_parser("doctor", help="ledger health, append-only proof, drift")
    doc.set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "export":
            return args.fn(args)
        if args.cmd == "doctor":
            try:
                conn = core.connect(args.db)
            except JanusError as e:
                return args.fn(args, None, open_blocker=str(e))
            return args.fn(args, conn)
        conn = core.connect(args.db)
        return args.fn(args, conn)
    except JanusError as e:
        print(f"janus: {e}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
