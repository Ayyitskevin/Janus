# Contributing to Janus

Janus has one governing rule:

> Janus records pending authority; it does not grant authority.

Contributions are welcome when they preserve that distinction and keep the
local-first interface inexpensive for agents and operators to use.

## Before changing the model

Read [the vision](docs/VISION.md), [the gate-model decision](docs/adr/0001-the-gate-model.md),
and [the threat model](docs/THREAT_MODEL.md) before changing gate state,
bindings, rulings, checks, exports, migrations, attribution, or a cross-module
seam. The repository contract in [AGENTS.md](AGENTS.md) applies to human- and
agent-authored changes alike.

The three invariants are:

1. A gate is open or closed, never both.
2. A ruling binds bytes, not names.
3. Reading a ruling is not authority.

Do not add automatic approval, inferred consent, human-bypassing risk scores,
a policy engine, priority integers, artifact storage, task tracking, or remote
writes. A proposal that needs one of those belongs in a design discussion, not
an implementation patch.

## Development setup

Janus supports Python 3.11 through 3.14. Create an isolated environment and
install the test dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Use only synthetic gate text, paths, identities, and credentials in tests and
examples. Never copy a live ledger into the repository or include real gate
questions, locators, stored commands, rulings, or operator metadata in an issue,
test fixture, log, or pull request.

## Verification

Run the complete repository gate from a clean checkout:

```bash
./scripts/check.sh
git diff --check
```

The gate lints and compiles the code, runs the invariant suite, builds the
source distribution and wheel, installs the wheel outside the checkout, checks
the CLI entry point, and compares packaged migrations with source. Record the
observed test count and command outcomes in the pull request; “should pass” is
not evidence.

Tests should exercise behavior through the same interface a caller uses and
should fail when the protected invariant is removed. Changes to the gate state
machine, binding semantics, append-only triggers, migration history, seat
attribution, export contract, or any cross-module seam require invariant-level
regression coverage, independent review, and human review before merge.

## Pull requests

Keep each change coherent and surgical. Explain:

- the behavior or documentation that changed and why;
- which invariant or public interface is affected;
- the exact verification commands and observed results;
- any skipped verification, compatibility limit, or follow-up;
- whether a migration, export contract, or cross-module seam changed.

Repository development never authorizes a live Janus operation. A merge does
not authorize permission repair, migration, installation, activation, stored
check execution, recovery, restore, or deployment; those remain separate
operator decisions with their own current-state verification.

Report security-sensitive findings through [the security policy](SECURITY.md),
not a public issue.
