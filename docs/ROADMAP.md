# Roadmap

Milestones are contract-first: each states what becomes true, and what stays
deliberately unavailable. Nothing here authorizes a cross-module seam.

## M0 — settle the contract (no code) — **EXIT MET 2026-08-24**

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

**Exit — MET.** Codex, a non-author seat, reviewed the model and non-goals at
the pinned M0 revision. The review returned HOLD on the claim that the
four-value `kind` enum was exhaustive: its evidence came from one operator's
twelve-gate, partly self-referential corpus. Kevin kept the four values until a
real gate fails to classify, and ADR 0001 records both the limitation and that
falsifiable trigger. The durable review is
`shared/handoffs/2026-08-24_janus-m0-pr1-review-codex.md`.

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
  Both paths show the effective commands in full using a terminal-safe escaped
  representation and confirm before execution; unattended callers must pass
  `--yes`, terminal controls cannot repaint a hidden prefix, and an unseen
  revision cannot substitute different bytes after preview. Exit status is
  recorded as an observation; observations never change state.
- ~~`janus board`~~ — the one screen. Sorted by observed decay, then by a passed
  horizon, then by the longest wait; no priority field exists to sort by.
  Rank order is `landed` → `unchecked`/`unmeasured` → `not yet`: evidence of
  slack demotes a gate, while absence of evidence does not promote it to safe.
  A measured status carries the age of the observation that supports it,
  separately from gate or ruling age; freshness is visible without an invented
  expiry threshold. A check revision invalidates its predecessor's status until
  the replacement is observed.
  When the queue outgrows one screen the board says how many it hid and why,
  because a board that silently drops rows is the surface it replaces.
  A bare `janus` invocation now reaches this same board implementation; the
  explicit `janus board` spelling remains available. This removes one command
  from the cheapest operator path without claiming the operator has adopted it.
- ~~The board surfaces approved gates whose delivery check still fails, under
  their own heading~~ (ADR 0001) — **PROMISED, NOT DELIVERED**. The resource
  case forced the field; live authority-gate adoption proved the delivery gap
  is about consumer effects rather than gate kind.
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
  unknown among approved gates, and *unknown is never counted as acted*. A
  superseded gate with a delivery check is not eligible: nobody approved its
  consumer action.
- **A supersede is not a ruling.** Counting "the world moved past it" as a
  decision would report a fleet that rules on everything. Half of the first
  ledger's closures were supersedes; that is the finding, and averaging it away
  destroys it.

Taken late, and worth recording as such: this says "instrument from the first
day of M1", and instrumentation actually began at M4. The first scorecard
therefore describes a ledger already 8 gates old rather than one measured from
its first.

**Exit — MET.** First scorecard published 2026-08-24, and the numbers are bad in
the way the milestone anticipated: **8 gates, all 8 attributed to one declared
seat identity**, in a ledger 54 minutes wide. 1 of 8 carries a decay check, so the board reads
`unmeasured` for the other 7; 0 of 8 set a horizon, so the overdue marker has
never fired. 6 of 8 carry options, which is the one M0 corpus claim the live
data supports. A pillar with only one observed attribution label is a finding,
and it is now a printed one.

The second scorecard, captured 2026-08-29, shows that finding changed rather
than calcified: 47 gates were recorded under three attribution labels — 21
declaring Codex, 20 declaring Claude Code, and 6 without a declared seat. The
labels do not prove which process or person raised a gate. The ledger still has
meaningful adoption gaps: only 3 of 47 gates have measurable delivery and none
has a horizon. Counts, denominators, command, and installed-copy context are
preserved in `docs/evidence/2026-08-29-adoption-scorecard.md`; this dated
measurement does not claim M2 board use or M3 export consumption.

## Operational maturity — receipt-bound rollout built, deployment not performed

The repository now owns the reversible work that must precede a live upgrade:
exact candidate and rollback artifacts, a coherent private SQLite backup,
forward-migration rehearsal on a copy, rollback-reader rehearsal, retained
hashes, and a closed manifest. See ADR 0004 and
`docs/spec/upgrade-preparation-v1.md`.

