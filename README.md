# Janus

**Every gate, both faces.**

Janus is a local-first ledger of the decisions an AI fleet is waiting on a human
to make. It records what was asked, what it was asked about, what decays while
it waits, and what was eventually ruled — and it never decides anything itself.

> Janus records pending authority; it does not grant authority.

## Quickstart

```bash
janus --seat <seat> raise "<one sentence a tired human can answer>" \
  --kind taste --decay "what worsens while this waits" \
  --consumer "<seat>: what happens on approve, what happens on refuse"

janus board                # the one screen: what is waiting, how long, what worsens
janus show <id>            # detail, and whether the binding still holds
janus stats                # the dated scorecard: is anyone actually using this
janus decide <id> --approve --reason "..." [--option <id>]
```

`board` is the screen to read first. It sorts by *observed* decay rather than by
any priority field — a decay check that has fired outranks one that has never
been run, which outranks one measured to still have time. A gate whose decay
sentence carries no re-runnable check is printed as `unmeasured`, because
unknown is not the same as fine. `janus list` remains the plain enumeration.

Under its own heading the board also carries **PROMISED, NOT DELIVERED**: gates a
human already approved whose `--delivery-check` has not yet succeeded. A ruling
closes a *decision*; it does not make a credential exist. Those gates have left
the decision queue while the thing they promised may never have arrived, and
nothing else was watching that gap. A promise drops off the moment its own check
passes.

Four fields are mandatory and the schema refuses a gate without them. Bind the
bytes with `--bind-kind file|git|text --bind <locator>`; offer alternatives with
repeatable `--option id:label[:detail]`, marking your recommendation with a
trailing `*` on the id. Full guidance lives in the fleet `janus` skill.

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
| `kind` | Why this needs a person: `irreversible` · `authority` · `taste` · `resource` |
| `raised_by` | The seat that raised it |
| `binding` | The exact artifact the answer applies to, pinned by SHA-256 |
| `decay` | What becomes untrue or more expensive while this waits — with a re-runnable check |
| `consumer` | Who acts on the answer, and what they will do |
| `options` | Optional named alternatives; empty means approve/refuse. Six of twelve real gates needed these |
| `delivery` | Optional check that a promised thing actually landed — for `resource` gates, an approval is a promise, not a delivery |
| `state` | `open` · `approved` · `refused` · `expired` · `withdrawn` · `superseded` |

Two fields carry most of the weight.

**`binding` is a digest, not a reference.** A ruling approves specific bytes. If
those bytes change, the ruling does not follow them — it is void, not "probably
still fine." This is the one lesson the fleet has already paid for: an approval
that names an artifact loosely can be replayed against a different artifact.

**`superseded` is a first-class outcome.** Measured across one real session,
the most common way a gate ends is not a ruling and not expiry — it is the world
moving past the question while it sat open. A queue that cannot say so
accumulates open gates whose subjects already shipped, and a board that lies
once stops being read.

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

M0 (the contract) and M1 (the ledger, the CLI, and adoption) shipped 2026-08-24.
M2's board has landed; its exit is deliberately **not** met yet, because that
exit is "the operator uses the board instead of grepping handoffs, measured by
asking rather than assumed", and nobody has been asked. M0's exit — a non-author
seat reviewing the model — is also still outstanding and is tracked as a gate in
Janus itself. See `docs/ROADMAP.md`.

## License

Apache-2.0. See [LICENSE](LICENSE).

Permissive rather than copyleft, deliberately. Part of what Janus is trying to
be useful for is a *shape* — a gate whose ruling binds a digest, and a queue
ordered by decay rather than by asserted priority. A format and a mechanism
spread by being cheap to adopt and safe to reimplement, which is what the
Apache patent grant is for. Copyleft's strongest tool is its network clause,
and Janus deliberately never becomes a network service: loopback only, one
user, no remote bind. There would be almost nothing for that clause to act on.
