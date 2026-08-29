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
4. Type, symlink, and hard-link aliases are visible rather than summarized as
   private.

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
- a second hard link is named with its observed link count;
- a symbolic-link database path is refused as private evidence;
- `doctor` exits `1` on broad existing storage while its modes remain
  byte-for-byte unchanged;
- `doctor` exits `0` and prints the exact `0700`/`0600` contract for a private
  family.

After implementation, the exact full repository gate passed **111 tests**. A
descriptor-hardening mutation then produced one failure under umask `777` while
the `000` case still passed; restoring the exact candidate returned both cases
to green. A non-editable wheel installed into a clean Python 3.12 environment;
real CLI creation under umasks `000` and `777` produced `0700/0700/0700/0600`
for the three nested directories and database, and installed `janus doctor`
reported the family private. Hosted-CI and independent-review evidence are
intentionally not claimed here until those gates run on a committed exact
candidate.
