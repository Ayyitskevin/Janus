# Delivery evidence after a ruling — live-ledger measurement

- Date: 2026-08-29
- Candidate base: `2b96a16a10856bad5c31841de14823ae357c74c8`
- Ledger: the local production ledger, queried read-only
- Question: can Janus explain a post-approval artifact change using its existing
  delivery observations without changing a ruling, adding a second evidence
  model, or treating historical evidence as authority?

## Assumptions tested

1. A delivery observation is meaningful only after an approved ruling.
2. Delivery checks are useful beyond `resource` gates because any approved
   consumer action can fail to land.
3. Binding and delivery are independent evidence: a binding compares current
   bytes with ruling-time bytes, while a delivery observation reports only what
   its stored command observed at one moment.
4. Restricting the scorecard's consumer-action proxy to approved gates will
   remove false eligibility without discarding legitimate live evidence.

## Measurement

The sweep covered every approved gate in the live ledger: 21 cases.

| Population | Cases | Current result |
| --- | ---: | --- |
| Approved gates | 21 | 17 authority, 2 resource, 1 taste, 1 irreversible |
| Approved gates with bindings | 18 | 15 Git, 3 file |
| Bound gates matching ruling-time bytes | 16 | 15 Git, 1 file |
| Bound gates changed since ruling | 2 | both file bindings |
| Approved gates with delivery checks | 2 | both resource gates |
| Latest valid delivery observation passed | 1 | preserved by the candidate semantics |
| Latest valid delivery observation failed | 1 | preserved by the candidate semantics |
| Delivery observations made before approval | 0 | the new refusal discards no live evidence |
| Non-approved gate with a delivery check | 1 | superseded, never observed; currently inflates scorecard measurability |

Both changed bindings have no delivery check or observation. The candidate must
therefore keep both loud as unexplained drift. It must not retrospectively label
either one delivered.

The 17 approved authority gates currently have no delivery checks. This is an
adoption finding, not evidence that they were not acted on: their consumer
outcomes are unmeasured. General delivery wording makes that gap expressible
without inventing evidence.

## Re-runnable evidence

Installed scorecard at measurement time:

```bash
janus stats --json
```

Observed: 44 raised, 21 approved, 3 gates reported as consumer-action
measurable, and 1 confirmed. The third measurable gate is superseded.

Eligibility and observation sweep (identifiers and descriptions intentionally
omitted from this durable aggregate):

```bash
sqlite3 -readonly ~/.janus/janus.db "
WITH effective AS (
  SELECT g.id,
         COALESCE((
           SELECT command FROM check_revisions cr
           WHERE cr.gate_id=g.id AND cr.kind='delivery'
           ORDER BY id DESC LIMIT 1
         ), g.delivery_check) AS command
  FROM gates g
)
SELECT COALESCE(r.state,'open') AS state,
       COUNT(*) AS gates_with_delivery_check
FROM effective e
LEFT JOIN rulings r ON r.gate_id=e.id
WHERE e.command IS NOT NULL
GROUP BY COALESCE(r.state,'open');
"
```

Observed: 2 approved and 1 superseded.

```bash
janus doctor
```

Observed: exactly 2 approved gates reported as drifted. This command is a
read-only diagnostic apart from SQLite's ordinary connection housekeeping; it
ran no stored checks and changed no gate state.

## Decision supported by the measurement

Keep two dimensions rather than synthesize one verdict:

- **Binding:** exact ruling-time bytes live / changed / unverifiable.
- **Delivery:** no post-ruling observation / latest check reported pass / latest
  check reported fail.

A pass may support the account that an approved effect landed. It does not prove
causality, does not name the post-action bytes unless the stored check itself
does so, and grants no authority. A later artifact change remains indistinguish-
able until the delivery check is run again; CLI wording must state that limit.

No schema or export-v1 change is justified by this evidence.

The derived delivery status is also bound to the effective check command. A
passing observation from a superseded command remains in append-only history
but becomes ineligible as soon as `revise-check` changes what is being measured;
the replacement must actually run before it can report delivery.

## Candidate replay against a live-ledger backup

The implementation candidate was run against a SQLite backup of the same live
ledger. It did not open or migrate the production database and ran no stored
checks.

```bash
sqlite3 -readonly ~/.janus/janus.db ".backup '/tmp/<scratch>/janus.db'"
PYTHONPATH=src .venv/bin/python -m janus.cli --db /tmp/<scratch>/janus.db \
  show <each-drifted-gate>
PYTHONPATH=src .venv/bin/python -m janus.cli --db /tmp/<scratch>/janus.db \
  stats --json
PYTHONPATH=src .venv/bin/python -m janus.cli --db /tmp/<scratch>/janus.db doctor
```

Observed:

- Both approved drifted gates remained loud and were classified as having no
  valid post-ruling delivery observation.
- Neither ruling was called void; each remained evidence about its recorded
  bytes and explicitly did not follow the live artifact.
- The candidate scorecard reported 21 eligible approved gates, 2 measurable,
  1 confirmed, and 19 unknown.
- `doctor` still reported both approved drifts. It also retained PR #5's
  separate fail-closed report for one pre-existing unreadable open binding.
