# ADR 0002: The stable export is a read-only evidence snapshot

- **Status:** Proposed; becomes Accepted when this human-reviewed change merges
- **Date:** 2026-08-29
- **Deciders:** Codex design synthesis; human review required by `AGENTS.md`
- **Supersedes:** nothing

## Context

`janus list --state all --json` is a display implementation that other systems
already consume as if it were a contract. Beacon consequently copies Janus's
state vocabulary, parses `janus list --help` to detect drift, and receives only
the five observations that `get_gate` keeps for display. Opening Janus's live
SQLite database would replace those accidental contracts with a worse one: it
would bypass the module that owns the ledger.

M3 promises a versioned, digest-verified record that another system can read
without a live-database reach-through. The interface must keep the governing
line intact: reading a ruling is evidence, not authority.

## Decision

Janus publishes one stable operation:

```text
janus export             # complete ledger snapshot
janus export <gate-id>   # one gate, in the identical record shape
```

The operation emits the `janus.export.v1` canonical JSON envelope documented
in `docs/spec/export-v1.md`. It is logically read-only by construction: it
opens the ledger with SQLite `mode=ro` and `query_only`, applies no migration,
writes no database row or audit event, executes no stored check, and does not
inspect a bound artifact. It never creates the main database.

SQLite's safe snapshot protocol for a WAL-mode database may nevertheless
materialize `-wal` and `-shm` coordination files beside a cleanly closed main
database. Those files are physical coordination state, not ledger mutations.
Janus admits export only inside its private directory boundary and post-checks
the complete family after the connection closes, so SQLite cannot silently
weaken the exact `0600` file contract. This side effect is explicit because
calling it nonexistent would make the read-only claim misleading.

The envelope declares Janus's exact gate-state vocabulary and the two semantic
facts consumers previously copied: whether each state is terminal and whether
it records a human ruling. Every gate record includes complete options, check
revisions, observations, and gate audit events. A point export and its record
inside a complete export are byte-identical after canonicalization.

Each record has its own SHA-256, and the document has a SHA-256 over the full
record collection and its source metadata. The digest proves content identity
only. It proves neither publisher identity, freshness, live-binding validity,
nor authority to act.

The v1 shape is closed and published as JSON Schema 2020-12 with a complete
golden envelope. A bound ruling that lacks its required ruling-time digest
remains readable only as `binding_evidence.status=invalid_missing`; the export
never normalizes that invalid record into an ordinary ruling. Preventing the
write belongs to the separately reviewed binding-integrity change.

The artifact deliberately carries no export timestamp. The same ledger state
and selection have one digest; the consumer records when it read those bytes.

## Consequences

- Consumers stop treating `list --json` and CLI help as schemas.
- SQLite tables, joins, display truncation, migration layout, and effective-
  check selection stay hidden behind Janus.
- Unknown schemas, states, duplicate JSON keys, floating-point values, migration
  mismatches, or digest mismatches fail closed.
- Stored commands and locators remain inert data and may be sensitive. An
  export inherits the ledger's confidentiality.
- On WAL-mode storage, a successful export may create or update private
  `-wal`/`-shm` coordination files. It does not create the main database or
  change logical ledger content, and it refuses any coordination file that is
  not inside the exact owner-only boundary.
- Existing `list --json` and `show --json` remain unstable human/diagnostic
  views. They are not aliases for the export contract.
- A later loopback GET may serve these exact bytes. It does not earn a second
  shape, pagination, filtering, or capability negotiation until a measured
  consumer requires them.

## Alternatives considered

- **Declare the current list JSON stable.** Rejected: it exposes internal row
  names, truncates observations, and makes vocabulary discovery a help-text
  scrape.
- **Build HTTP and capability discovery now.** Rejected: there is one measured
  caller and it already has a read-only CLI transport. A second transport is
  not yet real.
- **Offer a broad filter language.** Rejected: complete and exact-gate reads
  cover the measured calls. Filter planning and pagination would widen the
  contract before demand exists.
- **Include live drift or decay verdicts.** Rejected: export would gain side
  effects or freeze time-derived claims. Their recorded inputs travel; current
  verdicts remain the reader's deliberate computation.
- **Open with `immutable=1` to suppress WAL coordination.** Rejected: SQLite
  skips locking and change detection only when the file truly cannot change.
  Janus is a live ledger; falsely asserting immutability can return incorrect
  results or corruption errors during a concurrent write.
- **Copy only the main database into a temporary snapshot.** Rejected: committed
  rows may still live in the WAL, and copying the family without SQLite's
  locking protocol does not produce an atomic snapshot.
- **Depend on an external canonical-JSON package.** Rejected: the ledger is
  standard-library-only and locally substitutable. Janus instead publishes a
  deliberately narrow canonicalizer plus conformance vectors.

## Sources

- SQLite WAL read-only requirements and sidecar lifecycle:
  https://sqlite.org/wal.html#read_only_databases
- SQLite `immutable=1` semantics and incorrect-result warning:
  https://sqlite.org/uri.html#uriimmutable
