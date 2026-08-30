# Shadow predictions v1

M6 adds non-terminal predictions behind an explicit `--shadow` acknowledgement.
A prediction can be `approve`, `deny`, or `abstain`; none of those words changes
gate state, selects an option for a human, runs a stored check, or authorizes a
consumer.

## Command and ordering

```bash
janus predict <gate> --shadow --model simple
janus shadow-report
```

Prediction requires an open gate and at least one decision-context snapshot.
Migration 0005 refuses a `decision_prediction` audit event after any terminal
event and requires its context event and digest to belong to the same gate.
If a new context arrives during inference, the recorded result is `abstain`
with `context.drift`. If the gate closes during inference, nothing is recorded.

Predictions are typed canonical JSON in append-only audit events, so the stable
export v1 gate record already carries their exact bytes. They are deliberately
absent from the default board and from `show`; recommendation is a later,
separately reviewed stage.

## Deterministic eligibility

Eligibility runs before inference. The model is not called when any required
fact is unknown, when required test/review/rollback evidence is false, or when
the context names a current human-only ceiling:

- `irreversible` and `resource` gate kinds;
- `production` or unknown environments;
- security-sensitive work, money, legal logic, live data/schema, public
  effects, or infrastructure;
- work that is not reversible with verified rollback, passing tests, and a
  non-author review.

An ineligible context records `abstain` plus every categorical blocker. Janus
does not compress these rules into a risk score. Eligibility runs again after
inference; any change becomes abstention.

## Prediction record

A `janus.decision-prediction.v1` event contains:

- mode (`shadow`), verdict, reason codes, and a short summary;
- exact context event id and SHA-256;
- canonical input, policy, and prompt SHA-256 values;
- whether inference was attempted;
- adapter, public model alias, provider, and the SHA-256 of Vulcan's exact
  public model-catalog record;
- SHA-256 of the raw assistant-content bytes, or a bounded failure code.

Raw model output and chain-of-thought are not stored. Invalid JSON, duplicate
keys, extra fields, empty output, oversized output, invalid provenance,
timeouts, HTTP errors, redirects, and service failures all fail closed to a
recorded abstention. There is no retry or fallback.

## Vulcan adapter boundary

The production adapter accepts only an explicit loopback HTTP origin, disables
environment proxies and redirects, caps response bytes, and uses Vulcan's
strict JSON-schema response format. It also refuses every non-Ollama provider
after reading local catalog metadata and before submitting the prompt; Vulcan's
hosted aliases cannot move decision context off-machine in M6. The implemented request shape is grounded
in Vulcan 1.0.0's typed request contract at commit `8a37c43`:

<https://github.com/Ayyitskevin/Vulcan/blob/8a37c43f767e2019dd300009a7cde2eefff0f836/src/vulcan/schemas.py#L86-L140>

A live 2026-08-30 probe against `127.0.0.1:8140` confirmed the field is accepted.
The `simple` alias consumed the small probe's 128-token cap without returning
content, and did the same at 512 tokens. The same bounded input at 2,048 tokens
returned a schema-valid 257-character response with `finish_reason=stop` in
about 68 seconds. Those measurements set the CLI defaults to 2,048 tokens and a
90-second single-call timeout. Empty content remains a first-class abstention
path rather than an implicit retry.

Vulcan intentionally exposes a public alias and provider but not the
provider-native model name. `catalog_sha256` therefore identifies the exact
public discovery record, not hidden model weights. That is sufficient for
shadow comparison and explicitly insufficient for autonomous activation. M8
remains blocked on a stronger immutable model identity or an equivalent signed
deployment identity.

## Chronological evaluation

`shadow-report` selects the latest pre-ruling prediction per gate and compares
it only with a later `approved` or `refused` human ruling. It reports raw counts
and denominators for:

- abstention and non-abstaining coverage;
- agreement among non-abstaining predictions;
- unsafe false approvals among labeled human refusals;
- incorrect denials among labeled human approvals;
- total human approval/refusal labels and predictions not evaluated.

Older predictions for the same gate remain history but are not counted as
independent samples. Human rulings without predictions remain visible in the
human-label total rather than disappearing from the denominator story. The
report is evaluation evidence, never an activation decision.
