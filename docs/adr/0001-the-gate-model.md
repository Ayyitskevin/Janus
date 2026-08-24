# ADR 0001: The gate model

- Status: Proposed
- Date: 2026-08-23

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
- `state` — `open`, `approved`, `refused`, `expired`, `withdrawn`

`question`, `kind`, `decay`, and `consumer` are required. A gate without a
consumer is a note; a gate without decay has no claim on attention; a gate whose
question cannot be answered in one sentence has not been thought through. The
schema refuses all three rather than accepting a degraded record.

### The ruling

A ruling is `approved` or `refused`, carries a reason and the digest it was
bound to at the moment of ruling, and is terminal. `expired` and `withdrawn` are
terminal outcomes that are not rulings: the first is time, the second is the
raiser retracting the question.

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

## Open questions

- Does `kind` come from a fixed enum of irreversibility categories, or is it
  free text with a recommended vocabulary? A fixed enum is more analyzable and
  more likely to be wrong at the edges.
- Should a gate support a bounded set of options beyond approve/refuse — the
  "pick one of these three" decision, which is common and currently gets
  flattened into prose?
- Is `horizon` mandatory? A gate with no expiry never leaves the queue, but
  forcing every raiser to invent a date invites meaningless ones.
