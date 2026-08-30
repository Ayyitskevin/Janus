# ADR 0006: Delegated decision progression

- Status: Proposed
- Date: 2026-08-30
- Supersedes: ADR 0001's rejection of auto-approval and policy engines
- Preserves: ADR 0001's append-only state, byte binding, and consumer
  re-verification requirements

## Context

Janus was deliberately founded as a ledger of decisions only a human could
make. That constraint protected the first useful system from becoming a policy
engine before it had a trustworthy ledger, an adopted operator surface, or a
measured corpus.

The operator has now set a different long-term direction: Janus should learn
how they decide, first advise in shadow, and eventually approve or deny within
authority they explicitly delegate.

The current corpus is not sufficient to activate that behavior. The live
measurement in `docs/evals/2026-08-30-autonomy-corpus-readiness.md` reports 39
human rulings, all approvals, and no refusals. A model trained on that history
would learn that the operator says yes, not where the operator's boundary lies.
The progression therefore starts with better records and falsifiable
evaluation, not a model call.

This direction contradicts two accepted statements:

- ADR 0001 rejects auto-approval and a policy engine at every maturity.
- The original vision says the record's entire value is that a person decided.

Those statements are superseded only as described here. Existing human gates
and rulings retain their original meaning permanently.

## Decision

Janus may learn and record a **delegated verdict** after four separately
reviewable maturity stages: context capture, shadow prediction,
recommendation, and scoped autonomy.

The governing sentence becomes:

> Janus records decision authority; it does not grant execution authority.

A human ruling and a delegated verdict are different durable facts. They must
use different storage, different provenance, and different rendering. A model
must never be attributed as the operator, and a consumer must never infer that
a machine verdict proves the operator personally reviewed the gate.

### Preserved invariants

1. **A gate is open or closed, never both.** A terminal human ruling or a
   terminal delegated verdict may close a future delegation-eligible gate, but
   never both. Existing gates remain human-only. Reversal remains a new gate
   citing the prior one.
2. **Every terminal decision binds bytes, not names.** A delegated verdict
   records the artifact digest it observed and the canonical decision-context
   digest it evaluated. Drift voids applicability; it never silently retargets
   the verdict.
3. **Decision provenance is explicit.** Human rulings remain in `rulings`.
   Model predictions and delegated verdicts are separate append-only records
   carrying model, prompt, policy, delegation, and input digests.
4. **Reading a decision is not execution authority.** Icarus or another
   consumer independently validates its own authorization, the artifact, and
   all execution preconditions. A Janus decision is never sufficient grounds
   to act.

### Vocabulary

- **Decision context**: a minimized, canonical snapshot of declared facts that
  were available before a decision. It contains facts and evidence pointers,
  not raw chat transcripts, secrets, executable checks, or copied artifacts.
- **Prediction**: an append-only machine output of `approve`, `deny`, or
  `abstain`. A prediction never changes gate state.
- **Recommendation**: a prediction shown to the operator before a human ruling.
  It remains non-terminal.
- **Delegation**: an explicit, human-created, expiring allowlist of decisions a
  machine may make. Absence, ambiguity, expiry, or drift means abstain.
- **Delegated verdict**: a terminal machine decision made under one valid
  delegation. It is not a human ruling and does not grant execution authority.

`deny` means a proposed action is outside an affirmative policy or violates an
explicit prohibition. Uncertainty is `abstain`, not `deny`. This distinction
prevents a model's lack of knowledge from quietly becoming policy.

## Eligibility is categorical, not a risk score

Janus will not collapse authority into a numeric risk score. Eligibility is a
closed set of facts evaluated by deterministic code before any model output is
considered. Initial facts include:

- project and action class
- environment (`local`, `test`, `production`)
- reversibility and verified rollback
- whether the change touches authentication, sessions, secrets, money, legal
  logic, live schema/data, public output, or infrastructure
- required test and non-author review evidence

Under current fleet doctrine, security-sensitive work, money or legal logic,
live schema or data migrations, infrastructure changes, and public or
irreversible effects remain human-only. A delegation cannot widen those
ceilings. `resource` gates are also never machine-decidable because a verdict
cannot supply the resource.

The eligibility guard runs before inference and after inference. The second
check binds the verdict to the exact delegation and context that were tested;
it is not permission to trust the model.

## Decision engine module

The external interface is deliberately small:

```text
evaluate(decision_context, delegation | none) -> prediction
```

The module owns canonicalization, eligibility, prompt construction,
model-output validation, abstention, and provenance. Callers do not assemble
prompts or interpret free-form model text.

Vulcan is a remote-but-owned dependency. The inference seam therefore has two
adapters: a loopback Vulcan adapter in production and an in-memory adapter in
tests. The model receives a minimized canonical document, no credentials, no
tools, and no direct database access. Its response must validate against a
closed schema before it can be recorded.

## Progression

### M5 — learnable records

- Add append-only decision-context snapshots with canonical JSON and SHA-256.
- Add optional structured feedback to human rulings: reason codes and a short
  counterfactual describing what would have changed the answer.
- Capture which pre-ruling context snapshot the feedback describes.
- Export these records without claiming that missing context is false.

No inference is introduced in M5. Existing commands and gates retain their
meaning.