The repository also owns the corresponding rollout seam: exact-artifact
preflight, live-snapshot freshness, commit-addressed candidate and rollback
environments, maintenance refusal, quiescence proof, deliberate permission
repair, atomic activation, crash journal, installed provenance, and the closed
`janus.rollout-receipt.v1` contract. See ADR 0005 and
`docs/spec/rollout-receipt-v1.md`.

The crash path is now an executable interface rather than operator prose.
`apply_upgrade.py recover` validates the closed
`janus.rollout-in-progress.v1` journal and exact live identities, previews
without mutation, then either restores prior code/provenance or recognizes an
exact durably published success. Post-receipt publication and cleanup errors
preserve matching candidate state rather than manufacture a rollback. It never
restores the database. See
`docs/spec/rollout-in-progress-v1.md` and the dated recovery evaluation.

This is intentionally not a shipped-install claim. Preparation records
`deployment_performed: false`; rollout has not been applied to mickey's live
ledger. The installed copy remains legacy state until Kevin approves the exact
reviewed candidate and maintenance operation. A preparation bundle is
short-lived evidence, not authority: any later gate or ruling requires a new
backup and rehearsal before rollout.

Janus's own rollout has an explicit ordering boundary between authority and
freshness. External human authorization may follow packet inspection without
changing the ledger. If the decision is mirrored into Janus, the ruling is
recorded before the final preparation or the bundle is prepared again afterward;
a ruling after preparation changes the ledger and the freshness check refuses
the prior packet. A focused invariant test pins this behavior without letting
Janus read its own ledger as permission.

## Later, explicitly not next

- Emitting a pointer into the conversation relay when a gate is raised. Useful,
  and it is the seam most likely to turn Janus into a notifier. It waits until
  the ledger is proven.
- Ingesting gates from other modules. Every sibling that built a seam before it
  had a measured consumer closed it again.
- Multi-option gates, if M0 defers them.
- Any remote bind or second user.

## M5–M8 — delegated decision progression — **PROPOSED**

ADR 0006 supersedes the original permanent rejection of auto-approval without
weakening append-only history, byte binding, or independent execution
authorization. Each milestone is a separate reviewed PR and remains dormant
until its own exit criteria are met.

### M5 — learnable records

- Append-only canonical decision-context snapshots.
- Structured reason codes and counterfactual feedback attached to human
  rulings without changing the ruling record.
- Stable export coverage for the new records.

**Exit:** real approvals and refusals can be reconstructed from only the facts
available before each ruling, with missing facts represented as unknown.

### M6 — shadow prediction

- A deep decision-engine module with deterministic eligibility.
- Loopback Vulcan and in-memory inference adapters.
- Append-only, non-terminal predictions with complete provenance.
- Chronological evaluation against later human rulings.

**Exit:** shadow predictions cannot close gates or invoke consumers, and the
evaluation reports every denominator including abstentions.

**Implementation built; empirical exit not met.** The deep engine, deterministic
eligibility, loopback Vulcan and in-memory adapters, typed append-only prediction
events, and chronological report are implemented. Tests prove the mechanics do
not close gates. Real prospective predictions and later human labels are still
required before M6 can exit; the current corpus cannot supply them retroactively.

### M7 — recommendation

- Applicable predictions on `show` and the operator board.
- Human decisions remain independently recorded.
- Drift and calibration measurements by project and action class.

**Exit:** the operator has used recommendations on real gates and the measured
record supports or rejects advancement without extrapolation.

### M8 — scoped autonomy

- Human-created, expiring, revocable delegation envelopes.
- Separate append-only delegated verdicts with database-enforced terminal
  exclusivity and complete artifact/context/policy/model binding.
- An off-by-default activation and immediate fallback to shadow.

**Exit:** a separately reviewed activation packet demonstrates the chosen
thresholds on held-out chronological decisions, Kevin approves the exact scope,
and the first narrow GREEN canary is measured after activation.
