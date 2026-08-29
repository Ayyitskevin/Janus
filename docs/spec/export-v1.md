# Janus export v1

`janus export` is the stable, read-only interchange surface for gate evidence.
The full ledger and one-gate forms use the same envelope and record shape.

## Safety boundary

Successful verification means only that the parsed content matches its
record and document digests. It does **not** prove who produced the artifact,
that it is current, that a binding still matches, or that an action is
authorized. A consumer that may cause an effect independently re-verifies the
proposal bytes, its own policy, and its current preconditions.

Export does not execute stored decay/delivery commands or inspect binding
locators. Commands and locators are untrusted, potentially sensitive strings.

## Envelope

The top-level object has three members:

| Member | Meaning |
| --- | --- |
| `schema` | Exactly `janus.export.v1` |
| `document` | A `janus.gates.v1` document |
| `integrity` | `sha256`, canonicalizer identity, and `document_sha256` |

The document contains:

- `source`: `module: janus` and exact applied migration versions/checksums;
- `selection.gate_id`: `null` for the complete ledger or the requested ID;
- `semantics`: rulings are evidence, live bindings are not verified, and
  stored checks are not executed;
- `vocabulary`: the exact gate states with `terminal` and `human_ruled`
  traits, plus gate and binding kinds;
- `records`: ordered by `(raised_at, id)`.

Each record envelope contains a complete `janus.gate.v1` object and its own
`record_sha256`. The record preserves Janus's source words and attribution,
raise-time binding, terminal event and ruling-time digest, ordered options,
original/effective checks, all check revisions, all observations, citations,
and gate-specific audit events. A terminal event's `type` is either
`human_ruling` (`approved`, `refused`) or `non_ruling_closure` (`expired`,
`withdrawn`, `superseded`). An open gate has no terminal event.

There is intentionally no `authorized`, live-binding verdict, overdue flag,
decay verdict, or delivery verdict.

The normative closed-shape definition, including every key, type, nullability
rule, enumeration, and digest pattern, is the JSON Schema 2020-12 document
[`export-v1.schema.json`](export-v1.schema.json). Every object sets
`additionalProperties: false`: adding a field changes the contract and requires
a new schema version. Cross-field rules that JSON Schema cannot state plainly
(record and option ordering, ruling-to-option identity, point-selection
identity, and binding-evidence status) are normative in the reference
`verify_export()` codec and described below. Options use contiguous zero-based
positions, occur in position order, and have unique ids. A human ruling may
only name an option offered by that gate; an approval must name one when
options exist.

`terminal_event.binding_evidence` is one of:

- `recorded` plus the ruling-time SHA-256 for a bound human ruling;
- `not_applicable` plus `null` for an unbound ruling or a non-ruling closure;
- `invalid_missing` plus `null` for a bound ruling that violates the digest
  invariant. Verification keeps the record readable but can never make the
  invalid evidence read as fine. Preventing that invalid write is a separate
  gate-state/binding change and remains subject to its own human merge gate.

## `janus.canonical-json.v1`

Record and document digests are SHA-256 over these exact canonical bytes:

1. Input values are JSON null, booleans, signed 64-bit integers, strings,
   arrays, and objects with string keys. Floating point values and duplicate
   keys are refused.
2. Text is UTF-8 without a BOM. Unicode is not normalized and non-ASCII
   characters are emitted directly.
3. Object keys sort in ascending Unicode code-point order. Array order is
   preserved.
4. There is no insignificant whitespace: member and item separators are `:`
   and `,`.
5. JSON quotation and backslash escaping are required. Control characters use
   JSON's short escapes where defined (`\b`, `\t`, `\n`, `\f`, `\r`) and
   lowercase `\u00xx` otherwise. `/` is not escaped.
6. Integers use base-10 JSON notation with no leading plus sign or redundant
   leading zero.

Published primitive conformance vectors live at
`tests/fixtures/canonical-json-v1.json`; a complete verified envelope with
binding, ruling, options, revised checks, observations, provenance, and both
digest levels lives at `tests/fixtures/export-v1.golden.json`. Consumers must
pass both before claiming v1 compatibility.

## Failure semantics

- A missing exact gate is an error; an empty complete ledger is a valid
  document with an empty `records` array.
- The exporter refuses a local ledger whose migration history is absent,
  older, newer, or checksum-divergent from that exporter build. Export never
  repairs it. The verifier treats the ordered migration list as provenance,
  not as the wire-version discriminator: `janus.export.v1` and
  `janus.gates.v1` define compatibility so an internal migration need not break
  an unchanged consumer contract.
- Unknown envelope, document, or record schemas are incompatible.
- Missing or additional members in any v1 object are incompatible.
- Unknown gate states and binding kinds are incompatible; they never default.
- Verification stops on a document or record digest mismatch.

"Read-only" describes logical ledger content: export creates no main database,
schema, row, audit event, or observation. SQLite may materialize private
`-wal`/`-shm` coordination files to read a WAL-mode snapshot; Janus post-checks
the resulting database family against its exact storage boundary.

The CLI emits one canonical envelope followed by one line feed. The line feed
is transport whitespace and is outside both digest scopes.
