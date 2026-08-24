# Janus

**Every gate, both faces.**

Janus is a local-first ledger of the decisions an AI fleet is waiting on a human
to make. It records what was asked, what it was asked about, what decays while
it waits, and what was eventually ruled — and it never decides anything itself.

> Janus records pending authority; it does not grant authority.

## Why this exists

A well-run agent fleet manufactures human-decision debt on purpose. The
governing rule is that a human approves anything irreversible — money and legal
logic, schema changes against live data, security-sensitive code, deletion of
shared state, infrastructure teardown, anything public-facing. Every one of
those is a stop, and every stop produces a question that outlives the session
that asked it.

Measured on the fleet this was built for: **416 of 558 handoff documents (75%)
contain a pending human gate**, and those gates were scattered across five
different surfaces — handoff `open:` sections, an issue tracker, a chat channel,
a rules file, and per-agent memory. No surface answered the only question that
matters to the human:

> What is waiting on me, how long has it waited, and what gets worse if I keep
> waiting?

Agents are cheap and parallel. The operator is neither. In a fleet where every
other scarce resource is metered — GPU time, model budgets, work in flight —
human attention is the one nobody measures.

## What a gate is

One decision, raised by one agent, that only a human can make.

| Field | Meaning |
| --- | --- |
| `question` | What is actually being asked, in one sentence a tired human can answer |
| `kind` | Which category of irreversibility makes this a human's call |
| `raised_by` | The seat that raised it |
| `binding` | The exact artifact the answer applies to, pinned by SHA-256 |
| `decay` | What becomes untrue or more expensive while this waits — with a re-runnable check |
| `consumer` | Who acts on the answer, and what they will do |
| `state` | `open` · `approved` · `refused` · `expired` · `withdrawn` |

Two fields carry most of the weight.

**`binding` is a digest, not a reference.** A ruling approves specific bytes. If
those bytes change, the ruling does not follow them — it is void, not "probably
still fine." This is the one lesson the fleet has already paid for: an approval
that names an artifact loosely can be replayed against a different artifact.

**`decay` replaces priority.** A priority field records how urgent the raiser
felt, which is not information. Decay records what the delay actually costs —
"this branch diverges from main", "this measurement was taken at commit X and
is being cited as current" — and where possible ships the command that checks
whether it has happened yet. A queue sorted by observed decay is sorted by risk
of loss. A queue sorted by self-declared priority is sorted by whoever is most
insistent.

## What Janus never does

- **It never grants authority.** Reading an approval out of Janus is not
  permission to act. The system that acts re-verifies the binding digest itself.
  An approval record is evidence that a human ruled, and evidence does not
  confer authority.
- **It never decides.** There is no auto-approve, no "low risk so skip the
  human", no policy engine, no inferred consent. The entire value of the record
  is that a person made the call; a rule that decides on their behalf destroys
  the only thing being stored.
- **It never owns the work.** Task state, assignment, and project structure
  belong to the operator workspace. Janus holds the gate, not the job.
- **It never holds the artifact.** Only its digest, and enough identity to find
  it again.
- **It never edits a ruling.** A reversal is a new gate that cites the old one.

## Status

Pre-implementation. The contract is being settled before any code is written;
see `docs/VISION.md`, `docs/adr/0001-the-gate-model.md`, and `docs/ROADMAP.md`.
