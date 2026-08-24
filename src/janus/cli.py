"""janus — the CLI. Every gate, both faces.

Design note: refusals are sentences, not tracebacks. This tool exists to be
reached for by a tired human at 23:00 and by an agent mid-task; both need to be
told what is wrong and what to do instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from . import core
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
    if g["decay_check"]:
        print(f"            check: {g['decay_check']}")
    if g["horizon"]:
        print(f"  horizon   {g['horizon']}")
    if g["delivery_check"]:
        print(f"  delivery  check: {g['delivery_check']}")
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
    print("  This records that a human ruled. It grants nothing: the consumer "
          "re-verifies before acting.")
    return 0


def cmd_decide(a, conn) -> int:
    return _close(a, conn, "approved" if a.approve else "refused", a.reason)


def cmd_withdraw(a, conn) -> int:
    return _close(a, conn, "withdrawn", a.reason)


def cmd_expire(a, conn) -> int:
    return _close(a, conn, "expired", a.reason)


def cmd_supersede(a, conn) -> int:
    return _close(a, conn, "superseded", a.reason)


def cmd_check(a, conn) -> int:
    result = core.observe(conn, a.gate_id, a.kind, core.seat_actor(a.seat))
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
_DECAY_RANK = {"landed": 0, "unchecked": 1, "unmeasured": 1, "not yet": 2}


def _decay_status(g: dict) -> str:
    """What is actually known about this gate's decay — never a guess.

    `unmeasured` and `unchecked` are printed rather than left blank on purpose.
    A decay sentence with no re-runnable check is a claim, and a board that
    renders a claim identically to an observation flatters the prose.
    """
    obs = [o for o in g["observations"] if o["kind"] == "decay"]
    if obs:                                   # get_gate returns these newest-first
        return "landed" if obs[0]["exit_code"] == 0 else "not yet"
    return "unchecked" if g["decay_check"] else "unmeasured"


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
    gates = core.list_gates(conn, state="open")
    if a.check:
        # On demand, never on a timer (M2). An observation records an exit
        # status; it never changes a gate's state.
        for g in gates:
            if g["decay_check"]:
                try:
                    core.observe(conn, g["id"], "decay", core.seat_actor(a.seat))
                except (JanusError, OSError) as e:
                    print(f"decay check failed to run for {g['id']}: {e}", file=sys.stderr)
        gates = core.list_gates(conn, state="open")

    if not gates:
        print("Nothing is waiting on a human.")
        return 0

    rows = []
    for g in gates:
        secs, age = _age(g["raised_at"])
        status = _decay_status(g)
        rows.append({"g": g, "secs": secs, "age": age, "status": status,
                     "overdue": _overdue(g)})
    rows.sort(key=lambda r: (_DECAY_RANK[r["status"]], 0 if r["overdue"] else 1, -r["secs"]))

    term = shutil.get_terminal_size(fallback=(100, 24))
    width = min(max(term.columns, 80), 120)
    head = 2 + 10 + 2 + 5 + 2 + 12 + 2 + 12 + 2      # indent + the five columns
    body = width - head

    landed = sum(1 for r in rows if r["status"] == "landed")
    unmeasured = sum(1 for r in rows if r["status"] in ("unmeasured", "unchecked"))
    summary = f"{len(rows)} waiting on a human — longest wait {max(r['age'] for r in rows)}"
    if landed:
        summary += f" · {landed} with decay observed to have landed"
    if unmeasured:
        summary += f" · {unmeasured} whose decay has never been checked"
    print(summary + "\n")

    # One screen or it is wrong (ROADMAP M2). When it does not fit, say so —
    # a board that silently drops rows is exactly the surface it replaces.
    per_gate = 2
    shown = len(rows) if a.all else max((max(term.lines, 12) - 8) // per_gate, 1)
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

    print(textwrap.dedent("""
        Sorted by observed decay, then a passed horizon (!), then the longest wait.
        'unmeasured' means the decay sentence carries no re-runnable check — unknown,
        which is not the same as fine. `janus board --check` runs the checks that exist.
        `janus show <id>` for the full question and its options.
        Reading this board is not authority to act.""").rstrip())
    return 0


def cmd_doctor(a, conn) -> int:
    problems = 0
    print(f"db          {core.DEFAULT_DB if not a.db else a.db}")
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
    for g in core.list_gates(conn, state="all"):
        if g["binding_sha256"]:
            ok, _ = core.verify_binding(
                g["binding_kind"], g["binding_locator"], g["binding_sha256"])
            if ok is False:
                drifted += 1
                print(f"  drifted: {g['id']} — bound artifact no longer matches")
    if drifted:
        print(f"drift       {drifted} gate(s) bound to changed artifacts")
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
    c.set_defaults(fn=cmd_check)

    b = sub.add_parser("board", help="the one screen: what is waiting, how long, what worsens")
    b.add_argument("--check", action="store_true",
                   help="run every decay check first (on demand; observations only)")
    b.add_argument("--all", action="store_true", help="show every gate, past the one-screen fold")
    b.set_defaults(fn=cmd_board)

    doc = sub.add_parser("doctor", help="ledger health, append-only proof, drift")
    doc.set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        conn = core.connect(args.db)
        return args.fn(args, conn)
    except JanusError as e:
        print(f"janus: {e}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
