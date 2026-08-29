#!/usr/bin/env python3
"""Prepare and rehearse a Janus upgrade without changing live ledger content.

The output bundle is the reversible evidence an operator needs before the RED
deployment step: exact source artifacts, a coherent private SQLite backup, and
proof that both candidate and rollback code can read a migrated rehearsal copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "janus.upgrade-preparation.v1"
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
LEDGER_TABLES = (
    "gates",
    "gate_options",
    "rulings",
    "observations",
    "check_revisions",
    "audit_events",
)


class PreparationError(RuntimeError):
    """A fail-closed preparation refusal intended for the operator."""


def _run(
    argv: list[str],
    *,
    cwd: Path,
    capture: bool = False,
    isolated_python: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    if isolated_python:
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            check=True,
            text=True,
            capture_output=capture,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise PreparationError(f"command failed: {argv[0]}: {detail.strip()}") from exc


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args], cwd=repo, capture=True).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mode(info: os.stat_result) -> str:
    return f"{stat.S_IMODE(info.st_mode):04o}"


def _private_directory(path: Path) -> None:
    path.mkdir(mode=PRIVATE_DIRECTORY_MODE)
    path.chmod(PRIVATE_DIRECTORY_MODE)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != PRIVATE_DIRECTORY_MODE
    ):
        raise PreparationError(f"could not create private directory: {path}")


def _private_file(path: Path) -> None:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != PRIVATE_FILE_MODE
        ):
            raise PreparationError(f"could not create private file: {path}")
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _identity(path: Path, *, label: str) -> dict[str, object]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PreparationError(f"cannot inspect {label}: {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise PreparationError(f"{label} is a symbolic link: {path}")
    expected_type = stat.S_ISDIR if label == "ledger directory" else stat.S_ISREG
    if not expected_type(info.st_mode):
        raise PreparationError(f"{label} has the wrong type: {path}")
    if info.st_uid != os.geteuid():
        raise PreparationError(
            f"{label} owner uid {info.st_uid} does not match process uid {os.geteuid()}: {path}"
        )
    if label != "ledger directory" and info.st_nlink != 1:
        raise PreparationError(f"{label} has {info.st_nlink} hard links: {path}")
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mode": _mode(info),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def _directory_chain_finding(leaf: Path, *, private_leaf: bool) -> str | None:
    """Return why another OS user could replace an entry in ``leaf``'s path."""
    entries: list[tuple[Path, os.stat_result]] = []
    cursor = leaf
    while True:
        try:
            info = cursor.lstat()
        except OSError as exc:
            return f"cannot inspect directory {cursor}: {exc}"
        if stat.S_ISLNK(info.st_mode):
            return f"directory path contains a symbolic link: {cursor}"
        if not stat.S_ISDIR(info.st_mode):
            return f"directory path contains a non-directory: {cursor}"
        entries.append((cursor, info))
        if cursor == cursor.parent:
            break
        cursor = cursor.parent

    expected_uid = os.geteuid()
    for target, info in entries:
        if info.st_uid not in {0, expected_uid}:
            return (
                f"directory owner uid {info.st_uid} is neither root nor process uid "
                f"{expected_uid}: {target}"
            )

    if private_leaf:
        leaf_info = entries[0][1]
        if leaf_info.st_uid != expected_uid:
            return f"output parent is not owned by process uid {expected_uid}: {leaf}"
        leaf_mode = stat.S_IMODE(leaf_info.st_mode)
        if leaf_mode != PRIVATE_DIRECTORY_MODE:
            return f"output parent mode {leaf_mode:04o} (expected 0700): {leaf}"

    for (child, child_info), (parent, parent_info) in zip(entries, entries[1:]):
        parent_mode = stat.S_IMODE(parent_info.st_mode)
        if not (parent_mode & 0o022):
            continue
        if parent_mode & stat.S_ISVTX and child_info.st_uid in {0, expected_uid}:
            continue
        return f"directory permits another OS user to replace {child.name}: {parent}"
    return None


