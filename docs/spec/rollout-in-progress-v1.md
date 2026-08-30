# Rollout recovery journal v1

`scripts/apply_upgrade.py apply` writes one private
`janus.rollout-in-progress.v1` journal before changing the active environment.
The journal is a recovery descriptor, not a success receipt and never evidence
of authority. Its closed JSON contract is
[`rollout-in-progress-v1.schema.json`](rollout-in-progress-v1.schema.json).

Every path is absolute. Code also enforces the relationships JSON Schema cannot:
the active, installed-record, and maintenance paths are fixed children of the
named install root; release paths are the exact commit-addressed children of
`releases/`; a preserved directory is the exact recorded inode under `legacy/`;
and any success receipt is a direct child of `receipts/`. A symlink predecessor
must resolve to the exact rollback release while retaining its raw link text so
relative-link semantics can be restored.

The journal records both exact `INSTALLED` states as bytes, mode, path, and
SHA-256. It also records both release markers, the prepared database backup,
and the repository commit whose recovery implementation must be used. Recovery
therefore never reconstructs provenance from a commit name or trusts a path
merely because it appears in JSON.

## Reconciliation outcomes

`apply_upgrade.py recover` acquires the existing rollout lock, validates the
entire descriptor, and reports one of two outcomes:

- **complete forward:** an exact, schema-valid success receipt exists and the
  active environment plus installed provenance match it. The only remaining
  effect is removal of the stale journal. The apply path uses the same commit
  point: a receipt-publication or journal-cleanup exception preserves matching
  candidate state and refuses a second rollout attempt.
- **restore prior code:** no success receipt exists. The exact predecessor and
  prior installed provenance are restored, then the journal is removed only
  after both postconditions are rechecked.

Inspection is the default. Mutation requires `recover --yes`, which revalidates
the journal digest and all live identities under the lock. An unknown active
path, replaced legacy inode, changed provenance record, changed release marker,
unexpected receipt, invalid schema, or dirty/wrong recovery checkout refuses
without changing the journal or runtime state.

Recovery never copies the prepared database backup. A candidate migration may
already be durable when code recovery begins, and the rehearsed rollback reader
is the compatibility mechanism. Restoring data could discard legitimate rows
and remains a separate, explicitly authorized operation.
