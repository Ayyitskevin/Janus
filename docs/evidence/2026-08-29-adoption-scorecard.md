# Adoption scorecard — 2026-08-29

This is Janus's second dated adoption measurement. It updates the first
scorecard's most important finding — all eight gates had carried one declared
seat attribution — without rewriting that historical baseline.

## Measurement

Run on mickey against the live local ledger:

```bash
~/.local/bin/janus doctor
~/.local/bin/janus stats --json
```

`doctor` identified the executable as the installed copy under
`~/.local/share/janus/venv/lib/python3.12/site-packages/janus`, the ledger as
`~/.janus/janus.db`, migrations `0001_initial` and `0002_check_revisions`, and
12 open gates. It positively exercised both append-only triggers. The exact
scorecard was generated at `2026-08-29T21:25:15Z` over the interval from
`2026-08-24T12:56:06Z` through `2026-08-29T21:22:08Z`:

| Measure | Result |
| --- | ---: |
| Gates raised | 47 |
| Raised by `kevin-lee+codex` | 21 |
| Raised by `kevin-lee+claude-code` | 20 |
| Raised without a declared seat (`kevin-lee`) | 6 |
| Closed | 35 |
| Open | 12 |
| Human rulings (`approved`) | 21 |
| Non-ruling closures (`superseded`) | 14 |
| Delivery measurable / confirmed / unknown | 3 / 1 / 44 |
| Gates with decay checks | 31 |
| Gates with delivery checks | 3 |
| Gates with bindings | 43 |
| Gates with options | 31 |
| Gates with horizons | 0 |
| Observations | 11 |
| Gates ever checked | 8 |

The window is 462,362 seconds (5 days, 8 hours, 26 minutes, 2 seconds). Janus
reports raw counts and the denominator rather than inventing a weekly rate for
that short interval.

## What changed

The first scorecard found eight gates under one declared seat attribution. The
ledger now contains gate records under three attribution labels: Codex, Claude
Code, and no declared seat. The single-label finding changed; the distribution
alone cannot establish which processes or people used Janus.

The evidence also resists a flattering interpretation. Delivery is measurable
for only 3 of 47 gates, 44 remain unknown, only 8 gates have ever had a stored
check observed, and no gate has used a horizon. Those are current product and
workflow gaps, not zeros to omit.

## Boundaries

- This aggregate output contains no gate questions, locators, stored commands,
  or other potentially sensitive record content.
- Attribution identifies the OS user plus an optional declared seat; it does
  not prove which process or person typed a command.
- Gate creation does not prove the operator uses `janus board`; M2 remains open
  until that is measured by asking again.
- Gate creation does not prove a sibling independently verifies
  `janus.export.v1`; M3 remains open until a consumer actually ships.
- Reading a scorecard is evidence, never authority to act.
