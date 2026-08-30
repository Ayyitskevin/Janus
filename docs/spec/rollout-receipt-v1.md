# Rollout receipt v1

`scripts/apply_upgrade.py apply` publishes one
`janus.rollout-receipt.v1` document only after candidate activation and all
postconditions succeed. The receipt is local operational evidence. It is not a
Janus ruling, does not claim that execution was authorized, and is never an
input that grants permission to another module.

The closed JSON contract is
[`rollout-receipt-v1.schema.json`](rollout-receipt-v1.schema.json). Unknown
properties are refused. Paths are absolute because recovery must not depend on
a later working directory.

## Required identities

- `preparation.manifest_sha256` binds the exact preparation receipt.
- Candidate and rollback commits plus wheel digests bind both installed
  environments.
- `preparation.backup_sha256` binds the coherent database recovery snapshot.
- `before` and `after` carry database integrity, migration checksums, every
  ledger-table count and canonical content digest, plus observed family modes.
- `target.active_environment` names the one pointer ordinary callers use.
- `legacy_environment`, when present, preserves the real directory displaced
  while the activation seam is introduced.

`steps` is an ordered list of completed effects. A successful document must
contain every required step. A handled failure does not publish a success
receipt; it leaves a private in-progress journal until restoration is verified.
Before maintenance mutates the active path, that journal durably records the
prior path kind, symlink target or reserved legacy path, and filesystem identity.
It also records the exact prior `INSTALLED` bytes as base64, their SHA-256 digest,
the original mode, and the absolute target path. The switch refuses if the active
path no longer has the recorded identity. A crash after candidate provenance is
written therefore leaves enough private, integrity-checked state to restore the
prior provenance exactly rather than reconstructing it from a commit alone.

## Freshness and timing

The preparation snapshot must equal the current live ledger immediately before
maintenance and again after ordinary entry is blocked. Equality covers
migration version/checksum pairs, counts, and canonical content digests for all
six ledger tables. File timestamps alone are neither necessary nor sufficient.

SQLite read coordination may create or update WAL/SHM files during freshness
proof. These are physical effects, not logical writes, and the complete family
is checked before and after. Successful rollout ends with directory mode `0700`
and every existing database-family file at `0600`.

## Recovery meaning

`rollback.code_environment` is the exact rollback wheel already proven to read
the migrated schema. Repointing to it is a code rollback and preserves rows.
`rollback.database_backup` is a more destructive recovery artifact: restoring
it can erase later rows and is never performed automatically by this command.

`semantics.authority` is always `external_to_janus`, and
`semantics.receipt_is_authority` is always `false`. A consumer may verify this
receipt as evidence, but it must establish its own authorization and
preconditions before acting.
