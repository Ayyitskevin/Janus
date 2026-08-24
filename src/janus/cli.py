"""janus — the CLI. Every gate, both faces.

Design note: refusals are sentences, not tracebacks. This tool exists to be
reached for by a tired human at 23:00 and by an agent mid-task; both need to be
told what is wrong and what to do instead.
"""

from __future__ import annotations

import argparse
import json
import sys
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
