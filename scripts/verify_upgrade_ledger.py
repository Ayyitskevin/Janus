"""Read rollout evidence consistently in the host, candidate and rollback environments.

The drivers capture this source at startup and run it with each wheel's
isolated Python. The installed Janus version performs its normal migrations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

LEDGER_TABLES = ("gates", "gate_options", "rulings", "observations", "check_revisions",
                 "audit_events")


def _raise_requests(connection: sqlite3.Connection) -> list[list[str]]:
    registered = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = '0006_raise_request_id'"
    ).fetchone()
    obj = connection.execute(
        "SELECT type FROM sqlite_schema WHERE name = 'raise_requests'"
    ).fetchone()
    if registered is None and obj is None:
        return []
    if registered is None or obj is None or obj[0] != "table":
        raise sqlite3.IntegrityError("raise_requests table and migration 0006 disagree")
    if connection.execute("PRAGMA foreign_key_check(raise_requests)").fetchone() is not None:
        raise sqlite3.IntegrityError("raise_requests contains a missing gate")
    return [list(row) for row in connection.execute(
        "SELECT request_id, gate_id FROM raise_requests ORDER BY request_id COLLATE BINARY"
    )]


def ledger_content(connection: sqlite3.Connection) -> tuple[dict[str, int], dict[str, str]]:
    counts = {}
    digests = {}
    requests = _raise_requests(connection)
    for table in LEDGER_TABLES:
        cursor = connection.execute(f"SELECT * FROM {table}")
        columns = [item[0] for item in cursor.description]
        rows = [list(row) for row in cursor.fetchall()]
        rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        payload = {"columns": columns, "rows": rows}
        if table == "gates" and requests:
            payload["raise_requests"] = requests
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        counts[table] = len(rows)
        digests[table] = hashlib.sha256(content).hexdigest()
    return counts, digests


def main() -> None:
    from janus import __version__, core

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("candidate", "rollback"))
    parser.add_argument("db", type=Path)
    args = parser.parse_args()
    with closing(core.connect(args.db)) as connection:
        connection.execute("BEGIN")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        migrations = [dict(row) for row in connection.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version")]
        counts, digests = ledger_content(connection)
    result = {
        "version": __version__,
        "integrity": integrity,
        "migrations": migrations,
        "packaged_migrations": sorted(path.stem for path in core.MIGRATIONS_DIR.glob("*.sql")),
        "counts": counts,
        "content_sha256": digests,
    }
    if args.role == "candidate":
        result["storage_findings"] = core.storage_privacy_findings(args.db)
    else:
        result["migrations"] = [row["version"] for row in migrations]
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
