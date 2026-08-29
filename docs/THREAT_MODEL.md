# Threat model

Janus holds decision *metadata* for one trusted OS user on a loopback host. Its
contents are often more sensitive than the artifacts they point at, and its
worst failures are not breaches — they are a queue that quietly stops being
true.

## Trust boundary

One OS user, one machine, loopback bind. Loopback is not authentication and does
not isolate mutually untrusted processes running as that user. Any process able
to reach the database can read every gate and write rulings; the boundary is the
bind and the file permissions, nothing more.

This is inherited, not chosen, and it is why Janus must never enter the
permission path. A system that treats a Janus read as sufficient grounds to act
would be trusting a store that a same-OS-user process can rewrite.

## What is actually at risk

**Gate text leaks before the thing it describes is public.** This is the sharpest
risk and it is not obvious. An artifact can be private while its *existence* is
the secret: "approve disclosure of the auth bypass in X", "approve the
termination letter", "approve the acquisition NDA". A gate title can leak an
incident, a negotiation, or a person before any of them are public, and it leaks
in the one place designed to be scanned quickly. Treat titles as disclosable to
anyone who can read the database, and never put the finding in the question.

**Rulings are evidence about a person.** The record says what the operator
approved, when, and how long they took. That is an audit trail with real
consequences, and it is append-only by design — a bad ruling cannot be quietly
removed, only reversed by a new gate. That is the correct trade and it should be
a conscious one.

**Decay and delivery checks are commands.** A gate that ships a `check` ships
executable text. Anyone who can write a gate can propose a command that a later
operator or process runs. Checks must never run automatically on write, never
run as part of listing gates, and must be visible in full before they are
invoked. On-demand only, by explicit act.

*How this is honoured, and where it was broken.* `janus check <id>` runs one
check against one gate. `janus board --check` runs every stored check at once.
Each path initially shipped a version that ran stored text without first making
the operator read it: the board was corrected first, while the single-check path
remained an unguarded execution path. Both now share one boundary that prints
every effective command in full, flushes the preview before execution, and asks
for confirmation. A non-interactive caller must pass `--yes`; that skips the
prompt, not the preview. Consent binds the exact displayed command. A revision
visible when execution loads the gate invalidates that consent; one committed
after the final load cannot replace the displayed command held locally and
becomes effective on the next run. Janus therefore does not hold SQLite's global
writer lock across an arbitrary shell timeout merely to freeze the revision
table. A check that hangs is killed and recorded as exit `124` rather than
escaping as an exception, because an unrecorded check is indistinguishable from
one never run.

*Where this becomes remote code execution.* Everything above holds only while a
gate can be written solely by a process already running as this user — writing a
gate is already equivalent to running a command as them, so a check adds no
privilege. **M3 must not change that.** A read-only HTTP surface is safe; the
moment any remote or lower-trust caller can *write* a gate, its `check` field is
an arbitrary command that a later `--check` will run as this user. If ingest is
ever built, checks from a remote origin must be refused at write time, not
filtered at run time.

## Failure modes that matter more than attackers

**A queue that lies.** The corpus finding: a gate whose subject already shipped
sitting `open` forever. Trust in a board is lost once and not regained; the
`superseded` state exists for exactly this. Equally, an `approved` resource gate
whose thing never arrived reads as progress while the consumer is still blocked —
hence the delivery check.

**Silent wrong supersession.** `superseded` is the only terminal state settable
by someone who is neither raiser nor ruler, so it is the easiest to get wrong or
abuse. Mitigation is attribution and append-only history rather than
restriction: a wrong close is visible, answerable, and cheap to re-raise. A
digest would not help — it proves what overtook a gate, never that it did.

**A revised check that lies.** Migration 0002 lets a check be corrected, because
one written at raise time can measure something adjacent to the question and
append-only meant it could never be fixed — a delivered promise read as
outstanding forever, which is the "queue that lies" failure occurring inside the
section built to prevent it. The obvious abuse is revising a check to `true` so a
gate reports whatever the reviser wants. Mitigation is the one `superseded`
already relies on, and for the same reason: attribution plus append-only history.
A revision is a new row that cannot alter the original, `janus show` prints every
revision with who made it and why, and a bad one is visible, answerable, and
cheap to correct with another. Restricting who may revise would buy less than it
costs, and would put Janus in the business of judging its own records. Note the
revised command is executable text like any other check, so it inherits the
visible-before-invoked rule above.

**Approval drift.** A ruling that names an artifact loosely can be replayed
against different bytes. Mitigated by binding a SHA-256 at ruling time, recorded
by Janus and enforced by the consumer — never by Janus, which would put it in
the permission path.

**Becoming a decision-maker.** The catastrophic failure is not a leak. It is a
future version that infers consent, scores risk to skip a human, or auto-approves
anything. The record's entire value is that a person decided. `AGENTS.md`
rejects these at every maturity level, and this file restates it because threat
models are read by people who skip contracts.

## Explicitly out of scope

Multi-user authorization, remote access, network exposure, encryption at rest,
and protection against a hostile same-OS-user process. Protect the database with
OS permissions and backups.
