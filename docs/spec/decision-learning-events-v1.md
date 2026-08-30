# Decision-learning events v1

M5 stores decision-learning evidence as typed `audit_events`. This reuses the
ledger's existing append-only and stable-export boundaries: the event `detail`
is canonical JSON, and `janus export` preserves that exact string inside the
digest-protected gate record.

These events describe what was known and how a human later ruled. They are not
predictions, policy, delegations, or execution authority.

## Decision context

`janus context` may append `verb=decision_context` only while the gate is open.
Its detail is a `janus.decision-context-event.v1` envelope:

```json
{
  "schema": "janus.decision-context-event.v1",
  "context": {
    "schema": "janus.decision-context.v1",
    "project": "janus",
    "action_class": "merge",
    "environment": "test",
    "facts": {
      "reversible": true,
      "rollback_verified": null,
      "tests_passed": true,
      "non_author_reviewed": true,
      "security_sensitive": false,
      "money": false,
      "legal": false,
      "live_data": false,
      "public_effect": false,
      "infrastructure": false
    },
    "evidence_refs": ["github:Janus/pull/23"]
  },
  "context_sha256": "<sha256 of canonical context bytes>"
}
```

All ten fact keys are always present. Values are `true`, `false`, or `null`;
`null` means unknown and is never interpreted as false. Project and action
class are lowercase labels. Environment is `local`, `test`, `production`, or
`unknown`. Evidence references identify evidence without copying raw logs,
chat, artifacts, executable commands, or secrets into the learning record.

The digest uses `janus.canonical-json.v1`, defined by the export v1 contract.
Multiple snapshots may be appended while the gate remains open. History is
retained and the newest snapshot is the one a subsequent feedback event links.
The database refuses context after any terminal event, preventing hindsight
backfill from masquerading as pre-decision evidence.

## Human feedback

`janus decide` may append `verb=decision_feedback` in the same transaction as
an `approved` or `refused` human ruling. Its detail is:

```json
{
  "schema": "janus.decision-feedback.v1",
  "context_event_id": 42,
  "context_sha256": "<the cited context digest>",
  "outcome": "approved",
  "reason_codes": ["tests.pass", "review.non_author"],
  "counterfactual": "A failed required check would change this decision."
}
```

Reason codes are lowercase labels. A counterfactual is optional, but cannot be
recorded without at least one reason code. Structured feedback requires a
pre-ruling context; otherwise Janus refuses the whole ruling transaction and
leaves the gate open. A ruling without structured feedback remains supported
for compatibility and is explicitly reported as not learnable.

Database triggers require the feedback actor and outcome to match the human
ruling, require the cited context event and digest to belong to the same gate,
and allow only one feedback event for a ruling. Human provenance remains in the
existing `rulings` row; predictions and future delegated verdicts may not use
this event type.

## Compatibility and export

No member was added to `janus.gate.v1`. Both event types travel through the
existing ordered `audit_events` collection, so export v1 consumers that treat
unknown audit verbs as data remain wire-compatible. Consumers that construct a
learning corpus validate the event schema and context digest and join feedback
only to the cited earlier context. Missing context remains missing; old rulings
must not be backfilled as though their facts were captured before the decision.
