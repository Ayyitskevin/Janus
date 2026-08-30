## What changed

<!-- Describe the behavior or documentation change and why it belongs in Janus. -->

## Contract impact

<!-- Name affected invariants/interfaces. State explicitly if there is no contract impact. -->

- [ ] I read `docs/VISION.md`, `docs/THREAT_MODEL.md`, and the relevant ADRs for any domain-model or trust-boundary change.
- [ ] This change preserves: one terminal gate state, byte-bound rulings, and “a ruling is not authority.”
- [ ] This change adds no automatic approval, inferred consent, priority score, policy engine, artifact storage, or remote write path.
- [ ] I used only synthetic, non-sensitive fixtures and removed gate text, locators, stored commands, credentials, private paths, and operator metadata from the PR.

## Verification

<!-- Replace this comment with observed commands, exit codes, test count, and any skips. -->

- [ ] `./scripts/check.sh`
- [ ] `git diff --check`
- [ ] New invariant coverage fails when the protected behavior is removed, or this PR explains why no regression test applies.
- [ ] Exact-head CI is green.

## Review and operations

- [ ] I identified any state-machine, binding, trigger, migration, attribution, export, or cross-module-seam change that requires independent and human review.
- [ ] I documented compatibility limits, skipped verification, and follow-up work.
- [ ] This PR does not treat merge as authority for permission repair, migration, installation, stored-check execution, activation, recovery, restore, or deployment.
