# Roadmap

Milestones are contract-first: each states what becomes true, and what stays
deliberately unavailable. Nothing here authorizes a cross-module seam.

## M0 — settle the contract (no code)

- `README.md`, `docs/VISION.md`, ADR 0001 (the gate model), this file.
- The three invariants and the non-goal list are agreed before a schema exists.
- Resolve ADR 0001's open questions: `kind` enum vs vocabulary, multi-option
  gates, whether `horizon` is mandatory.
- Threat model sketch: Janus holds decision *metadata*, which can be sensitive
  even when the artifact is not — a gate title can leak an incident before it
  is public.

**Exit:** a non-author seat reviews the model and the non-goals.

## M1 — the local core, with adoption shipped alongside

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

## M2 — decay, and the board

- `decay.check` executed on demand — never on a timer in this milestone — with
  its exit status recorded as an observation, not a state change.
- `janus board`: the one screen answering *what is waiting, how long, what
  worsens.* Sorted by observed decay. If it does not fit one screen it is wrong.
- Expiry as an explicit operator action first; automatic expiry only once the
  horizon field has proven meaningful in practice.

**Exit:** the operator uses the board instead of grepping handoffs, measured by
asking rather than assumed.

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
