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
  Exit status is recorded as an observation; observations never change state.
- ~~`janus board`~~ — the one screen. Sorted by observed decay, then by a passed
  horizon, then by the longest wait; no priority field exists to sort by.
  Rank order is `landed` → `unchecked`/`unmeasured` → `not yet`: evidence of
  slack demotes a gate, while absence of evidence does not promote it to safe.
  When the queue outgrows one screen the board says how many it hid and why,
  because a board that silently drops rows is the surface it replaces.
- ~~Expiry as an explicit operator action~~ — `janus expire` (M1). Automatic
  expiry still deliberately absent; `horizon` has not yet proven meaningful.

**Exit — NOT MET.** The exit is *the operator uses the board instead of grepping
handoffs, measured by asking rather than assumed*, and nobody has been asked.
The code landing is not the milestone closing. This stays open until there is an
answer, and "he has not said no" is not an answer.

## M3 — read-only surfaces

- Loopback HTTP, GET-only, mirroring the CLI. No mutation over HTTP in this
  milestone.
- A stable export artifact — a versioned, digest-verified gate record other
  systems can read without a live database.

**Exit:** one sibling reads the export without Janus knowing or caring.

## M4 — measured adoption

- Instrument from the first day of M1: gates raised per week by seat, time from
  raise to ruling, expiry rate, and the share of gates whose consumer actually
  acted.
- Publish the numbers even when they are bad. A pillar nobody uses is a finding.

**Exit:** a dated scorecard with no blank measures.

## Later, explicitly not next

- Emitting a pointer into the conversation relay when a gate is raised. Useful,
  and it is the seam most likely to turn Janus into a notifier. It waits until
  the ledger is proven.
- Ingesting gates from other modules. Every sibling that built a seam before it
  had a measured consumer closed it again.
- Multi-option gates, if M0 defers them.
- Any remote bind or second user.
