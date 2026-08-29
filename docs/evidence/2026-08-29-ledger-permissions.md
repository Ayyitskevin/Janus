# Ledger permission evidence — 2026-08-29

This evidence validates whether Janus's stated single-OS-user boundary survives
real filesystem creation behavior. It does not authorize merge, installation,
or changing the live ledger's permissions.

## Assumptions tested

1. New nested ledger directories can be made exactly `0700` without relying on
   the caller's umask.
2. The main database and SQLite WAL/shared-memory sidecars can remain exactly
   `0600` across that same range.
3. Existing broad storage can be diagnosed without a silent chmod.
4. Missing paths, wrong identity, symlink, and hard-link aliases are visible
   rather than summarized as private.
5. A new ledger is refused when another OS user could replace its pathname.

## Before

The candidate's four focused tests against unchanged main produced four
failures: nested parents were `0777` under umask `000`, the privacy inspector did
not exist, `doctor` returned success on `0775`/`0644` storage, and it printed no
private-storage evidence.

Read-only `lstat` measurement of the live mickey family found:

| Path role | Mode |
| --- | --- |
| `~/.janus` | `0775` |
| database | `0644` |
| WAL | `0644` |
| shared memory | `0644` |

The home directory is `0750` and the primary group currently has only Kevin as
a member, so this is a direct-mode hardening finding rather than proof that
another current account read the ledger. No live mode or database byte changed.
Because the ledger directory itself is group-writable and non-sticky, the final
candidate intentionally refuses to open it. Any later installation must be
sequenced after human-approved backup and repair; this slice performs neither.

## Creation matrix

One real `core.connect()` creation was run for each of 18 umasks:

```text
000 002 007 022 027 077 117 177 222
277 337 377 444 477 555 577 677 777
```

Each case used three newly created nested directories and held the SQLite
connection open while inspecting the main database, WAL, and shared-memory
files. Results: **18/18 directory families were exactly `0700`; 18/18 database
families were exactly `0600`; 18/18 privacy inspections returned zero
findings.** Temporary directories were removed after the run.

## Adversarial cases

Invariant tests additionally prove:

- `0775` directory and `0644` database modes are both named;
- required missing paths and wrong owner/type findings are not flattened into
  "private";
- a second hard link is named with its observed link count;
- dangling database symlinks create no target under umasks `000` and `777`;
- a symlink anywhere in a new ledger's directory chain creates no database;
- replaceable existing parents are refused before database creation;
- existing ledgers in writable sticky or non-sticky directories are refused;
- group/world-writable database and sidecar files are refused;
- a database entry appearing between preflight and exclusive creation is
  refused without creating its symlink target;
- directory and database creation failures return structured CLI refusals, not
  tracebacks;
- descriptor-close failures remain structured, and cannot preempt exact-inode
  cleanup after failed hardening;
- a failed descriptor hardening removes the exact empty file Janus created;
- rollback-journal identity is inspected before `doctor` opens SQLite;
- stable export refuses replaceable directories, database/directory symlinks,
  hard links, and sidecar type hazards without creating or migrating storage;
- `doctor` exits `1` on broad existing storage while its modes remain
  byte-for-byte unchanged;
- `doctor` exits `0` and prints the exact `0700`/`0600` contract for a private
  family.

The first fixed-point review correctly held the candidate: it reproduced a
dangling-symlink bypass, creation within an attacker-writable directory,
absence reported as private, and a wrong-type `doctor` crash before diagnosis.
Each became an invariant test before the candidate was revised.

The repeated review held again because an existing ledger in a writable
directory remained replaceable, and because an inaccessible path escaped as a
traceback. Both now fail through structured diagnostics before SQLite opens.

The final spec review then found the same policy duplicated incorrectly at the
stable export seam: its read-only connector resolved and opened the path
directly. Export now calls the shared identity preflight before its unchanged
`mode=ro` open, with adversarial coverage for both connectors.

The final standards pass rejected the remaining mode exceptions: a writable
database is directly mutable, and sticky protection does not cover a sidecar
name that is still absent. The final invariant is exact and shared:
`0700` directory, `0600` family files, safe identity, or refusal.

The next timing pass found two adjacent failure paths: absence was sampled
twice, allowing a new entry to inherit a stale preflight result, and filesystem
creation errors escaped the CLI boundary. Setup now uses one absence snapshot,
exclusive creation, a mandatory post-create preflight, and structured errors.

The final descriptor fault injection found `close(2)` could override that
structured refusal and skip failed-hardening cleanup. Descriptor invalidation
is now centralized; cleanup runs even when close also reports an error.

After implementation, the exact full repository gate passed **124 tests**. A
descriptor-hardening mutation then produced one failure under umask `777` while
the `000` case still passed; restoring the exact candidate returned both cases
to green. A non-editable wheel installed into a clean Python 3.12 environment;
real CLI creation under umasks `000` and `777` produced `0700/0700/0700/0600`
for the three nested directories and database, and installed `janus doctor`
reported the family private. Hosted-CI and independent-review evidence are
intentionally not claimed here until those gates run on a committed exact
candidate.
