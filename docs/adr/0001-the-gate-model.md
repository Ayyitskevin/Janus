# ADR 0001: The gate model

- Status: Proposed
- Date: 2026-08-23
- Amended: 2026-08-24 (open questions resolved against a real corpus)

## Context

An AI fleet operating under a rule that a human approves anything irreversible
produces a continuous stream of pending decisions. On the fleet this was built
for, 416 of 558 handoff documents contained such a gate, spread across five
surfaces that each hold it incidentally: handoff `open:` sections, an issue
tracker, a chat channel, a rules file, and per-agent memory files.

None of those surfaces is wrong; none of them owns the thing. An issue tracker
models work, and a decision is not work. A chat channel models discussion, and
a decision is not a message. A handoff models a session boundary, and a decision
outlives the session that raised it.

The specific failures this causes are observable:

- **Silent expiry.** A decision that mattered on Monday is irrelevant by Friday,
  and nothing says so; the human re-derives the context to discover it is moot.
- **Approval drift.** An approval given for "the PR" is later treated as
  covering a changed PR. This fleet has already paid for this once, in an
  execution module whose manifest binding compared a caller-supplied digest to
  a caller-supplied object.
- **Unrankable queues.** Every raiser believes its own gate is urgent, so a
  self-declared priority field sorts by insistence rather than by risk.
- **Orphan gates.** A gate is raised, the raising session ends, and no consumer
  exists to act on the answer. The human answers into a void.

## Decision

Janus models exactly one entity — the **gate** — and one terminal event per
gate, the **ruling**.

### The gate

A gate is one decision that only a human can make. It carries:

- `id`, `raised_by` (seat), `raised_at`
- `question` — one sentence a tired human can answer
- `kind` — which category of irreversibility makes this a human's call
- `binding` — optional; `{artifact_kind, locator, sha256}`
- `decay` — what worsens while it waits, with an optional re-runnable check
- `consumer` — who acts on the answer, and what they do with each outcome
- `horizon` — when the gate expires if unanswered
- `options` — optional ordered alternatives; empty means approve/refuse
- `state` — `open`, `approved`, `refused`, `expired`, `withdrawn`, `superseded`

`question`, `kind`, `decay`, and `consumer` are required. A gate without a
consumer is a note; a gate without decay has no claim on attention; a gate whose
question cannot be answered in one sentence has not been thought through. The
schema refuses all three rather than accepting a degraded record.

### The ruling

A ruling is `approved` or `refused`, carries a reason and the digest it was
bound to at the moment of ruling, and is terminal. When the gate carries
`options`, an `approved` ruling must also name exactly one option id.

Three terminal outcomes are not rulings, because nobody ruled: `expired` (time
ran out), `withdrawn` (the raiser retracted the question), and `superseded`
(the world moved past it — see the resolved open questions below, where the
corpus shows this is the most common outcome of all).

The record is append-only. A reversal is a **new gate** that cites the prior
gate, never an edit. This mirrors the correction discipline already used
elsewhere in the fleet: the record extends, it does not rewrite.

### Binding is the load-bearing rule

A ruling approves specific bytes. `sha256` is computed over the artifact
content, and the ruling stores the digest observed at ruling time. A consumer
must re-verify that digest against the live artifact before acting.

Janus records the binding. Janus does not enforce it. Enforcement in Janus would
put it in the permission path, which is the one place it must never be — an
approval record is evidence that a human ruled, and evidence does not confer
authority.

### Decay replaces priority

There is no priority field. `decay` is prose describing what the delay costs,
plus an optional `check` command whose exit status indicates whether the decay
has occurred. A board sorted by observed decay is sorted by risk of loss.

This is continuous with the fleet's existing rule that priority means risk of
loss rather than effort; decay makes that rule temporal and checkable.

## Consequences

**Accepted:**

