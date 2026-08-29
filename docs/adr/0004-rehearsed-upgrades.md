# ADR 0004: Upgrades are prepared as reversible evidence before deployment

- **Status:** Proposed; becomes Accepted when this human-reviewed change merges
- **Date:** 2026-08-29
- **Deciders:** Codex design; human review required by `AGENTS.md`
- **Supersedes:** the untracked host-only `janus-update` preparation path

## Context

Janus now refuses the broad storage modes inherited by the installed M1 ledger.
That is the correct ordinary-command boundary, but it exposes an operational
gap: the host-only `janus-update` script force-reinstalls from a checkout and
records the installed commit without first creating a coherent database backup,
rehearsing forward migration, or preserving an independently installable
rollback artifact.

Permission repair and live migration are human-gated changes to high-integrity
state. A mature repository can still own everything needed to make that later
decision informed and reversible without performing it.

## Decision

Janus owns a non-deploying preparation operation and a versioned manifest. The
operation accepts an exact live database path, a new private output path, and
the full commit SHA of the currently installed rollback version. It refuses a
dirty candidate checkout and builds both candidate and rollback artifacts from
the committed Git trees.

SQLite's online backup API creates one coherent private backup. Only a copy of
that backup is opened by candidate code, allowing forward migrations to run in
rehearsal. The tool requires integrity, migration-prefix preservation, and
unchanged row counts plus canonical content digests across gates, options,
rulings, observations, check revisions, and audit events. It then opens the
migrated copy with a newly installed rollback wheel and requires the same
integrity, counts, and content digests. The exact wheels and source
distributions remain in the bundle.

The output tree is private throughout and published atomically only after all
checks pass. Failure cleans the complete staging tree and never leaves a path
that resembles a successful bundle. The manifest explicitly records
`deployment_performed: false`.

Preparation deliberately admits an existing database whose modes are broader
than Janus's current `0700`/`0600` contract: creating the safe backup is the
first step out of that legacy state. It still refuses symlinks, hard links,
wrong owners, unsafe ancestor replacement, and database identity change. It
reports the observed modes before and after rather than silently repairing
them.

## Consequences

- A future permission repair or install can start from a coherent backup and
  exact candidate/rollback artifacts rather than an ad hoc checkout.
- Forward-only migrations remain forward-only; rollback means reinstalling old
  code that can read the migrated schema, or restoring the preserved database.
- Ambient Python import paths and user packages cannot substitute different
  Janus code during rehearsal.
- Preparation may use SQLite WAL/SHM coordination but performs no logical live
  writes. It never chmods, installs, migrates live data, restarts, or deploys.
- The operator still must verify that `--rollback-commit` matches the installed
  version. A Git ancestry check proves relationship, not deployment identity.
- Build isolation may obtain the pinned build backend declared by the selected
  source tree. Artifact hashes record the resulting bytes; v1 does not claim a
  reproducible or hermetic build.

## Alternatives considered

- **Keep the host-only force-reinstall script as the whole upgrade path.**
  Rejected: it has no coherent backup, rehearsal, or retained rollback wheel.
- **Migrate or chmod the live ledger inside preparation.** Rejected: that would
  cross the human gate and turn a reversible evidence step into deployment.
- **Copy the database, WAL, and SHM files directly.** Rejected: unrelated file
  copies are not one coherent SQLite snapshot.
- **Trust the currently installed virtual environment for rollback.** Rejected:
  the install step may replace it. The bundle instead builds and exercises a
  rollback wheel from exact committed bytes.
- **Require the legacy ledger to pass current privacy checks before backup.**
  Rejected: it would make the only safe escape path unavailable to the exact
  installed state that needs it. Identity hazards remain hard refusals.