The first implementation stores both records as typed append-only audit events,
specified in `docs/spec/decision-learning-events-v1.md`. This keeps export v1
wire-compatible while carrying their exact canonical bytes in its existing
digest-protected audit collection. Database triggers enforce pre-ruling context,
same-gate linkage, matching human provenance, and one feedback event per ruling.

### M6 — shadow prediction

- Add `janus predict <gate> --shadow`.
- Record `approve`, `deny`, or `abstain` with model, prompt, policy, input, and
  output digests.
- Keep predictions non-terminal and absent from the default operator board.
- Add chronological evaluation that compares predictions only with later
  human rulings.

M6 cannot close a gate, select an option for a human, invoke a check, or trigger
a consumer.

### M7 — recommendation

- Display the latest applicable prediction on `show` and the board.
- Show reason codes and uncertainty, not hidden chain-of-thought.
- Record the human ruling independently, then score agreement and calibration.
- Report denominators for false approvals, false denials, abstention, coverage,
  and per-scope drift.

Recommendation remains advisory. Removing the model must leave human decision
flow intact.

### M8 — scoped autonomy

- Add human-created, expiring, revocable delegations with closed scope and
  categorical ceilings.
- Add separate append-only delegated verdicts.
- Enforce database-level exclusivity between a human ruling and delegated
  verdict for one gate.
- Start with narrow GREEN scopes and an activation default of off.
- Fail closed to `abstain` on malformed output, missing facts, context drift,
  artifact drift, delegation drift, model drift, timeout, or service failure.
- Provide one command that disables autonomous verdicts without modifying
  historical records.

M8 activation requires a separately reviewed evaluation packet and explicit
human sign-off. Shipping dormant mechanics is not activation.

## Evaluation contract

Unit tests prove implementation, not judgment quality. Activation evidence
must use real, chronological decisions that were not available when the policy
or prompt was selected.

Every report states counts and denominators for:

- unsafe false approvals
- incorrect denials
- abstentions
- eligible coverage
- agreement by project and action class
- drift across time and policy/model versions

Thresholds are chosen before evaluating the activation set. No percentage over
a tiny denominator is presented as maturity. An abstention is a successful
safe outcome when the system lacks authority or evidence.

Synthetic refusals may exercise code paths but never count as evidence that the
system learned the operator. Real refusals and their reasons must be collected
before autonomous approvals can activate.

This shape is consistent with external primary guidance without treating that
guidance as fleet authority:

- [NIST AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/)
  calls for documented application scope, differentiated human/AI roles, and
  defined human oversight.
- [NIST AI RMF Appendix C](https://airc.nist.gov/airmf-resources/airmf/appendices/app-c-ai-risk-management-and-human-ai-interaction/)
  distinguishes autonomous decisions, human deferral, and advisory use, and
  identifies the frequency and rationale of human overrides as useful evidence.
- [OWASP LLM06: Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
  recommends minimizing model functionality, permissions, and autonomy while
  enforcing authorization again in downstream systems.
- [Optimal strategies for reject option classifiers](https://arxiv.org/abs/2101.12523)
  provides the selective-classification vocabulary behind treating abstention,
  coverage, and error risk as separate measurements.

## Threats and controls

- **Prompt injection in gate text or evidence.** Treat all decision context as
  untrusted data; use a closed schema and keep eligibility in deterministic
  code outside the prompt.
- **Training-data poisoning.** Only attributed human rulings are labels.
  Predictions and delegated verdicts never train as though the operator made
  them.
- **Human impersonation.** Separate tables, actors, exports, and UI language;
  no machine record may use `ruled_by` or render as a human ruling.
- **Stale delegation replay.** Bind the delegation digest and expiry into every
  verdict and re-check them before recording.
- **Omitted facts.** Closed schemas distinguish `false` from `unknown`; unknown
  mandatory facts force abstention.
- **Model or policy drift.** Pin and record model, prompt, and policy digests.
  A changed identity returns the system to shadow until re-evaluated.
- **Confused deputy execution.** Janus never invokes Icarus and Icarus never
  treats a Janus read as sufficient authorization.
- **Silent self-expansion.** Models cannot create or amend delegations, policy
  ceilings, prompts, or their own activation state.

## Consequences

Accepted:

- Janus becomes more than a human queue, but only through explicit delegation
  and visible provenance.
- The schema grows several append-only record types rather than overloading the
  existing `rulings` table.
- Early coverage will be low because abstention is preferred to guessed scope.
- Useful learning requires the operator to record refusals and reasons, not
  just approvals.

Rejected:

- Treating a confidence score as authority.
- Re-labeling a model output as Kevin's ruling.
- Fine-tuning directly on raw gate text or private conversation history.
- Letting a model execute checks, call tools, mutate policy, or write the
  ledger directly.
- Enabling autonomous decisions in the same change that introduces their
  storage or evaluation machinery.

## Adoption and rollback

Human-only Janus remains the fallback at every stage. M5 is additive. M6 and M7
can be removed without changing gate state. M8 defaults off, and disabling it
prevents new delegated verdicts while leaving the append-only record readable.

Each stage is a separate reviewed PR. The first activation is a separate RED
operation with an exact policy/model/context packet, a rehearsed rollback, and
post-activation measurement.
