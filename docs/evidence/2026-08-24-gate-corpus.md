# Gate corpus — one real session, 2026-08-23/24

Twelve pending-human items produced by a single working session on the fleet
Janus is being designed for. Recorded before the schema exists, so the model is
settled against observed gates rather than imagined ones.

Sources: `shared/logs/mickey-actions.log` for 2026-08-23,
`shared/handoffs/2026-08-23_*`, and Buzz `#fleet` history.

| # | Gate | Shape | Why a human | Horizon? | Outcome |
|---|---|---|---|---|---|
| 1 | Non-author review + merge of Minerva PR #45 | approve/refuse | irreversible (security surface) | yes — evidence pinned to a commit, branch diverges | **merged without the review** |
| 2 | Provision a Claude-seat Athena token | *action request* | resource (only the operator can mint it) | no | still open |
| 3 | Add an L4 doctrinal trigger to Part II | approve/refuse | authority (doctrine) | no | still open |
| 4 | Minerva: new `ActorKind` vs append to `actor_id` | **multi-option (2)** | taste (design, costed both ways) | yes — blocked implementation | option B |
| 5 | Identity model: declared / tokens / phased | **multi-option (3)** | taste | yes — blocked implementation | option A |
| 6 | Janus licence: Apache-2.0 vs AGPL-3.0 | **multi-option (2)** | authority (legal) | yes — before forks exist | Apache-2.0 |
| 7 | GLM: pre-merge reviewer vs post-merge auditor | **multi-option (2)** | authority (process) | no | still open |
| 8 | Deploy Minerva v0.3.0a1 to mickey | approve/refuse | irreversible (live service) | no | approved |
| 9 | Re-enable the seat-canary timer | approve/refuse | authority (quiet hours) | no | still open |
| 10 | `kind`: fixed enum vs vocabulary | **multi-option (2)** | taste | yes — blocks M1 schema | open |
| 11 | Multi-option gates: yes/no | approve/refuse | taste | yes — blocks M1 schema | open |
| 12 | Is `horizon` mandatory | **multi-option (2)** | taste | yes — blocks M1 schema | open |

## What the corpus says

**Multi-option is the majority shape, not an edge case.** Six of twelve gates
present two or three named alternatives with costed trade-offs, and the two
highest-stakes ones (#4, #5) are both multi-option. A model offering only
approve/refuse would flatten half the corpus into prose and lose exactly the
structure that made those decisions answerable in one pass.

**The RED list is the wrong `kind` enum.** Mapping each gate onto the fleet's
irreversibility categories — money/legal, schema-against-live-data,
security-sensitive, shared-state deletion, infrastructure teardown,
public/irreversible — covers only 3 of 12. The rest are design and policy calls
that are perfectly reversible; they need a human because the human owns the
taste, the authority, or the resource, not because the action cannot be undone.

Four categories cover all twelve with nothing left over:

- **irreversible** — cannot be undone; the RED list (#1, #8)
- **authority** — only the owner may rule: doctrine, legal, process (#3, #6, #7, #9)
- **taste** — a design choice with no dominant answer (#4, #5, #10, #11, #12)
- **resource** — only the human can supply it: credential, money, hardware (#2)

**Horizon is meaningful in roughly half.** Six of twelve carry a real deadline,
and every one of those six is a gate blocking work already underway. The other
six have no honest date, and forcing one would manufacture noise.

**One gate is not a decision at all.** #2 asks the operator to *do* something
only they can do. It has a question, a consumer, and decay, and it blocked three
downstream artifacts for hours — but no ruling makes the token exist.

## The finding that matters most

**Gate #1 was neither decided, refused, nor expired. It was overtaken.**

The review was requested at 18:17. The PR was merged at 22:10 without it. The
gate was still `open` at the moment its subject stopped existing as a pending
question — the world moved past it.

Expiry does not describe this: nothing timed out. Withdrawal does not describe
it: the raiser never retracted. Refusal does not describe it: nobody ruled.

This is the most common decay mode in the corpus, and it is precisely the one
that destroys trust in a queue. A board showing an `open` gate whose subject
already shipped is worse than no board, because the operator learns the queue
lies and stops reading it. A state machine without this outcome will
systematically accumulate stale-but-open gates and blame the human for them.
