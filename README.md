# Janus

[![CI](https://github.com/Ayyitskevin/Janus/actions/workflows/ci.yml/badge.svg)](https://github.com/Ayyitskevin/Janus/actions/workflows/ci.yml)

**Every gate, every accountable face.**

Janus is a local-first ledger of the decisions an AI fleet is waiting to have
made. It records what was asked, what it was asked about, what decays while it
waits, and what was eventually decided with exact provenance.

> Janus records decision authority; it does not grant execution authority.

The current release remains human-only. [ADR 0006](docs/adr/0006-delegated-decision-progression.md)
proposes a measured progression from learnable human records through shadow and
recommendation modes to explicitly delegated machine verdicts. No autonomous
verdict is implemented or activated yet.

## Quickstart

```bash
janus --seat <seat> raise "<one sentence a tired human can answer>" \
  --kind taste --decay "what worsens while this waits" \
  --consumer "<seat>: what happens on approve, what happens on refuse"

janus                      # the one screen: what is waiting, how long, what worsens
janus board --all          # explicit board command; show rows below the first screen
janus list --unmeasured-delivery  # approved decisions with no delivery check
janus show <id>            # detail, and whether the binding still holds
janus check <id>           # preview one stored command, confirm, then observe
janus stats                # the dated scorecard: is anyone actually using this
janus export [<id>]        # stable, digest-verified evidence (never authority)
janus export --request-id ID  # recover one gate from its saved raise request
janus verify-export FILE   # verify an export offline; use - to read from stdin
janus revise-check <id> --kind delivery --command "..." --reason "..."
janus context <id> --project janus --action-class merge --environment test \
  --fact tests_passed=yes --fact security_sensitive=no --evidence-ref ci:run-123
janus decide <id> --approve --reason "..." --reason-code tests.pass \
  --counterfactual "A failed required check would change this decision."
janus predict <id> --shadow --model simple  # non-terminal; cannot authorize
janus shadow-report                         # chronological outcomes + denominators
```

`janus` opens the board by default; `janus board` remains its explicit form.
It is the screen to read first. It sorts by *observed* decay rather than by any
priority field — a decay check that has fired outranks one that has never been
run, which outranks one measured to still have time. A gate whose decay sentence
carries no re-runnable check is printed as `unmeasured`, because unknown is not
the same as fine. `janus list` remains the plain enumeration.
Measured rows show two different clocks rather than blending them: gate or
ruling age is on the first line, and the age of the status-driving observation
is on the second. Janus exposes freshness without inventing a threshold at
which evidence automatically becomes stale. Revising a check starts a new
measurement epoch: predecessor observations remain history, while the board
returns to `unchecked` until the effective check runs.

A check is executable text written once, at raise time, by someone guessing at a
future they have not seen — so `revise-check` exists for when it turns out to
measure something adjacent to the question. It is a **new row**, never an edit:
the original stays on the gate, `show` prints every revision with who made it and
why, and the effective check is simply the newest one. That is the append-only
rule kept rather than bargained with. Both `check` and `board --check` print the
effective commands in full, as terminal-safe escaped string representations,
before they run and ask for confirmation. Quotes delimit the command;
backslashes, control characters, and non-ASCII characters are escaped so stored
text cannot repaint the preview. An unattended caller must add `--yes`; that
flag skips the prompt, never the preview. A replacement already visible when
execution loads the gate invalidates the preview; a later revision cannot
substitute its unseen bytes for the command Janus already displayed and held
locally.

`export` is the machine-readable boundary. Unlike the diagnostic `list --json`
shape, it is versioned, self-describing, complete, and protected by per-record
and whole-document SHA-256 digests. It opens the ledger read-only, performs no
migration, runs no check, and does not inspect live bindings. The exact format
and cross-language conformance vector are in [the export v1
specification](docs/spec/export-v1.md). Verifying an export proves content
identity only; it does not turn a ruling into permission to act.

A conductor handing off one decision should send the expected gate ID and
canonical document SHA-256 through a trusted channel independent of the file.
The reviewer can then run this without the ledger or the bound artifact:

```bash
janus verify-export decision.json --expect-gate "$gate_id" \
  --expect-document-sha256 "$document_sha256" --json
```

Use the export's `integrity.document_sha256` from the trusted source. Reformatting
the JSON changes the file bytes but preserves that digest. With no expectations,
success means the file passed its internal format and integrity checks. With
both flags, the receipt identifies the exact decision and version the conductor
sent. It includes the recorded state, consumer, selected option and
binding-evidence status. A complete-ledger export gives a count and digest and
cannot satisfy `--expect-gate`. A failed verification exits 2 with a diagnostic
on stderr and no receipt. The reviewer still checks current proposal bytes,
delivery evidence and their own authority before acting.

For a file-bound human ruling, the consumer can also check a local copy of the
proposal, even after a rename. Hash the proposed file directly and use the gate
and document digest from the trusted handoff.

```bash
(
  checksum=$(sha256sum < "$proposal") || exit 1
  janus verify-export decision.json --expect-gate "$gate_id" \
    --expect-document-sha256 "$document_sha256" \
    --expect-bound-file-sha256 "${checksum%% *}" --json
)
```

On a match, `matched_bound_file_sha256` identifies the bytes measured when
the human ruled. These can differ from the bytes present when the gate was
raised. Check the receipt's state as well. A matching file can carry a
`refused` ruling. A different file digest or a decision without a recorded
file digest exits 2. Janus reads only the supplied export during verification.

The diagnostic shape is also available where an agent most needs it: `raise`, `decide`,
`withdraw`, `expire`, and `supersede` accept `--json` and print the gate as one object on
stdout (the same shape as `show --json`), with the advice lines on stderr — so the id of a
gate you just raised comes back as data, not as prose to parse. For plain command
substitution, `raise --quiet` prints the bare id and nothing else on stdout. Capture it
into a lowercase name: under zsh, `GID`/`UID`/`EGID`/`EUID` are special parameters and
assigning `GID=$(janus raise ...)` calls setgid(2), which fails with "(eval):1: failed to
change group ID: operation not permitted" — a shell behavior, never a Janus one.

For a decision that another agent may resume, save a request ID in the work
packet before the first raise. Use the same ID and decision on every attempt:

```bash
janus --seat conductor raise "Use the wider margin on the contact sheet?" \
  --kind taste --decay "The proof waits for layout approval." \
  --consumer "Print builder applies the chosen margin." \
  --request-id gallery/contact-sheet-margin-1 --quiet
```

On the same ledger, a retry returns the original gate ID, even from another
seat or after a ruling. Use `--json` to read its current state and original
raiser. Changing a raise field, the ordered options, or the resolved binding
under the same ID produces a conflict. Inspect the original gate before
choosing a fresh ID for a new decision. Without a request ID, each raise creates
a separate gate. IDs are case-sensitive and use 1–128 ASCII letters, digits,
dots, underscores, colons, slashes, or hyphens, starting with a letter or digit.

If the raise response was lost, a fresh conductor can recover the gate from
the same ledger with just the request ID saved in its work packet:

```bash
janus export --request-id gallery/contact-sheet-margin-1 --output decision.json
```

The export contains the decision as recorded at read time, including any later
ruling. It has the same bytes as exporting that gate by its generated ID at
the same snapshot. The conductor can recover it even if the original bound
file has moved. An unknown request ID exits 2 with a diagnostic on stderr and
empty stdout. Pass a successful export to the reviewer with independently
trusted gate and digest expectations for `janus verify-export`.

Use `--output FILE` to publish a handoff. It works with any export selector.
Janus stages the complete export beside the destination with mode 0600 and
replaces the destination in one rename. By default, a successful file export writes
nothing to stdout. If the ledger is unavailable or publication fails, an
earlier handoff stays intact. Report recovery as pending and retry the same
request on its owning ledger. A file left from an earlier run can describe
a different decision or an older version.

When handing one decision to a consumer, add `--receipt-json` to get the
verification values for the exact snapshot published by this invocation:

```bash
janus export --request-id gallery/contact-sheet-margin-1 --output decision.json --receipt-json
```

After publication succeeds, stdout contains a single JSON object with
`gate_id` and `document_sha256`, followed by a newline. This also works with
a positional gate ID. It requires one selected decision and a file destination.
Pass those two values to the consumer through a separate trusted work packet.
The consumer can then check the file with `verify-export`, even if another
publisher has since replaced the delivery path.

A lookup or publication failure emits no receipt. If writing the receipt
fails after the rename, Janus exits nonzero and reports that the file was
published. Treat the receipt as unavailable. Retrying publishes the snapshot
available at retry time. Use a complete receipt from a successful invocation.

Choose an existing output directory where another OS user cannot replace
your files. Janus checks directory ownership and permissions and refuses
non-sticky group- or world-writable parents. An existing destination must be
a regular file. The source ledger and its SQLite sidecar names are reserved,
including case and Unicode-normalization variants in the same directory.
When the ledger or destination name contains non-ASCII characters, use a
literal `.json` suffix for a new file beside the ledger, or publish in a
separate directory. Filesystems differ in how they match absent sidecar names.
To stream the same JSON and trailing newline, omit `--output` or use
`--output -`.

Decision contexts and structured human feedback are typed append-only audit
events, so the unchanged export v1 envelope carries their exact bytes and
digests. Context can be recorded only before a ruling; omitted facts remain
`unknown`, and feedback cites the exact latest snapshot. The closed payloads
and minimization rules are in [decision-learning events
v1](docs/spec/decision-learning-events-v1.md). They collect future evaluation
evidence; they do not make a prediction or change who decides.
Shadow prediction is equally explicit: `predict` requires `--shadow`, runs a
categorical eligibility guard before any model call, and records `abstain` on
unknown facts, protected scopes, drift, invalid output, or inference failure.
It cannot write a ruling or appear as a recommendation. The loopback-only
Vulcan seam, provenance fields, model-identity limitation, and evaluation
denominators are specified in [shadow predictions
v1](docs/spec/shadow-predictions-v1.md).
For a WAL-mode ledger, SQLite may materialize private `-wal`/`-shm`
coordination files even on a `mode=ro` connection. Export post-checks those
files against the same `0600` boundary; it never creates the main database or
changes schema, rows, audit history, or gate state.

The ledger is the local trust boundary, not just an implementation detail. A
new ledger and every directory Janus creates for it are owner-only (`0600` and
`0700` respectively), independent of the caller's umask. Creation refuses a
replaceable or symlinked directory chain rather than securing one file inside
an unsafe pathname. `janus doctor` audits the database, WAL, shared-memory, and
rollback-journal family for ownership, type, mode, symlink, and hard-link drift.
It reports existing exposure and exits nonzero, but never silently changes
permissions on live storage or creates a missing ledger; unsafe identity paths
are not opened. In particular, an existing ledger family that is not exactly
`0700`/`0600` must be repaired or relocated before ordinary commands or stable
export resume.

Under its own heading the board also carries **PROMISED, NOT DELIVERED**: gates a
human already approved whose `--delivery-check` has not yet succeeded. A ruling
closes a *decision*; it does not make a credential, edit, or other consumer
effect exist. A promise drops off the moment its own check passes.

When approvals have no delivery check, the board links to
`janus list --unmeasured-delivery`. It shows each full question, the chosen
option when there is one, and the recorded consumer. Delivery is unknown for
these gates. Adding a delivery check removes the gate from this list even
before its first observation. Append `--json` for the ordinary list records.

Four fields are mandatory and the schema refuses a gate without them. Bind the
bytes with `--bind-kind file|git|text --bind <locator>`; offer alternatives with
repeatable `--option id:label[:detail]`, marking your recommendation with a
trailing `*` on the id. Full guidance lives in the fleet `janus` skill.

## Development

Janus declares Python 3.11+ support; CI exercises 3.11 through the current
stable line, 3.14. A fresh clone can install the test toolchain and run the same
gate CI uses:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
./scripts/check.sh
```

The gate lints and compiles the sources, runs every invariant test, builds a
source distribution and wheel, then installs that wheel into a clean virtual
environment and checks the CLI plus packaged migrations. Keep it green before
pushing; CI repeats the same gate on every branch push and pull request.

### Prepare an upgrade without deploying it

Janus keeps deployment behind a separate human gate, but the repository owns
the reversible preparation work. From a clean candidate checkout, create a
private `0700` parent and pass the exact commit reported by the currently
installed copy:

```bash
rollback_commit=$(awk '$1 == "commit" {print $2}' ~/.local/share/janus/INSTALLED)
prepared_parent=$(mktemp -d "$HOME/.local/share/janus-preparation.XXXXXX")
python scripts/prepare_upgrade.py \
  --db "$HOME/.janus/janus.db" \
  --output "$prepared_parent/bundle" \
  --rollback-commit "$rollback_commit"
```

The command builds candidate and rollback wheels from exact committed trees,
creates a coherent SQLite backup, migrates only a private rehearsal copy, and
proves the rollback wheel can still read that copy. The retained manifest is
closed by [the upgrade-preparation v1
schema](docs/spec/upgrade-preparation-v1.schema.json) and always records
`deployment_performed: false`. It does not chmod the live ledger, install code,
or authorize deployment. Full refusal and privacy semantics are in [ADR
0004](docs/adr/0004-rehearsed-upgrades.md).

### Preflight and apply that exact preparation

The same clean candidate checkout can verify that a preparation is still
deployable without changing installed code or logical ledger content:

```bash
python scripts/apply_upgrade.py preflight \
  --bundle "$prepared_parent/bundle" \
  --db "$HOME/.janus/janus.db" \
  --install-root "$HOME/.local/share/janus" \
  --active "$HOME/.local/share/janus/venv" \
  --wrapper "$HOME/.local/bin/janus"
```

Preflight requires the exact clean commit that created the bundle, re-hashes
both wheels and the backup, binds the currently installed rollback commit, and
takes a new coherent SQLite snapshot. Any intervening Janus activity makes the
bundle stale; prepare a new one instead of treating an old backup as authority.
The Python environment running this repository script must include the declared
test toolchain, and `lsof` is required for the mutating command.

For Janus's own rollout, keep authority and freshness separate. External human
authorization may follow inspection of the prepared packet as long as it does
not write the ledger. If the decision is also recorded in Janus, record it
before the final preparation or create a fresh bundle afterward. A later gate,
ruling, observation, or check revision intentionally makes the prior bundle
stale. Run preflight immediately before apply; the rollout command never reads
Janus as its own permission source.

Actual rollout remains a human-reviewed maintenance operation. After reviewing
the displayed identities and effects, the operator invokes the same command as
`apply --yes`. `--yes` only confirms those displayed effects; authorization is
external to Janus. The command stages exact candidate and rollback environments,
enters a fail-closed maintenance state, proves the ledger is quiescent and still
fresh, repairs legacy storage to `0700`/`0600`, runs the rehearsed migration,
atomically activates the candidate, and writes a closed local receipt. It never
restores the database backup automatically because doing so could discard later
decisions. See [ADR 0005](docs/adr/0005-receipt-bound-rollout.md) and the
[rollout-receipt v1 contract](docs/spec/rollout-receipt-v1.md).

If a process or machine stops after maintenance begins, the durable journal
keeps ordinary Janus commands fail-closed. Inspect it from an exact clean
checkout of the candidate commit that began the rollout:

```bash
python scripts/apply_upgrade.py recover \
  --install-root "$HOME/.local/share/janus" \
  --active "$HOME/.local/share/janus/venv"
```

The default is read-only and prints the journal digest, observed active and
provenance states, and exact reconciliation effects. After reviewing that
output, repeat with `--yes`. Recovery revalidates the same digest under the
rollout lock. An incomplete rollout restores only the exact prior code entry
point and `INSTALLED` bytes; a fully published, exact success receipt completes
forward by removing the stale journal. A receipt-publication or journal-cleanup
error preserves matching candidate state and refuses a retry instead of
manufacturing a code rollback around an already-migrated database. Unknown state
is left untouched. The database backup is never restored automatically. The
closed journal contract
and refusal rules are in the [rollout recovery journal v1
specification](docs/spec/rollout-in-progress-v1.md).

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

One decision, raised by one agent, that currently waits for a human ruling.
Under ADR 0006, future gates may be eligible for a separately identified
machine verdict only when a human-created delegation and deterministic safety
rules both allow it.

| Field | Meaning |
| --- | --- |
| `question` | What is actually being asked, in one sentence a tired human can answer |
| `kind` | Why this needs a person: `irreversible` · `authority` · `taste` · `resource` |
| `raised_by` | The seat that raised it |
| `binding` | The exact artifact the answer applies to, pinned by SHA-256 |
| `decay` | What becomes untrue or more expensive while this waits — with a re-runnable check |
| `consumer` | Who acts on the answer, and what they will do |
| `options` | Optional named alternatives; empty means approve/refuse. Six of twelve real gates needed these |
| `delivery` | Optional post-approval check that the promised consumer effect actually landed |
| `state` | `open` · `approved` · `refused` · `expired` · `withdrawn` · `superseded` |

Two fields carry most of the weight.

**`binding` is a digest, not a reference.** A ruling approves specific bytes. If
those bytes change, the ruling does not follow them: it remains historical
evidence about the recorded bytes and does not apply to the new ones. This is the
one lesson the fleet has already paid for: an approval that names an artifact
loosely can be replayed against a different artifact.

**`delivery` is evidence on a second axis.** A post-ruling check may report that
the approved effect landed. It does not mutate the ruling, prove causality, name
the post-action bytes unless its stored command explicitly checks them, or grant
authority. Janus therefore reports binding and delivery separately. Delivery
checks cannot run before approval, and a historical pass must be run again to
detect a later regression. Revising a delivery check immediately makes results
from its predecessor ineligible for current status while retaining them in
append-only history.

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

- **It never grants execution authority.** Reading a human ruling or future
  delegated verdict out of Janus is not permission to act. The system that acts
  re-verifies the binding, its own authorization, and every execution
  precondition.
- **It never infers delegation.** A confidence score, model preference, or
  historical pattern cannot widen an explicit human-created envelope. Unknown,
  expired, or drifted scope means abstain.
- **It never impersonates a person.** Human rulings and machine predictions or
  verdicts use separate records, identities, and language.
- **It never delegates fleet RED classes under current doctrine.** Security,
  money/legal, live-data migration, infrastructure, and public or irreversible
  effects still require the human.
- **It never owns the work.** Task state, assignment, and project structure
  belong to the operator workspace. Janus holds the gate, not the job.
- **It never holds the artifact.** Only its digest, and enough identity to find
  it again.
- **It never edits a ruling.** A reversal is a new gate that cites the old one.

## Installing it where a fleet will use it

Install a **copy**, not the working tree:

```bash
python3 -m venv ~/.local/share/janus/venv
~/.local/share/janus/venv/bin/pip install /path/to/janus     # note: no -e
```

and put a wrapper on `PATH` that execs that venv's `janus`. Publishing a change
is then a deliberate act rather than a side effect of editing.

For upgrades, use the prepared, receipt-bound flow above. A publisher that
force-reinstalls whichever branch a mutable checkout happens to contain cannot
prove installed identity, backup freshness, or rollback readiness.

This is worth the extra step. An editable install (`pip install -e`) makes the
checkout itself the program, so on a machine where several agents share one
clone, a `git switch` during a review — or a half-saved edit — silently changes
the binary every other agent is running. That is not hypothetical: it happened
here, a reviewer reported failures nobody else could reproduce, and a missing
import took the whole fleet's CLI down mid-edit. `janus doctor` reports which of
the two you are running, and names the branch when it is a working tree.

## Status

M0 (the contract) and M1 (the ledger, the CLI, and adoption) shipped 2026-08-24.
M0's non-author review exit is met; the review's accepted limitation and
revisit trigger live in ADR 0001. M2's board has landed, but its usage exit is
deliberately **not** met: when asked on 2026-08-24, the operator said *"not yet —
haven't used it"*. M3's export contract is built, while independent sibling
consumption remains open. A second M4 scorecard on 2026-08-29 measured 47 gates
under Codex, Claude Code, and unseated attribution labels, without treating
those labels as proof of identity, board use, or export use. See
`docs/ROADMAP.md`.

## License

Apache-2.0. See [LICENSE](LICENSE).

Permissive rather than copyleft, deliberately. Part of what Janus is trying to
be useful for is a *shape* — a gate whose ruling binds a digest, and a queue
ordered by decay rather than by asserted priority. A format and a mechanism
spread by being cheap to adopt and safe to reimplement, which is what the
Apache patent grant is for. Copyleft's strongest tool is its network clause,
and Janus deliberately never becomes a network service: loopback only, one
user, no remote bind. There would be almost nothing for that clause to act on.
