# Vision

**Every gate, both faces.**

Janus records pending authority; it does not grant authority. That governing
rule does not move.

## Product identity

Janus is a local-first ledger of the decisions an AI fleet is waiting on one
human to make. It owns the path from a raised gate, through the decay it
accrues while it waits, to a ruling bound to the exact artifact it approved.

It is the missing half of an execution authority system. Where an execution
module owns *authority granted*, Janus owns *authority pending* — and the fact
that pending authority has a cost.

An agent's urgency is not a priority. A chat message is not a decision. An
approval that does not name a digest is not an approval.

## What it owns, and what it must not

| System | Owns |
| --- | --- |
| **Janus** | Pending human decisions, their decay, and rulings bound to a digest |
| Execution module | Whether an action is authorized, and carrying it out |
| Operator workspace | Work, assignment, project state, activity history |
| Research memory | Evidence, claims, uncertainty, findings |
| Conversation relay | Live discussion and identity |
| Inference gateway | Model access, budgets, usage |

Janus does not orchestrate, execute, approve, notify, or schedule. It is a
ledger and a queue. Emitting a pointer to a gate is not notification; recording
that a human ruled is not granting.

## The three invariants

1. **A gate is open or closed, never both.** State is single-valued and
   terminal states are terminal. A reversal is a new gate that cites the old,
   never an edit.
2. **A ruling binds bytes, not names.** Every decision carries the SHA-256 of
   the artifact it applies to. If the digest changes, the ruling does not
   follow. Janus records the binding; the consumer enforces it.
3. **Reading a ruling is not authority.** A consumer that acts on an approval
   must independently re-verify the binding and its own preconditions. Janus is
   never in the permission path.

A fourth rule is operational rather than architectural, but it is why the
project exists: **a gate that nobody can act on is a bug in the gate, not in the
human.** Every gate names its consumer and what that consumer will do with each
possible answer. If it cannot, it is a note, and notes belong in a handoff.

## What decay means

Decay is the field that makes this more than a to-do list. It states what
becomes untrue, more expensive, or unrecoverable while the gate waits, and
where possible carries a command that checks whether it has happened yet.

Priority records how the raiser felt. Decay records what the delay costs. Only
one of those is a measurement, and only one of them can be checked by someone
who was not there.

## Non-goals

Naming these does not open them.

- No auto-approval, risk scoring that bypasses a human, inferred consent, or
  policy engine. Ever, at any maturity.
- No task tracking, assignment, sprints, or project structure.
- No notification service, escalation ladder, or paging.
- No storage of the artifact under decision — digest and locator only.
- No cross-module writes in the first milestone. Other systems learn about a
  gate by reading, or by a human telling them.
- No multi-user authorization, remote bind, or cloud hosting.

## Licensing

Apache-2.0, chosen to match this vision rather than to match a sibling. Athena
and Vulcan are AGPL because they are host-able operator services with real
appropriation risk. Janus is not one and never becomes one — loopback, single
user, no remote bind are all stated non-goals above — so the network clause
that makes the AGPL worth its friction would have nothing to act on here.

What Janus does have in common with Minerva, which is also Apache-2.0, is that
some of its value is an interchange contract: the M3 export is meant to be read
and verified by systems that are not Janus. A contract others should implement
is worth licensing permissively, and the explicit patent grant makes adopting
the mechanism safer than a bare MIT would.

Revisit this if Janus ever acquires a hosted form or commercial stakes. This is
a considered default, not legal advice.
- No dashboard for its own sake. A board view must answer the operator's
  question in one screen or it is not built.

## The adoption rule, learned the expensive way

A sibling module in this fleet shipped a genuinely good tool that agents did not
use for weeks. The causes were measured, not guessed: it had no skill entry so
it was invisible at session start, its binary was not on `PATH`, its
documentation led with the maximum-ceremony path, and nothing downstream
consumed its output.

Janus therefore treats adoption as part of the first working milestone, not a
follow-up:

- The agent-facing skill and the `PATH` entry ship **with** the first usable
  CLI, never after it.
- The documented default path is the cheapest correct one. The rigorous path is
  documented second, for when the record must defend itself.
- Seat attribution exists from the first schema, not as a retrofit.
- If no consumer reads a gate, that is a finding about Janus, and it gets
  measured rather than explained away.
