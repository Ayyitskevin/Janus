# Board observation freshness evaluation

## Decision under test

The board derives `landed`, `not yet`, `broken`, `delivered`, and `not landed`
from an observation taken at a specific time. Before this change it displayed
only the age of the gate or ruling. That made a status backed by old evidence
look current and violated the repository contract that a stale check must be
readable as stale.

The change does not create a stale/fresh cutoff, expire evidence, reorder the
queue, rerun a check, or change ledger/export state. It places `observed <age>`
on the status row's detail line using the same observation query that derived
the status. `unmeasured` and `unchecked` continue to carry no observation age.
A decay-check revision also starts a new measurement epoch: the predecessor's
result remains in append-only history but cannot describe the effective check,
even if its command text is later reused or the wall clock moves backward.

## Evidence before implementation

The governing evidence is local rather than an open-web convention:

- `AGENTS.md` requires stale check results to be readable as stale.
- ADR 0001 defines checks as observations taken at a moment, never state.
- ADR 0002 rejects a new HTTP transport until a second measured caller exists,
  so an HTTP surface was not substituted for this display defect.
- An aggregate-only `mode=ro` query of the live ledger found eight latest
  observations. The oldest was 431,977 seconds old (just under five days),
  while the board displayed none of their ages. The main database SHA-256 was
  `816812f4c5aa37a312d603cd6ef5b86c88c5de8b150f48409e62f69450213398`
  before and after. SQLite materialized an empty WAL and a shared-memory
  coordination file during the read; no ledger row or database byte changed.

## Twenty-four-history CLI sweep

The sweep used a real temporary migrated ledger and the real `janus board
--all` command. Gate, ruling, and observation times were deliberately different
so one timestamp could not accidentally stand in for another.

| Case | Gate history | Status/board treatment | Observation age shown |
| ---: | --- | --- | --- |
| 1 | open 1d, no check | unmeasured | none |
| 2 | open 2d, check never run | unchecked | none |
| 3 | open 3d, decay passed now | landed | 0m |
| 4 | open 8d, decay passed 1d ago | landed | 1d |
| 5 | open 14d, decay failed normally now | not yet | 0m |
| 6 | open 30d, decay failed normally 7d ago | not yet | 7d |
| 7 | open 4d, check broke now | broken | 0m |
| 8 | open 21d, check broke 10d ago | broken | 10d |
| 9 | open 45d, decay passed 20d ago | landed | 20d |
| 10 | open 60d, decay failed normally 20d ago | not yet | 20d |
| 11 | open 90d, check broke 30d ago | broken | 30d |
| 12 | open 120d, no check | unmeasured | none |
| 13 | approved 3d, no delivery check | counted, not listed | none |
| 14 | approved 4d, delivery never checked | unchecked | none |
| 15 | approved 6d, not landed now | not landed | 0m |
| 16 | approved 12d, not landed 3d ago | not landed | 3d |
| 17 | approved 20d, delivery check broke 7d ago | broken | 7d |
| 18 | approved 40d, delivery check broke 30d ago | broken | 30d |
| 19 | approved 8d, delivered now | omitted as delivered | none |
| 20 | approved 15d, delivered 5d ago | omitted as delivered | none |
| 21 | approved 30d, not landed 14d ago | not landed | 14d |
| 22 | approved 60d, not landed 30d ago | not landed | 30d |
| 23 | approved 90d, delivery never checked | unchecked | none |
| 24 | approved 150d, no delivery check | counted, not listed | none |

Baseline emitted **0** explicit observation-age rows. The candidate emitted
**15**, exactly one for every listed history whose status was derived from an
observation. The nine unobserved, deliberately omitted, or count-only histories
remained blank. Representative output:

```text
  not yet     30d    <gate-id>     taste         case 06 open freshness
  observed    7d                   worsens →     branch divergence continues
  not landed  12d    <gate-id>     resource      case 16 approved freshness
  observed    3d                   check →       false
```

The invariant tests separately drive both decay and post-ruling delivery paths
through the CLI and fail if gate/ruling age is substituted for observation age.
A third end-to-end regression proves that revising a decay check returns the
board to `unchecked` and removes the predecessor's age until the replacement
check runs.
