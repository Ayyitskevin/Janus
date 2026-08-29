# Upgrade preparation manifest v1

`scripts/prepare_upgrade.py` creates a private, self-contained evidence bundle
before a Janus upgrade. It does not install code, change permissions, migrate
the live ledger, restart a service, or grant authority to do any of those
things.

The bundle has four purposes:

1. preserve one coherent SQLite backup, including committed WAL rows;
2. bind candidate and rollback source distributions and wheels to exact Git
   commits and SHA-256 digests;
3. migrate only a private rehearsal copy and prove its integrity and row counts;
4. prove the exact rollback wheel can read the candidate-migrated rehearsal
   copy.

The JSON contract is
[`upgrade-preparation-v1.schema.json`](upgrade-preparation-v1.schema.json). All
paths in the manifest except `source.repo` and `live_source.database` are
relative to the bundle root. Unknown properties are refused by the schema.

## Invocation

Run from a clean candidate checkout whose test dependencies are installed. The
rollback commit must be the full lowercase SHA of the version currently
installed, and it must be an ancestor of the candidate:

```bash
python scripts/prepare_upgrade.py \
  --db /absolute/path/to/janus.db \
  --output /absolute/private-parent/janus-upgrade-YYYYMMDDTHHMMSSZ \
  --rollback-commit <full-40-character-installed-commit>
```

The output parent must already exist, be owned by the caller, and have mode
`0700`. The output path must not exist. The tool refuses a dirty checkout,
symlinked path component, database hard link, wrong owner, missing database,
unknown rollback commit, or a rollback that is not an ancestor.

Candidate and rollback artifacts are built from `git archive` of the recorded
commits, never from mutable working-tree bytes. Each is installed into a fresh
virtual environment from its local wheel with `--no-index --no-deps`; ambient
`PYTHONPATH`, `PYTHONHOME`, and user site packages are excluded from both
verifiers.

## SQLite and privacy semantics

The backup uses SQLite's online backup API through a `mode=ro`, `query_only`
source connection. This includes committed WAL content without copying a live
database family as unrelated files. As with stable export, SQLite may touch
WAL/SHM coordination state while obtaining a safe snapshot. The operation
performs zero logical source writes, checks the main database identity before
and after, and never changes live schema, rows, or audit history.

The staging tree is owner-only throughout. Every retained directory is `0700`
and every retained file is `0600`. The manifest contains counts, migration
checksums, filesystem identities, and artifact hashes, but no gate text. The
backup itself remains ledger-confidential and must be protected accordingly.

Publication is a same-parent atomic rename followed by a parent-directory
`fsync`. Any failure before publication removes the complete staging tree; a
cleanup failure is reported rather than hidden.

## What successful preparation proves

- The recorded source commits existed and the candidate checkout was clean.
- The bundle artifacts have the recorded SHA-256 digests.
- SQLite reported `integrity_check=ok` for the backup and rehearsal copy.
- Candidate migration preserved all earlier migration version/checksum pairs
  plus the row counts and canonical content digest of every ledger table.
- The rollback wheel opened the migrated copy and observed the same counts and
  content digests.

It does **not** prove that the operator selected the correct installed commit,
that the candidate is bug-free, that a future deployment will succeed, or that
deployment is authorized. `deployment_performed` is always `false`; deployment
remains a separate human-reviewed operation.

## Normative implementation sources

- Python SQLite online backup:
  https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup
- Python virtual-environment disposability:
  https://docs.python.org/3.11/library/venv.html
- POSIX atomic replacement and durability primitives:
  https://docs.python.org/3.11/library/os.html#os.replace and
  https://docs.python.org/3.11/library/os.html#os.fsync
- pip local-only, dependency-free installation:
  https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-no-deps
