# Autonomy corpus readiness — 2026-08-30

## Question

Does the live Janus ledger contain enough real, pre-decision evidence to begin
training or activating autonomous approve/deny behavior?

## Assumptions under test

1. The existing human ruling history contains both approvals and refusals.
2. `kind` and free-text reasons are enough to reconstruct delegation
   eligibility without inventing facts.
3. Existing records distinguish production, security, money/legal, live-data,
   public-effect, reversibility, rollback, test, and review conditions.
4. The corpus is broad enough in time and outcome to evaluate behavioral
   agreement honestly.

## Method

The measurement queried the live mickey ledger read-only. It inspected all
human rulings, not a selected sample, and returned only aggregates. Gate
questions, reasons, locators, and other potentially sensitive text were not
copied into this evaluation.

```bash
db=/home/kevin-lee/.janus/janus.db
sqlite3 -readonly "$db" <<'SQL'
SELECT state, COUNT(*) FROM rulings GROUP BY state ORDER BY state;
SELECT g.kind,
       SUM(r.state = 'approved'),
       SUM(r.state = 'refused'),
       COUNT(*)
FROM gates g JOIN rulings r ON r.gate_id = g.id
WHERE r.state IN ('approved', 'refused')
GROUP BY g.kind ORDER BY g.kind;
SELECT COUNT(*),
       SUM(g.binding_sha256 IS NOT NULL),
       SUM(g.horizon IS NOT NULL),
       SUM(g.delivery_check IS NOT NULL),
       SUM(EXISTS (SELECT 1 FROM gate_options o WHERE o.gate_id = g.id)),
       SUM(length(trim(r.reason)) > 0)
FROM gates g JOIN rulings r ON r.gate_id = g.id
WHERE r.state IN ('approved', 'refused');
SELECT MIN(ruled_at), MAX(ruled_at), COUNT(DISTINCT substr(ruled_at, 1, 10))
FROM rulings WHERE state IN ('approved', 'refused');
SQL
```

The schema was also checked for explicit eligibility fields. None of these
columns exists on `gates`: project, action class, environment, reversibility,
security sensitivity, money, legal, live schema/data, public effect, verified
rollback, tests, review, reason codes, or counterfactual.

## Results

| Measure | Observed |
| --- | ---: |
| Human rulings | 39 |
| Approved | 39 |
| Refused | 0 |
| Artifact-bound | 36 |
| With free-text reason | 39 |
| With options | 29 |
| With horizon | 0 |
| With delivery check | 2 |
| Days containing a ruling | 5 |
| Structured delegation-eligibility fields | 0 |

By gate kind, the 39 human rulings are 33 `authority`, 3 `taste`, 2
`resource`, and 1 `irreversible`. There are no negative labels in any kind.

## Findings

1. **The outcome distribution cannot teach an approve/deny boundary.** A model
   can obtain perfect historical accuracy by always approving.
2. **`kind` is not a safety classification.** ADR 0001 deliberately defines it
   as why a human is needed. It cannot substitute for environment,
   reversibility, or RED-class facts.
3. **Free-text reasons are useful evidence but not a closed policy input.**
   Deriving missing safety facts from prose would turn model inference into the
   eligibility guard that ADR 0006 explicitly rejects.
4. **The time window is too narrow for drift measurement.** Five active days do
   not establish stable behavior across projects or changing conditions.
5. **The ledger is strong enough to anchor future labels.** Human actor, time,
   terminal outcome, free-text reason, and usually an artifact digest already
   exist. Additive context and feedback records can extend them without
   rewriting history.

## Decision supported by this evidence

Proceed with M5 context and feedback capture. Do not choose, train, benchmark,
or activate an autonomous model from the current corpus. Synthetic refusals may
test mechanics but must not be counted as evidence about the operator.

Re-run this evaluation after M5 has captured real approvals and refusals across
multiple eligible scopes. Activation thresholds belong in a later evaluation
packet and must be selected before that held-out packet is scored.
