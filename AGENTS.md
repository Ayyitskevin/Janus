# AGENTS.md — Janus repository contract

## Purpose

> Janus records decision authority; it does not grant execution authority.

This file narrows the fleet-wide autonomy contract for this repository. Explicit
user instruction and the fleet safety rules still take precedence.

## Working agreement

- Read `docs/VISION.md` and `docs/adr/0001-the-gate-model.md` before changing
  the domain model, the state machine, or anything touching bindings.
- The four invariants are not negotiable without a superseding ADR: a gate is
  open or closed and never both; every terminal decision binds bytes, not
  names; human rulings and delegated verdicts are never conflated; reading a
  decision is not execution authority.
- Autonomous decisions are allowed only under ADR 0006's explicit,
  human-created delegation envelope. Never add inferred consent, a scalar risk
  score that bypasses eligibility rules, machine attribution as a human,
  autonomous RED-class decisions, a priority integer, or storage of the
  artifact under decision.
- Treat the gate ledger, ruling records, binding digests, and migration history
  as high-integrity surfaces. Smallest reviewed change, plus an invariant-level
  regression test.
- Do not let Janus become sufficient permission to act. A consumer must
  independently verify the artifact and its own execution authority even when
  Janus records a valid delegated verdict.
- Adoption work is not optional follow-up. A change that makes the CLI harder to
  reach for an agent is a regression.

## Red and green boundaries

Green in an isolated checkout: documentation, tests, local code, reversible
fixtures, and an approved branch/PR workflow.

Human review required before merge for: the gate state machine, binding
semantics, append-only triggers, migration history, seat attribution,
delegation eligibility, decision provenance, model-output validation, any
export contract, and anything that would create a cross-module seam.

## Four invariants to re-check

- **State** lives in migrated SQLite. A ruling and its binding digest are the
  durable human record; a delegated verdict, its delegation, and their binding
  digests are the durable machine record. The artifact itself is never state
  here. Human and machine authority provenance must remain distinguishable.
- **Feedback** lives in structured errors, audit rows, `doctor`, and tests.
- **Deleting** a gate or ruling must fail — consumers cite them.
- **Timing**: decay checks are observations taken at a moment, never state
  transitions. A stale check result must be readable as stale.
