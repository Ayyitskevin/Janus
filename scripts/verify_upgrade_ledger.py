"""Read rollout evidence consistently in the host, candidate and rollback environments.

The preparation and apply drivers invoke this file with the wheel's isolated
Python. Opening with that installed Janus version performs its normal migrations.
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


def ledger_content(connection: sqlite3.Connection) -> tuple[dict[str, int], dict[str, str]]:
    counts = {}
    digests = {}
    for table in LEDGER_TABLES:
        cursor = connection.execute(f"SELECT * FROM {table}")
        columns = [item[0] for item in cursor.description]
        rows = [list(row) for row in cursor.fetchall()]
        if table == "gates" and "request_id" in columns:
            index = columns.index("request_id")
            # Migration 0006 leaves old gates unkeyed. Their v1 digest stays
            # comparable to a pre-migration receipt until the first keyed raise.
            if all(row[index] is None for row in rows):
                columns.pop(index)
                for row in rows:
                    row.pop(index)
        rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        content = json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False,
                             separators=(",", ":")).encode()
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