- Raising a gate costs more than writing a line in a handoff, because four
  fields are mandatory. This is deliberate, and it is also the largest adoption
  risk. It is mitigated by making the cheapest correct path the documented
  default, not by relaxing the fields.
- Janus cannot answer "is this authorized?" — only "did a human rule, on what
  bytes, and when." Consumers keep their own checks.
- A gate can expire while still mattering. That is a real outcome and it is
  recorded as one, rather than being hidden by an indefinite queue.

**Rejected:**

- *Auto-approval or risk scoring that skips the human.* The record's entire
  value is that a person decided.
- *Storing the artifact.* Digest and locator only; the artifact lives where it
  lives.
- *A priority integer.* It measures the raiser's feelings.
- *Cross-module write seams in milestone 1.* Every sibling in this fleet that
  built a seam before it had a proven consumer closed it again.

## Open questions — resolved 2026-08-24

Settled against twelve real gates from one session rather than by taste. The
corpus is `docs/evidence/2026-08-24-gate-corpus.md`; two of the three answers
came out differently than the leaning recorded when this ADR was written.

### `kind` is a fixed enum, but NOT the irreversibility list

The obvious enum was the fleet's RED categories. Measured against the corpus it
covers **3 of 12**. The rest are perfectly reversible design and policy calls
that need a human because the human owns the taste, the authority, or the
resource. `kind` therefore answers *why this needs a person*, not *how
dangerous it is*:

- `irreversible` — cannot be undone once done
- `authority` — only the owner may rule: doctrine, legal, process
- `taste` — a design choice with no dominant answer
- `resource` — only the human can supply it: a credential, money, hardware

Four values covered all twelve with nothing left over and nothing forced. No
`other`: an escape hatch would absorb exactly the gates worth noticing. A gate
that fits none of these is evidence the enum is wrong, and that is a finding to
raise, not a value to add casually.

### Multi-option gates: YES, and they are the majority

Six of twelve gates present two or three named alternatives with costed
trade-offs, including both of the highest-stakes ones. Approve/refuse alone
would flatten half the corpus into prose.

A gate carries an optional ordered `options` list. Empty means approve/refuse.
Non-empty means the ruling selects exactly one option id, and a ruling that
names no option — or an unknown one — is refused at write time. The raiser
states the recommended option; a queue that offers choices without a
recommendation just moves the work.

### `horizon` is OPTIONAL

Six of twelve carry a real deadline, and every one of those is a gate blocking
work already in flight. The other six have no honest date. Making it mandatory
would manufacture six invented dates to get six real ones, and invented dates
are what teach an operator to ignore the field.

### Two things the corpus surfaced that this ADR had not considered

**A terminal `superseded` outcome is required.** Gate #1 — a review requested
at 18:17, its PR merged at 22:10 without it — was neither decided, refused, nor
expired. Nothing timed out, nobody retracted, nobody ruled: the world moved past
the question. This is the most common decay mode observed, and a state machine
lacking it accumulates open gates whose subjects already shipped. A board that
lies once stops being read. `superseded` records what overtook the gate, and is
set by whoever observes it, not only by the raiser.

The full terminal set is therefore: `approved`, `refused`, `expired`,
`withdrawn`, `superseded`.

**Not every gate is a decision.** Gate #2 asks the operator to *do* something
only they can do — mint a credential. It has a question, a consumer, decay, and
it blocked three artifacts for hours, but no ruling makes a token exist. It is
admitted under `kind: resource`, and its ruling means "I will" or "I won't" —
which deliberately leaves a gap between the ruling and the effect. That gap is
real and must not be hidden: an `approved` resource gate is a promise, not a
delivery, and the consumer still has to check that the thing arrived.

## Still genuinely open

- Whether `superseded` needs to name the superseding artifact by digest, the way
  a ruling does, or whether free text is honest enough for something observed
  after the fact.
- Whether a resource gate should carry an explicit `delivered` observation
  distinct from its ruling, or whether that is the consumer's business.
