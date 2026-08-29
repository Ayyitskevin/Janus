# Unverifiable bound rulings — live-ledger migration evaluation

Date: 2026-08-29
Last measured: 2026-08-29T17:49:31Z

## Decision under test

When an `approved` or `refused` ruling belongs to a gate that has a binding,
Janus must refuse to store the ruling if it cannot derive the binding digest at
ruling time. Existing invalid history remains readable and is surfaced by
`show` and `doctor`.

This is record validation, not execution authorization. Janus still does not
answer whether a consumer may act.

## Assumptions and evidence

1. **A bound ruling with no digest violates the product contract.** Verified
   against `AGENTS.md`, `docs/VISION.md`, ADR 0001, and the threat model. Each
   says a ruling binds the bytes observed at ruling time; none permits a bound
   ruling whose digest is `NULL`.
2. **Refusing the invalid write does not put Janus in the permission path.**
   Verified against ADR 0001's boundary: Janus records a binding; the consumer
   independently enforces its own authority and preconditions. Rejecting a
   malformed record grants nothing.
3. **The migration is compatible with the real ledger.** Measured on a SQLite
   backup of the live mickey ledger, never on the live file itself. The backup
   contained 41 real gates, including 37 bound gates.

No open-web research was used. The choice is governed by Janus's repository
contract and real fleet data, not by an external product convention.

## Live measurement

The measurement opened `/home/kevin-lee/.janus/janus.db` read-only, copied it
with SQLite's backup API into a temporary directory, and applied the candidate
migration only to that copy.

| Measure | Observed result |
| --- | ---: |
| Real gates evaluated | 41 |
| Bound gates evaluated | 37 |
| Approved gates | 21 |
| Open gates | 9 |
| Superseded gates | 11 |
| Open bound gates whose bytes are currently unverifiable | 1 |
| Existing bound rulings with `bound_sha256 = NULL` | 0 |
| Ledger table row counts changed by migration | No |
| New trigger installed on copied ledger | Yes |
| Schema before | `0001_initial,0002_check_revisions` |
| Schema after on copy | `0001_initial,0002_check_revisions,0003_bound_rulings_require_digest` |

The one affected open gate is the live condition that motivated PR #3. After
this change it remains open rather than becoming a terminal ruling that binds
nothing. No existing ruling is rejected or rewritten, and append-only history
is preserved.

## Mixed-version and rollback proof

The current `main` code at `e952ebee9b9e397c4079f9f40037116b72609b90`
was run against the migrated disposable copy. It successfully wrote one valid
unbound approval and one valid bound refusal. When the same code attempted the
old failure mode—a ruling on a bound artifact that had disappeared—the new
database trigger refused the write and the gate stayed open.

That makes an application rollback compatible with the forward migration: the
pre-change code can continue reading and writing valid records after `0003` is
present. Janus migrations are forward-only, so there is deliberately no down
migration. Removing the trigger would restore an invalid record shape and would
require a separate reviewed forward migration and human approval. Before any
live installation, the operator still takes the normal timestamped database
backup; this evaluation did not install code or migrate the live ledger.

Inline-text bindings remain rulable. They have no external file or Git object
that can disappear, and the ruling records their existing immutable digest.
The new refusal applies to external bindings whose bytes cannot be re-derived.

## Verification criteria

- CLI approve and refuse both fail when a bound artifact cannot be read.
- The core refuses the same write when called without the CLI.
- SQLite refuses a direct invalid insert.
- The gate remains open and no ruling row is written.
- A drifted but readable artifact can still be ruled on with explicit `--yes`,
  and the ruling stores the digest of the bytes actually reviewed.
- A legacy digestless ruling remains visible as `NONE RECORDED`.
- An inline-text binding can still be ruled on and records its digest.
- A non-editable package install includes and applies migration 0003.
