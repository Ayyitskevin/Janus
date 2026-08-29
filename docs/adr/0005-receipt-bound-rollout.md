# ADR 0005: Rollout consumes a fresh preparation receipt, never a branch

- **Status:** Proposed; becomes Accepted when this human-reviewed change merges
- **Date:** 2026-08-29
- **Deciders:** Codex design; human review required by `AGENTS.md`
- **Depends on:** ADR 0004 and `janus.upgrade-preparation.v1`
- **Supersedes:** the host-only branch-based `janus-update` publisher after a
  successful live soak and a separately approved removal

## Context

The repository can prepare a coherent backup and exact candidate/rollback
artifacts, but the installed publisher is still outside the repository. It
force-reinstalls whichever tree `~/ai-workspace/janus` happens to contain and
offers a dirty-tree escape hatch. On 2026-08-29 that tree was clean but checked
out to the abandoned `claude/freeze-as-beacon-absorbs` branch, while the shared
installed copy remained 34 commits behind GitHub `main`.

The defect is not merely that the host script can select the wrong commit. It
also has no freshness check against the live ledger, no retained rollback
environment, no maintenance state, no atomic activation seam, and no closed
receipt tying the installed bytes to the backup that preceded migration.

Janus cannot solve this by reading its own approval ledger. A ruling is
evidence that a person decided; the caller remains responsible for authority.
The rollout module must therefore be safe to invoke only after external human
authorization and must never query Janus for permission.

## Decision

Janus owns one repository script, `scripts/apply_upgrade.py`, whose interface
accepts an absolute preparation bundle, live database, install root, and active
environment path. Its default mode is preflight. Mutation requires an explicit
`apply` subcommand plus `--yes`; that confirmation acknowledges effects but is
not an authorization mechanism.

The implementation is a deep module behind that interface:

1. validate the closed preparation manifest, exact candidate and rollback
   artifact hashes, bundle privacy, repository commit, existing install record,
   wrapper target, and absolute path relationships;
2. prove the current live ledger still matches the prepared backup by taking a
   new coherent SQLite snapshot; any intervening gate, ruling, observation, or
   audit row makes the preparation stale and refuses rollout;
3. install candidate and rollback wheels with `--no-index --no-deps` into new,
   commit-addressed environments before entering maintenance;
4. acquire the rollout lock, redirect the ordinary active-environment path to a
   fail-closed maintenance environment, and refuse while any process still has
   the database family open;
5. repeat freshness proof under maintenance, then deliberately repair the live
   directory/database family to `0700`/`0600`;
6. migrate through the staged candidate, require unchanged ledger counts and
   canonical content digests, and require the exact rollback environment to
   read the migrated database with the same content;
7. atomically repoint the active environment to the candidate, atomically write
   the installed provenance record, and publish a closed
   `janus.rollout-receipt.v1` receipt;
8. restore the prior active environment and provenance record on handled
   failure. A durable in-progress journal makes crash recovery explicit rather
   than guessing which step ran.

The first rollout may encounter a real directory at the active path rather
than a symbolic link. It preserves that environment under a private legacy
path before creating the activation seam. Later candidate/rollback switches
are single-entry atomic symlink replacement.

The receipt records only observed effects and exact identities. Its semantics
state that authority is external to Janus and that the receipt is not
authority. It never copies a gate question, ruling, or operator identity.

Database restore is deliberately not automated. Code rollback selects the
already-tested rollback environment; restoring the prepared database would
discard legitimate rows written after backup and remains a separate human-
approved recovery action.

## Compatibility window

Migration 0003 is additive enforcement. The rollback wheel packages only 0001
and 0002 but has already been exercised against a 0003 database. During the
mixed-version window, old code can still read the database; an unsafe old
attempt to create a bound ruling without its digest is rejected by the new
SQLite trigger. The migration never removes an old shape.

The host-only updater remains present during the first soak as an explicitly
deprecated escape path. It is not removed in this change. Removal is a later
contract step after the receipt-bound path is production-proven and no caller
depends on branch-based installation.

## Consequences

- Installation identity is derived from artifact digests and commits, not a
  mutable checkout name.
- A preparation bundle is intentionally short-lived. Normal Janus activity
  makes it stale and forces a fresh backup instead of silently widening RPO.
- The active CLI is unavailable briefly under a clear maintenance refusal; a
  failed or crashed rollout cannot masquerade as a healthy old install.
- The install root gains commit-addressed candidate and rollback environments,
  a private rollout lock/journal, an atomic active pointer, and durable receipts.
- Actual application against the mickey ledger remains RED: human approval,
  quiescence, exact-head verification, and post-deploy observation are still
  required.

## Alternatives considered

- **Keep installing the current checkout.** Rejected: branch and working-tree
  identity are mutable and do not bind the prepared artifact.
- **Trust a successful preparation indefinitely.** Rejected: the live ledger is
  active; one later gate makes the backup incomplete as a recovery point.
- **Switch code and let the first ordinary command migrate.** Rejected: schema
  mutation would occur outside the maintenance receipt and may surprise a
  reader who believed it was only listing gates.
- **Restore the backup automatically on failure.** Rejected: it could erase
  valid post-backup decisions. Code rollback is safe; data rollback is a new
  destructive decision.
- **Read a Janus approval before applying.** Rejected categorically: that puts
  Janus in its own permission path and violates the governing invariant.
- **Delete the host updater immediately.** Rejected: migration contracts only
  after the replacement has been exercised successfully and usage is zero.
