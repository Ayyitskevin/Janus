# Roadmap

Milestones are contract-first: each states what becomes true, and what stays
deliberately unavailable. Nothing here authorizes a cross-module seam.

## M0 — settle the contract (no code)

- `README.md`, `docs/VISION.md`, ADR 0001 (the gate model), this file.
- The three invariants and the non-goal list are agreed before a schema exists.
- ~~Resolve ADR 0001's open questions~~ **done 2026-08-24**, settled against a
  twelve-gate corpus from one real session (`docs/evidence/2026-08-24-gate-corpus.md`)
  rather than by taste: `kind` is a four-value enum of *why a human is needed*
  (`irreversible`/`authority`/`taste`/`resource`) and explicitly NOT the
  irreversibility list, which covered only 3 of 12; multi-option gates are
  required and are the majority shape; `horizon` is optional. The corpus also
  forced a fifth terminal state, `superseded`, and admitted resource requests
  that are not decisions.
- ~~Threat model sketch~~ **done 2026-08-24** — `docs/THREAT_MODEL.md`. The
  sharpest risk is not a breach: a gate title can leak an incident, a
  negotiation, or a person in the one surface designed to be scanned quickly.
  The catastrophic failure is a future version that decides on the operator's
  behalf.

**Exit:** a non-author seat reviews the model and the non-goals.

## M1 — the local core, with adoption shipped alongside — **SHIPPED 2026-08-24**

Exit met: three real fleet gates raised into `~/.janus/janus.db`, one ruled
end to end, and a decay check run as an observation. 20 invariant tests green.
Adoption shipped in the same commit: `janus` on `PATH` and the fleet `janus`
skill. What is deliberately still absent: any HTTP surface, any cross-module
write.

- Append-only SQLite: `gates`, `rulings`, `audit_events`. Triggers refuse update
  and delete. Forward-only packaged migrations with recorded checksums.
- Seat attribution in the first schema — a declared seat appended to the OS
  user, never replacing it, and never accepted from a remote caller.
- CLI: `raise`, `list`, `show`, `decide`, `withdraw`, `expire`, `doctor`.
- `binding` computed and stored on both raise and rule; `janus show` states
  plainly when a live artifact no longer matches its bound digest.
- **Shipped in the same milestone, not after:** the agent skill entry, the
  `PATH` wrapper, and a documented cheapest-correct path.

**Exit:** a real gate from this fleet raised, ruled, and consumed end to end.

**Deliberately unavailable:** any HTTP surface, any write from another module.

## M2 — decay, and the board — **BUILT 2026-08-24, EXIT NOT MET**

- ~~`decay.check` executed on demand~~ — `janus check <id>` (M1) and
  `janus board --check`, which runs every check that exists. Never on a timer.
  Both paths show the effective commands in full and confirm before execution;
  unattended callers must pass `--yes`, and an unseen revision cannot substitute
  different bytes after preview. Exit status is recorded as an observation;
  observations never change state.
- ~~`janus board`~~ — the one screen. Sorted by observed decay, then by a passed
  horizon, then by the longest wait; no priority field exists to sort by.
  Rank order is `landed` → `unchecked`/`unmeasured` → `not yet`: evidence of
  slack demotes a gate, while absence of evidence does not promote it to safe.
  When the queue outgrows one screen the board says how many it hid and why,
  because a board that silently drops rows is the surface it replaces.
- ~~The board surfaces approved resource gates whose delivery check still
  fails, under their own heading~~ (ADR 0001) — **PROMISED, NOT DELIVERED**.
  Shipped late: the first build of the board omitted it, which is exactly the
  gap ADR 0001 wrote the section to close. An approved gate with no delivery
  check at all is counted in a sentence rather than listed, because a row
  nothing can ever clear teaches the reader to skip the section.
- ~~Expiry as an explicit operator action~~ — `janus expire` (M1). Automatic
  expiry still deliberately absent; `horizon` has not yet proven meaningful.

**Exit — NOT MET, and now measured rather than pending.** Asked 2026-08-24;
the operator's answer was *"not yet — haven't used it"*. Recorded as the result,
not as a failure to chase: a board that was built and is not used is the same
class of finding as a pillar nobody adopts, and it is worth more written down
than argued away. M2 stays open until the answer changes. Re-ask, do not assume.

## M3 — read-only surfaces

- Loopback HTTP, GET-only, mirroring the CLI. No mutation over HTTP in this
  milestone.
- ~~A stable export artifact~~ — **BUILT, EXIT NOT MET.** `janus export`
  publishes a versioned, digest-verified complete or exact-gate snapshot from a
  read-only connection. It carries recorded inputs rather than running checks or
  computing live verdicts, and it declares Janus's state semantics so a sibling
  need not copy them. Contract: `docs/spec/export-v1.md`.

**Exit — NOT MET.** One sibling must read and independently verify the export
without Janus knowing or caring. Beacon is the measured first consumer; its
current legacy reader still consumes `list --json`, so implementation alone
does not satisfy this exit.

## M4 — measured adoption — **SHIPPED 2026-08-24**

`janus stats` (add `--json`) prints the dated scorecard. Every measure this
milestone named is there, and three rules keep it honest, each with a test:
every measure prints a number including zero; every rate prints its denominator,
because a percentage over n=2 is a lie with a decimal point; and nothing is
extrapolated — a per-week rate over a 54-minute ledger is invention, so the
window is stated and the rate is **refused by name** rather than omitted, so the
absence reads as a decision instead of an oversight.

Two measures needed a judgment call rather than a query:

- **"The share of gates whose consumer actually acted"** cannot be taken
  directly — nothing records that a consumer acted. The only observable proxy is
  a delivery check, so the number is split into measurable, confirmed, and
  unknown, and *unknown is never counted as acted*.
- **A supersede is not a ruling.** Counting "the world moved past it" as a
  decision would report a fleet that rules on everything. Half of the first
  ledger's closures were supersedes; that is the finding, and averaging it away
  destroys it.

Taken late, and worth recording as such: this says "instrument from the first
day of M1", and instrumentation actually began at M4. The first scorecard
therefore describes a ledger already 8 gates old rather than one measured from
its first.

**Exit — MET.** First scorecard published 2026-08-24, and the numbers are bad in
the way the milestone anticipated: **8 gates, all 8 raised by a single seat**, in
a ledger 54 minutes wide. 1 of 8 carries a decay check, so the board reads
`unmeasured` for the other 7; 0 of 8 set a horizon, so the overdue marker has
never fired. 6 of 8 carry options, which is the one M0 corpus claim the live
data supports. A pillar nobody but its author uses is a finding, and it is now
a printed one.

## Later, explicitly not next

- Emitting a pointer into the conversation relay when a gate is raised. Useful,
  and it is the seam most likely to turn Janus into a notifier. It waits until
  the ledger is proven.
- Ingesting gates from other modules. Every sibling that built a seam before it
  had a measured consumer closed it again.
- Multi-option gates, if M0 defers them.
- Any remote bind or second user.
