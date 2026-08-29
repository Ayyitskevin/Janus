# ADR 0003: New ledger storage is private; existing storage is diagnosed

- **Status:** Proposed; becomes Accepted when this human-reviewed change merges
- **Date:** 2026-08-29
- **Deciders:** Codex design; human review required by `AGENTS.md`
- **Supersedes:** nothing

## Context

Janus's threat model names one trusted OS user and says the database's file
permissions are part of that boundary. The implementation did not own the
claim. `Path.mkdir(parents=True)` used ambient defaults, SQLite created the
database under the ambient umask, and `doctor` inspected neither. On mickey the
live family measured `0775` for `~/.janus` and `0644` for the database, WAL, and
shared-memory files. Kevin's private `0750` home and private primary group limit
the present reachability, but an alternate `--db` location can silently remove
that ancestor protection.

## Decision

Before SQLite opens a missing ledger, Janus creates each missing parent
directory itself and sets only those newly created directories to `0700`. It
then reserves the new database with an exclusive descriptor and sets that
descriptor to `0600` before SQLite sees the path. The descriptor identity avoids
a close/reopen chmod race, and explicit modes make the result independent of a
permissive or over-restrictive umask. SQLite's database family consequently
inherits the owner-only file mode.

`janus doctor` inspects the exact active family: containing directory, database,
`-wal`, `-shm`, and `-journal` when present. It fails on a wrong owner, wrong
type, mode other than `0700`/`0600`, a symlink, or more than one hard link to a
database-family file. Missing optional sidecars are not findings.

Inspection never repairs. Existing directories and files are not chmodded by an
ordinary open or by `doctor`; the output says so and exits nonzero. A human can
then choose the maintenance window, backup, and exact permission change. This
keeps feedback in Janus without turning a diagnostic into a hidden migration.

## Consequences

- A new ledger remains private under every measured umask from `000` through
  `777`, including its live WAL and shared-memory files.
- Existing broad storage becomes loud but keeps working, preserving adoption
  while the owner schedules deliberate repair.
- Alternate locations no longer inherit unsafe creation defaults silently.
- Symlink and hard-link aliases remain possible for existing storage but are
  visible failures rather than accepted as owner-only evidence.
- This change writes no schema migration and does not alter gates, rulings,
  observations, audit semantics, stable export, or execution authority.

## Alternatives considered

- **Trust umask.** Rejected: the real CLI can be started by many harnesses, and
  ambient process policy is not Janus's invariant.
- **Automatically chmod every open.** Rejected: it mutates live external state,
  can surprise intentional group access, and makes a diagnostic an unannounced
  migration.
- **Refuse every ordinary command until old modes are repaired.** Rejected for
  this compatibility slice: it would strand the live decision queue at install
  time. `doctor` supplies fail-loud feedback while secure creation prevents new
  debt.
- **Inspect only the main database.** Rejected: in WAL mode the database family
  includes sidecars whose permissions can disclose committed gate content.

## Sources and evidence

- Python 3.11 `Path.mkdir` documents that intermediate parents ignore the
  supplied mode: https://docs.python.org/3.11/library/pathlib.html#pathlib.Path.mkdir
- Python 3.11 `os.open` documents exclusive creation and umask-masked modes:
  https://docs.python.org/3.11/library/os.html#os.open
- SQLite documents that WAL and journal files are part of the database family:
  https://sqlite.org/howtocorrupt.html#_deleting_a_hot_journal
- Measured cases and commands: `docs/evidence/2026-08-29-ledger-permissions.md`.
