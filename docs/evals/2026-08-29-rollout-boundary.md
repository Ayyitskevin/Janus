# Rollout-boundary live measurement — 2026-08-29

## Question

Does mickey already have a repository-owned, exact-artifact deployment path,
or is a receipt-bound rollout module solving a measured operational gap?

This is a read-only census. It did not run the updater, change the installed
environment, alter permissions, migrate the ledger, or rule on a gate.

## Result

All 20 probes confirmed the proposed change addresses current state rather than
a hypothetical future:

| # | Observation | Live result |
|---:|---|---|
| 1 | `janus` wrapper selects the installed copy | yes, `~/.local/share/janus/venv/bin/janus` |
| 2 | publisher lives outside the repository | yes, `~/.local/bin/janus-update` |
| 3 | publisher defaults to a mutable checkout | yes, `~/ai-workspace/janus` |
| 4 | publisher has a dirty-tree escape hatch | yes, `JANUS_ALLOW_DIRTY` |
| 5 | publisher force-reinstalls that checkout | yes, pip `--force-reinstall` |
| 6 | default checkout is not `main` | `claude/freeze-as-beacon-absorbs` |
| 7 | default checkout is not PR #13's candidate | `a25abf247d0e1c10b584e4fc9cd4e9490a103daf` |
| 8 | default checkout is clean | yes; the defect is branch identity, not dirt |
| 9 | installed provenance record exists | yes |
| 10 | installed commit matches the prepared rollback | `6b62110e9c918c67219ab0b45dbf4dd9937d6620` |
| 11 | installed package version | `0.1.0` |
| 12 | PR #13 candidate descends from installed commit | yes, `d1d4ed87…` |
| 13 | GitHub `main` descends from installed commit | yes, `49e07f17…` |
| 14 | preparation explicitly did not deploy | `deployment_performed=false` |
| 15 | all retained artifact hashes recompute | 4 of 4 |
| 16 | live storage requires reviewed repair | directory `0775`, database `0644` |
| 17 | live migrations predate the candidate | 0001, 0002 |
| 18 | live ledger advanced after preparation | +1 gate, +3 options, +1 audit event |
| 19 | repository `main` packages migration 0003 | yes |
| 20 | repository already has a rollout command | no |

## Decision pressure

The current publisher can install a clean but wrong branch, so “refuse dirty”
is not a sufficient admission check. The real ledger also advanced within
minutes of preparation, proving that a bundle must be freshness-bound at
rollout rather than treated as a durable authorization token.

The replacement therefore consumes exact local artifact hashes, stages both
directions, refuses any live content drift, and writes installed provenance
only after candidate migration and rollback-reader verification. It does not
remove the old updater in the same change; production use and a caller census
must precede that contract step.

## Reproduction

The probes use only `git`, file metadata/hashes, the installed distribution
metadata, and an SQLite `mode=ro&immutable=1` inspection after confirming no
WAL/SHM files existed. The exact command and observed output are recorded in
Athena issue MWS-52 and the session handoff; live counts are intentionally not
promoted to a permanent project invariant.
