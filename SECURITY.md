# Security policy

## Supported versions

Janus is pre-1.0 and is not published as a hosted service. Security fixes target
the current `main` branch; older commits and locally installed copies may lag and
are not maintained as separate supported releases. `janus doctor` identifies the
copy and migrations currently running, but its output is evidence, not an
instruction to upgrade.

## Reporting a vulnerability

Do not put exploit details, real gate text, locators, stored commands, rulings,
credentials, private paths, or operator metadata in a public issue.

Use GitHub's private **Report a vulnerability** flow when it is available for
this repository. If that flow is unavailable, contact the repository owner
privately through the contact information on their GitHub profile. A public
issue may request a private contact channel only if it reveals no vulnerability
details or sensitive metadata.

Include the affected commit or installed version, the smallest synthetic
reproduction, the expected refusal or invariant, and the observed behavior.
Do not run a stored check from an untrusted report and do not attach a live
ledger. There is no promised response-time SLA; please avoid public disclosure
until the maintainer has acknowledged the report and coordinated a safe fix.

## Security boundary

Janus is a local, single-OS-user tool. Its SQLite ledger can contain sensitive
decision metadata. Owner-only filesystem permissions are the access-control
boundary; loopback is not authentication, and mutually untrusted processes
running as the same user are outside the threat model. Janus has no supported
remote bind, multi-user authorization, or cloud-hosted mode.

Decay and delivery checks are executable text. They run only after their exact
terminal-safe representation is displayed and the operator confirms it (or
explicitly supplies `--yes`). No listing, export, or diagnostic command should
silently execute one. Remote or lower-trust writers must never be allowed to
store commands for later execution.

The stable export format proves record and document integrity, not origin,
authenticity, correctness, or permission to act. A ruling is historical evidence
about exact bytes; every consumer independently re-verifies its own authority
and current preconditions.

For the complete assumptions and known limits, read
[the threat model](docs/THREAT_MODEL.md). Repository changes and green checks do
not authorize operations against an installed ledger.
