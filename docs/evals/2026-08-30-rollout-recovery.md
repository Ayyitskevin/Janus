# Rollout recovery evaluation — 2026-08-30

## Question and assumptions

The change tests one claim: an operator can safely reconcile every state that
Janus's rollout can durably expose after an uncatchable process exit, without
guessing and without restoring the database.

The design began with four assumptions and tested each separately:

1. Atomic pathname replacement is not enough for crash durability; file data
   and changed directory entries need explicit synchronization.
2. A recovery journal must be validated before any path it names is consumed,
   and the recovery process must hold the same exclusive rollout lock.
3. A journal can coexist with a fully durable success receipt after a crash, so
   recovery must distinguish completed-forward from incomplete rollback.
4. Code rollback is compatible with the additive migration, but copying the
   prepared database backup during automatic recovery could destroy later data.

## Evidence used before implementation

- Python documents `os.replace` as atomic on successful same-filesystem rename
  and `os.fsync` as the primitive that forces pending writes:
  <https://docs.python.org/3/library/os.html#os.replace> and
  <https://docs.python.org/3/library/os.html#os.fsync>.
- SQLite's atomic-commit description durably writes recovery information before
  mutation, obtains exclusive ownership for recovery, and deletes its hot
  journal only after restoration has been flushed:
  <https://sqlite.org/atomiccommit.html#rollback>.
- SQLite also treats an abandoned journal as evidence of an interrupted
  transaction on the next open, not as proof that rollback is always the right
  outcome: <https://www.sqlite.org/tempfiles.html#rollback_journals>.
- Fleet evidence in `[[03-knowledge/concepts/architecture/atomic-replacement-ownership]]`
  already requires exact device/inode and provenance bytes before the first
  namespace mutation, and
  `[[07-operations/troubleshooting/crash-durable-control-boundaries]]` requires
  enough durable intent to resume without guessing.

The sources support the ordering and validation rules. The Janus-specific
completed-forward distinction comes from a direct fault injection: a process
can exit after the receipt's file and directory synchronization but before the
journal unlink.

## Real temporary-filesystem cases

All cases execute against real private directories, real symlinks, real atomic
replacement and fsync calls, and real forked processes. `os._exit(91)` bypasses
Python exception cleanup at four critical points. No live Janus install or
ledger is touched.

| Case | Expected result |
|---|---|
| hard exit after directory maintenance switch | exact prior inode restored |
| hard exit after symlink maintenance switch | exact raw link text restored |
| active entry missing after legacy rename | exact preserved inode restored |
| prior state already restored, journal remains | journal only is removed |
| original checkout path gone, exact candidate elsewhere | commit identity recovers |
| preview without `--yes` | tree and database bytes unchanged |
| wrong displayed journal digest | refusal, state unchanged |
| hard exit after candidate migration | code restored; migration retained |
| hard exit after candidate provenance write | prior bytes and mode restored |
| hard exit after durable success receipt | candidate completed forward |
| unknown journal property | refusal, state unchanged |
| journal target path escape | refusal, state unchanged |
| candidate release-marker drift | refusal, state unchanged |
| unrecognized installed-provenance bytes | refusal, state unchanged |
| changed maintenance refusal bytes | refusal, state unchanged |
| unexpected active directory | refusal, state unchanged |
| replaced legacy inode | refusal, state unchanged |
| changed source commit | refusal, state unchanged |
| receipt path escape | refusal, state unchanged |
| legacy path escape | refusal, state unchanged |
| symlinked receipt | refusal, target untouched |
| receipt digest mismatch | refusal, state unchanged |
| schema-invalid receipt with matching digest | refusal, state unchanged |
| oversized receipt | refusal before parsing |
| oversized journal | refusal before parsing |
| broad journal mode | refusal, state unchanged |
| broad rollout-lock mode | refusal, state unchanged |

## Result

From the isolated worktree environment:

```text
PATH="$PWD/.venv/bin:$PATH" ./scripts/check.sh
All checks passed!
218 passed in 68.94s
Successfully built janus_gates-0.1.0.tar.gz and
  janus_gates-0.1.0-py3-none-any.whl
clean wheel install + CLI smoke + packaged-migration verification: exit 0

.venv/bin/python -m pytest -q tests/test_apply_upgrade.py -k recovery_
24 passed, 19 deselected in 19.66s
```

These are local implementation and measurement results, not merge or deployment
authority. The final commit, hosted CI identity, and independent review belong
in the session handoff because they do not exist until publication.
