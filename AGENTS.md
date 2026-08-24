# AGENTS.md — Janus repository contract

## Purpose

> Janus records pending authority; it does not grant authority.

This file narrows the fleet-wide autonomy contract for this repository. Explicit
user instruction and the fleet safety rules still take precedence.

## Working agreement

- Read `docs/VISION.md` and `docs/adr/0001-the-gate-model.md` before changing
  the domain model, the state machine, or anything touching bindings.
- The three invariants are not negotiable without a superseding ADR:
  a gate is open or closed and never both; a ruling binds bytes, not names;
  reading a ruling is not authority.
- Never add: auto-approval, risk scoring that bypasses a human, inferred
  consent, a policy engine, a priority integer, or storage of the artifact
  under decision. These are rejected at every maturity level, not deferred.
- Treat the gate ledger, ruling records, binding digests, and migration history
  as high-integrity surfaces. Smallest reviewed change, plus an invariant-level
  regression test.
- Do not let Janus enter the permission path. If a change would let another
  system treat a Janus read as sufficient grounds to act, it is wrong even if
  it is convenient.
- Adoption work is not optional follow-up. A change that makes the CLI harder to
  reach for an agent is a regression.

## Red and green boundaries

Green in an isolated checkout: documentation, tests, local code, reversible
fixtures, and an approved branch/PR workflow.

Human review required before merge for: the gate state machine, binding
semantics, append-only triggers, migration history, seat attribution, any
export contract, and anything that would create a cross-module seam.

## Four invariants to re-check

- **State** lives in migrated SQLite. A ruling and its binding digest are the
  durable record; the artifact itself is never state here.
- **Feedback** lives in structured errors, audit rows, `doctor`, and tests.
- **Deleting** a gate or ruling must fail — consumers cite them.
- **Timing**: decay checks are observations taken at a moment, never state
  transitions. A stale check result must be readable as stale.
