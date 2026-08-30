# ADR 0003: New ledger storage is private; existing storage is diagnosed

- **Status:** Accepted (2026-08-29; implementation merged as `f412c7d`)
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

Creation is admitted only through a symlink-free directory chain whose entries
are owned by the process user or root and cannot be replaced by another OS user.
Sticky directories such as `/tmp` are accepted only when their protected child
has an expected owner. An existing containing directory must already be owned by
the process user at exactly `0700`; Janus refuses rather than chmodding it. The
new file is re-identified after descriptor hardening, and a failed hardening
attempt removes only the exact inode Janus created. Connection setup snapshots
absence once and always takes the exclusive-creation path from that snapshot;
an entry that appears during setup is a refusal, never stale permission to open.
After preflight, writable SQLite opens use `mode=rw`, so an existing database
that disappears cannot be recreated through SQLite's default create behavior.
Filesystem creation errors are translated into typed, operator-facing storage
refusals; ledger-integrity failures such as a changed migration checksum remain
distinct.

This boundary deliberately requires POSIX owner and mode semantics. On a
platform without them Janus refuses with an operator-facing error rather than
claiming an equivalent privacy guarantee it cannot prove.
On Linux POSIX ACLs do not bypass an exact owner-only mode: the group permission
bits correspond to the ACL mask, which caps every named-user and named-group
entry. Thus `0700`/`0600` makes inherited or retained non-owner ACL entries
ineffective; an effective grant necessarily changes the checked mode bits.

`janus doctor` inspects the exact active family: containing directory, database,
`-wal`, `-shm`, and `-journal` when present. It fails on a wrong owner, wrong
type, mode other than `0700`/`0600`, a symlink, or more than one hard link to a
database-family file. Missing optional sidecars are not findings.

Inspection never repairs. Existing directories and files are not chmodded by an
ordinary open or by `doctor`, and `doctor` does not create a missing ledger; the
output says so and exits nonzero. A human can then choose the maintenance
window, backup, and exact permission change. This keeps feedback in Janus
without turning a diagnostic into a hidden migration.
`doctor` uses an existing-only connector, so deletion between inspection and
SQLite open remains absence rather than falling through the ordinary command
path's private-creation behavior.
Any wrong mode is a refusal, not only a diagnostic finding. A writable database
can be mutated directly; a writable containing directory can replace the main
database; and even a sticky writable directory leaves absent WAL, SHM, or
journal names available for another user to create. Janus therefore requires
the containing directory to be exactly `0700` and every present family file to
be exactly `0600`. Other identity hazards (symlinks, wrong types or owners, and
extra hard links) are likewise refused before SQLite opens them. `doctor` prints
those findings and skips checks that would require an unsafe open.

The boundary is shared by ordinary writable commands and the stable export's
separate logically read-only connector. Export does not create the main
database, migrate schema, or write ledger rows. SQLite may materialize WAL/SHM
coordination files for its `mode=ro` snapshot; a successful export post-checks
the resulting family after close. Both connectors lexically normalize an
absolute pathname without resolving symbolic links, refuse the same identity
hazards, and then open SQLite in their explicit `mode=rw` or `mode=ro`.

## Consequences

- A new ledger remains private under every measured umask from `000` through
  `777`, including its live WAL and shared-memory files.
- Existing broad storage fails closed until the owner schedules deliberate
  repair. No ordinary command or export silently accepts a weaker boundary.
- A diagnostic cannot create or recreate the ledger, including across an
  inspection/open race.
- Alternate locations no longer inherit unsafe creation defaults or
  replaceable parent paths silently.
- Database-family symlinks, wrong types/owners, and hard-link aliases are
  visible failures and are refused before SQLite opens them.
- This change writes no schema migration and does not alter gates, rulings,
  observations, audit semantics, the stable-export format, or execution
  authority.

## Alternatives considered

- **Trust umask.** Rejected: the real CLI can be started by many harnesses, and
  ambient process policy is not Janus's invariant.
- **Automatically chmod every open.** Rejected: it mutates live external state,
  can surprise intentional group access, and makes a diagnostic an unannounced
  migration.
- **Allow broad existing modes when one pathname looks stable.** Rejected after
  fixed-point review reproduced database replacement, direct file mutation, and
  creation of absent sidecars in a sticky directory. This would preserve
  compatibility by giving up ledger integrity. Installation must instead be
  sequenced after deliberate repair; this change does not perform that repair.
- **Inspect only the main database.** Rejected: in WAL mode the database family
  includes sidecars whose permissions can disclose committed gate content.

## Sources and evidence

- Python 3.11 `Path.mkdir` documents that intermediate parents ignore the
  supplied mode: https://docs.python.org/3.11/library/pathlib.html#pathlib.Path.mkdir
- Python 3.11 `os.open` documents exclusive creation and umask-masked modes:
  https://docs.python.org/3.11/library/os.html#os.open
- Linux `open(2)` documents that `O_CREAT|O_EXCL` refuses symbolic links:
  https://man7.org/linux/man-pages/man2/open.2.html
- GNU/Linux `chmod(1)` documents sticky-directory rename protection:
  https://man7.org/linux/man-pages/man1/chmod.1.html
- Linux `path_resolution(7)` documents component lookup and directory
  permission semantics:
  https://man7.org/linux/man-pages/man7/path_resolution.7.html
- Linux `acl(5)` documents the correspondence between group mode bits and the
  ACL mask, plus the mask's cap on named-user and named-group permissions:
  https://man7.org/linux/man-pages/man5/acl.5.html
- SQLite documents that WAL and journal files are part of the database family:
  https://sqlite.org/howtocorrupt.html#_deleting_a_hot_journal
- Measured cases and commands: `docs/evidence/2026-08-29-ledger-permissions.md`.