def _source_family(db: Path) -> dict[str, dict[str, object]]:
    if not db.is_absolute():
        raise PreparationError("--db must be an absolute path")
    chain_finding = _directory_chain_finding(db.parent, private_leaf=False)
    if chain_finding:
        raise PreparationError(f"unsafe ledger path: {chain_finding}")
    family = {"directory": _identity(db.parent, label="ledger directory")}
    family["database"] = _identity(db, label="database")
    for suffix, label in (
        ("-wal", "WAL"),
        ("-shm", "shared memory"),
        ("-journal", "rollback journal"),
    ):
        path = Path(f"{db}{suffix}")
        if path.exists() or path.is_symlink():
            family[suffix[1:]] = _identity(path, label=label)
    return family


def _same_database_identity(before: dict[str, object], after: dict[str, object]) -> bool:
    return (before["device"], before["inode"]) == (after["device"], after["inode"])


def _database_facts(connection: sqlite3.Connection) -> dict[str, object]:
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise PreparationError(f"SQLite integrity check failed: {integrity!r}")
    migrations = [
        {"version": row[0], "checksum": row[1]}
        for row in connection.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        )
    ]
    counts = {}
    content_sha256 = {}
    for table in LEDGER_TABLES:
        cursor = connection.execute(f"SELECT * FROM {table}")
        columns = [item[0] for item in cursor.description]
        rows = [list(row) for row in cursor.fetchall()]
        rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        content = json.dumps(
            {"columns": columns, "rows": rows},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        counts[table] = len(rows)
        content_sha256[table] = hashlib.sha256(content).hexdigest()
    return {
        "integrity_check": "ok",
        "migrations": migrations,
        "counts": counts,
        "content_sha256": content_sha256,
    }


def _backup_database(source: Path, destination: Path) -> tuple[dict, dict]:
    before = _source_family(source)
    _private_file(destination)
    source_uri = f"{source.as_uri()}?mode=ro"
    destination_uri = f"{destination.as_uri()}?mode=rw"
    try:
        with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
            source_connection.execute("PRAGMA query_only = ON")
            with closing(sqlite3.connect(destination_uri, uri=True)) as backup_connection:
                # Python documents Connection.backup as safe while other clients
                # access the database, so committed WAL rows are not lost.
                # Source: https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.backup
                source_connection.backup(backup_connection)
                facts = _database_facts(backup_connection)
                if source_connection.total_changes != 0:
                    raise PreparationError("the read-only source connection reported writes")
    except sqlite3.Error as exc:
        raise PreparationError(f"could not create a coherent SQLite backup: {exc}") from exc

    _fsync_file(destination)
    _fsync_directory(destination.parent)
    after = _source_family(source)
    if not _same_database_identity(before["database"], after["database"]):
        raise PreparationError("the live database pathname changed identity during backup")
    return facts, {"before": before, "after": after}


def _safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(root):
                raise PreparationError(f"git archive escaped its destination: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise PreparationError(f"git archive contains an unsupported entry: {member.name}")
        extraction_options = {"filter": "data"} if sys.version_info >= (3, 12) else {}
        source.extractall(destination, **extraction_options)


def _build_artifacts(
    repo: Path,
    commit: str,
    work: Path,
    artifacts: Path,
    label: str,
) -> list[dict]:
    archive = work / f"{label}-source.tar"
    source = work / f"{label}-source"
    _private_directory(source)
    _run(
        ["git", "-C", str(repo), "archive", "--format=tar", "--output", str(archive), commit],
        cwd=repo,
    )
    _safe_extract(archive, source)
    archive.unlink()
    _run(
        [sys.executable, "-m", "build", "--outdir", str(artifacts), str(source)],
        cwd=work,
        isolated_python=True,
    )
    distributions = sorted((*artifacts.glob("*.tar.gz"), *artifacts.glob("*.whl")))
    wheels = [path for path in distributions if path.suffix == ".whl"]
    if len(distributions) != 2 or len(wheels) != 1:
        raise PreparationError("build must produce exactly one source archive and one wheel")
    for path in distributions:
        path.chmod(PRIVATE_FILE_MODE)
        _fsync_file(path)
    _fsync_directory(artifacts)
    return [
        {
            "path": str(Path("artifacts") / label / path.name),
            "sha256": _sha256(path),
        }
        for path in distributions
    ]


def _verifier_code() -> str:
    return """
import json
import hashlib
import pathlib
import sqlite3

from janus import __version__, core

db = pathlib.Path(__import__('sys').argv[1])
connection = core.connect(db)
integrity = [row[0] for row in connection.execute('PRAGMA integrity_check')]
migrations = [dict(row) for row in connection.execute(
    'SELECT version, checksum FROM schema_migrations ORDER BY version')]
counts = {table: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
          for table in ('gates', 'gate_options', 'rulings', 'observations',
                        'check_revisions', 'audit_events')}
content_sha256 = {}
for table in counts:
    cursor = connection.execute(f'SELECT * FROM {table}')
    columns = [item[0] for item in cursor.description]
    rows = [list(row) for row in cursor.fetchall()]
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, separators=(',', ':')))
    content = json.dumps({'columns': columns, 'rows': rows}, ensure_ascii=False,
                         separators=(',', ':')).encode()
    content_sha256[table] = hashlib.sha256(content).hexdigest()
connection.close()
findings = core.storage_privacy_findings(db)
print(json.dumps({'version': __version__, 'integrity': integrity,
                  'migrations': migrations, 'counts': counts,
                  'content_sha256': content_sha256,
                  'storage_findings': findings}, sort_keys=True))
"""


def _rollback_verifier_code() -> str:
    return """
import json
import hashlib
import pathlib
from janus import __version__, core

db = pathlib.Path(__import__('sys').argv[1])
connection = core.connect(db)
integrity = [row[0] for row in connection.execute('PRAGMA integrity_check')]
migrations = [row['version'] for row in connection.execute(
    'SELECT version FROM schema_migrations ORDER BY version')]
counts = {table: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
          for table in ('gates', 'gate_options', 'rulings', 'observations',
                        'check_revisions', 'audit_events')}
content_sha256 = {}
for table in counts:
    cursor = connection.execute(f'SELECT * FROM {table}')
    columns = [item[0] for item in cursor.description]
    rows = [list(row) for row in cursor.fetchall()]
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, separators=(',', ':')))
    content = json.dumps({'columns': columns, 'rows': rows}, ensure_ascii=False,
                         separators=(',', ':')).encode()
    content_sha256[table] = hashlib.sha256(content).hexdigest()
connection.close()
print(json.dumps({'version': __version__, 'integrity': integrity,
                  'migrations': migrations, 'counts': counts,
                  'content_sha256': content_sha256}, sort_keys=True))
"""


def _json_result(completed: subprocess.CompletedProcess[str], label: str) -> dict:
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PreparationError(f"{label} returned invalid verification output") from exc


def _installed_verification(
    wheel: Path,
    environment: Path,
    rehearsal_db: Path,
    work: Path,
    code: str,
    label: str,
) -> dict:
    _run([sys.executable, "-m", "venv", str(environment)], cwd=work, isolated_python=True)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    # Install only the exact local wheel; dependency resolution cannot reach an index.
    # Source: https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-no-deps
    _run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
        cwd=work,
        isolated_python=True,
    )
    return _json_result(
        _run(
            [str(python), "-c", code, str(rehearsal_db)],
            cwd=work,
            capture=True,
            isolated_python=True,
        ),
        label,
    )


def _rehearse(
    candidate_wheel: Path,
    rollback_wheel: Path,
    backup: Path,
    work: Path,
) -> dict[str, object]:
    rehearsal = work / "rehearsal"
    _private_directory(rehearsal)
    rehearsal_db = rehearsal / "janus.db"
    _private_file(rehearsal_db)
    shutil.copyfile(backup, rehearsal_db)
    rehearsal_db.chmod(PRIVATE_FILE_MODE)

    # Virtual environments are disposable and should be recreated, not copied.
    # Source: https://docs.python.org/3.11/library/venv.html#creating-virtual-environments
    candidate = _installed_verification(
        candidate_wheel,
        rehearsal / "candidate-venv",
        rehearsal_db,
        work=rehearsal,
        code=_verifier_code(),
        label="candidate verifier",
    )
    if candidate["integrity"] != ["ok"] or candidate["storage_findings"]:
        raise PreparationError(f"candidate rehearsal failed: {candidate!r}")

    rollback = _installed_verification(
        rollback_wheel,
        rehearsal / "rollback-venv",
        rehearsal_db,
        work=rehearsal,
        code=_rollback_verifier_code(),
        label="rollback verifier",
    )
    if (
        rollback["integrity"] != ["ok"]
        or rollback["counts"] != candidate["counts"]
        or rollback["content_sha256"] != candidate["content_sha256"]
    ):
        raise PreparationError(f"rollback rehearsal failed: {rollback!r}")
    return {"candidate": candidate, "rollback": rollback}


def _validate_inputs(
    repo: Path,
    db: Path,
    output: Path,
    rollback_commit: str,
) -> tuple[str, str, str]:
    if os.name != "posix" or not hasattr(os, "geteuid"):
        raise PreparationError("upgrade preparation requires POSIX ownership and mode semantics")
    if not output.is_absolute():
        raise PreparationError("--output must be an absolute path")
    if output.exists() or output.is_symlink():
        raise PreparationError(f"output already exists: {output}")
    parent = output.parent
    parent_finding = _directory_chain_finding(parent, private_leaf=True)
    if parent_finding:
        raise PreparationError(f"unsafe output parent: {parent_finding}")
    if _git(repo, "status", "--porcelain"):
        raise PreparationError(
            "repository is dirty; preparation only accepts exact committed bytes"
        )
    commit = _git(repo, "rev-parse", "HEAD^{commit}")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    invalid_rollback_sha = len(rollback_commit) != 40 or any(
        character not in "0123456789abcdef" for character in rollback_commit
    )
    if invalid_rollback_sha:
        raise PreparationError("--rollback-commit must be a full lowercase 40-character SHA")
    try:
        resolved_rollback = _git(repo, "rev-parse", f"{rollback_commit}^{{commit}}")
    except PreparationError as exc:
        raise PreparationError(
            f"rollback commit is not present in this repository: {rollback_commit}"
        ) from exc
    if resolved_rollback != rollback_commit:
        raise PreparationError("--rollback-commit did not resolve to the exact requested commit")
    try:
        _run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", rollback_commit, commit],
            cwd=repo,
            capture=True,
        )
    except PreparationError as exc:
        raise PreparationError("rollback commit is not an ancestor of the candidate") from exc
    rollback_tree = _git(repo, "rev-parse", f"{rollback_commit}^{{tree}}")
    _source_family(db)
    return commit, tree, rollback_tree


def prepare(
    repo: Path,
    db: Path,
    output: Path,
    rollback_commit: str,
) -> dict[str, object]:
    for value, option in (
        (db, "--db"),
        (output, "--output"),
    ):
        if not value.is_absolute():
            raise PreparationError(f"{option} must be an absolute path")
    repo = repo.absolute()
    db = db.absolute()
    output = output.absolute()
    commit, tree, rollback_tree = _validate_inputs(repo, db, output, rollback_commit)

    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.preparing-", dir=output.parent))
    published = False
    try:
        stage.chmod(PRIVATE_DIRECTORY_MODE)
        stage_identity = _identity(stage, label="ledger directory")
        if stage_identity["mode"] != "0700":
            raise PreparationError(
                f"staging directory mode {stage_identity['mode']} (expected 0700)"
            )
        artifacts = stage / "artifacts"
        candidate_artifacts = artifacts / "candidate"
        rollback_artifacts = artifacts / "rollback"
        backup_dir = stage / "backup"
        work = stage / ".work"
        for directory in (
            artifacts,
            candidate_artifacts,
            rollback_artifacts,
            backup_dir,
            work,
        ):
            _private_directory(directory)

        candidate_built = _build_artifacts(
            repo, commit, work, candidate_artifacts, "candidate"
        )
        rollback_built = _build_artifacts(
            repo, rollback_commit, work, rollback_artifacts, "rollback"
        )
        candidate_wheel_name = next(
            item["path"]
            for item in candidate_built
            if str(item["path"]).endswith(".whl")
        )
        rollback_wheel_name = next(
            item["path"]
            for item in rollback_built
            if str(item["path"]).endswith(".whl")
        )
        candidate_wheel = stage / str(candidate_wheel_name)
        rollback_wheel = stage / str(rollback_wheel_name)
        backup = backup_dir / "janus.db"
        backup_facts, source_identity = _backup_database(db, backup)
        rehearsal = _rehearse(candidate_wheel, rollback_wheel, backup, work)
        if rehearsal["candidate"]["counts"] != backup_facts["counts"]:
            raise PreparationError("candidate migration changed ledger row counts")
        if rehearsal["candidate"]["content_sha256"] != backup_facts["content_sha256"]:
            raise PreparationError("candidate migration changed ledger content")
        original_migrations = backup_facts["migrations"]
        candidate_migrations = rehearsal["candidate"]["migrations"]
        if candidate_migrations[: len(original_migrations)] != original_migrations:
            raise PreparationError("candidate migration changed existing migration history")

        shutil.rmtree(work)
        manifest = {
            "schema": SCHEMA,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": {"repo": str(repo), "commit": commit, "tree": tree, "clean": True},
            "artifacts": {
                "candidate": {"commit": commit, "files": candidate_built},
                "rollback": {
                    "commit": rollback_commit,
                    "tree": rollback_tree,
                    "files": rollback_built,
                },
            },
            "live_source": {
                "database": str(db),
                "identity": source_identity,
                "logical_writes": 0,
            },
            "backup": {
                "path": str(backup.relative_to(stage)),
                "sha256": _sha256(backup),
                **backup_facts,
            },
            "rehearsal": rehearsal,
            "deployment_performed": False,
        }
        manifest_path = stage / "manifest.json"
        _private_file(manifest_path)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        _fsync_file(manifest_path)
        _fsync_directory(stage)

        # POSIX rename publishes the complete prepared directory atomically.
        # Source: https://docs.python.org/3.11/library/os.html#os.replace
        os.replace(stage, output)
        published = True
        _fsync_directory(output.parent)
        return manifest
    except BaseException as primary:
        if not published:
            try:
                shutil.rmtree(stage)
            except OSError as cleanup:
                raise PreparationError(
                    f"{primary}; sensitive staging cleanup also failed: {stage}: {cleanup}"
                ) from primary
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="build, back up, and rehearse a Janus upgrade without deploying it"
    )
    parser.add_argument("--db", required=True, type=Path, help="absolute existing Janus database")
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="new bundle path under an existing 0700 directory",
    )
    parser.add_argument(
        "--rollback-commit",
        required=True,
        help="full lowercase commit SHA of the currently installed rollback version",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        manifest = prepare(repo, args.db, args.output, args.rollback_commit)
    except PreparationError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    print(f"prepared      {args.output}")
    print(f"commit        {manifest['source']['commit']}")
    print(f"backup sha256 {manifest['backup']['sha256']}")
    versions = [item["version"] for item in manifest["rehearsal"]["candidate"]["migrations"]]
    print(f"migrations    {', '.join(versions)}")
    print("live logical writes  none — deployment remains a separate human-approved step")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
