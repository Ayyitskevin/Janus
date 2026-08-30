"""Invariant tests for the non-deploying upgrade preparation boundary."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from copy import deepcopy
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts" / "prepare_upgrade.py"

sys.path.insert(0, str(PROJECT / "src"))

from janus import core  # noqa: E402


def _load_script():
    spec = importlib.util.spec_from_file_location("janus_prepare_upgrade", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare_upgrade = _load_script()


def _run(*argv: str, cwd: Path, check: bool = True, env=None):
    return subprocess.run(
        argv,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=True,
        env=env,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _manifest_schema() -> dict:
    path = PROJECT / "docs" / "spec" / "upgrade-preparation-v1.schema.json"
    return json.loads(path.read_text())


def _legacy_ledger(root: Path, *, keep_open: bool = False):
    """Create real 0002 state so rollback and candidate both rehearse upgrades."""
    directory = root / "ledger"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    db = directory / "janus.db"
    descriptor = os.open(db, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    db.chmod(0o600)

    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute(
        "CREATE TABLE schema_migrations ("
        "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)"
    )
    for migration in sorted((PROJECT / "src" / "janus" / "migrations").glob("*.sql"))[:2]:
        sql = migration.read_text()
        connection.executescript(sql)
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at, checksum) VALUES (?,?,?)",
            (migration.stem, core.now(), hashlib.sha256(sql.encode()).hexdigest()),
        )
        connection.commit()
    gate_id = core.raise_gate(
        connection,
        question="Approve disclosure of the confidential incident?",
        kind="authority",
        decay="the private finding remains uncoordinated",
        consumer="operator: acts only after independently checking the ruling binding",
        actor="tester",
    )
    if keep_open:
        return db, gate_id, connection
    connection.close()
    return db, gate_id, None


def _committed_copy(root: Path) -> tuple[Path, str, str]:
    repo = root / "repo"
    shutil.copytree(
        PROJECT,
        repo,
        ignore=shutil.ignore_patterns(
            ".git",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
            "__pycache__",
            "*.pyc",
            "*.egg-info",
            "build",
            "dist",
        ),
    )
    _run("git", "init", "-q", cwd=repo)
    _run("git", "config", "user.name", "Janus test", cwd=repo)
    _run("git", "config", "user.email", "janus-test@example.invalid", cwd=repo)
    newest_migration = (
        repo / "src" / "janus" / "migrations" / "0004_decision_learning_events.sql"
    )
    newest_migration_bytes = newest_migration.read_bytes()
    newest_migration.unlink()
    _run("git", "add", ".", cwd=repo)
    _run("git", "commit", "-qm", "rollback without migration 0004", cwd=repo)
    rollback = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    newest_migration.write_bytes(newest_migration_bytes)
    _run("git", "add", str(newest_migration.relative_to(repo)), cwd=repo)
    _run("git", "commit", "-qm", "candidate adds migration 0004", cwd=repo)
    candidate = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    return repo, rollback, candidate


def _invoke(repo: Path, db: Path, output: Path, rollback: str, *, env=None):
    return _run(
        sys.executable,
        str(repo / "scripts" / "prepare_upgrade.py"),
        "--db",
        str(db),
        "--output",
        str(output),
        "--rollback-commit",
        rollback,
        cwd=repo,
        check=False,
        env=env,
    )


def test_backup_captures_committed_wal_without_changing_live_rows(tmp_path):
    db, gate_id, connection = _legacy_ledger(tmp_path, keep_open=True)
    assert connection is not None
    before_hash = _sha256(db)
    before_rows = connection.execute("SELECT COUNT(*) FROM gates").fetchone()[0]
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir(mode=0o700)
    backup_dir.chmod(0o700)
    backup = backup_dir / "janus.db"

    facts, identities = prepare_upgrade._backup_database(db, backup)

    assert facts["integrity_check"] == "ok"
    assert facts["counts"]["gates"] == 1
    assert set(facts["content_sha256"]) == set(prepare_upgrade.LEDGER_TABLES)
    assert [item["version"] for item in facts["migrations"]] == [
        "0001_initial",
        "0002_check_revisions",
    ]
    with sqlite3.connect(backup) as copied:
        assert copied.execute("SELECT id FROM gates").fetchone()[0] == gate_id
    assert connection.execute("SELECT COUNT(*) FROM gates").fetchone()[0] == before_rows
    assert identities["before"]["database"]["inode"] == identities["after"]["database"]["inode"]
    assert _sha256(db) == before_hash
    connection.close()


def test_content_digest_detects_same_count_ledger_rewrite(tmp_path):
    db, _, connection = _legacy_ledger(tmp_path, keep_open=True)
    assert connection is not None
    before = prepare_upgrade._database_facts(connection)

    connection.execute("DROP TRIGGER gates_no_update")
    connection.execute("UPDATE gates SET question = 'rewritten without changing the count'")
    connection.commit()
    after = prepare_upgrade._database_facts(connection)

    assert before["counts"] == after["counts"]
    assert before["content_sha256"]["gates"] != after["content_sha256"]["gates"]
    connection.close()


def test_end_to_end_bundle_is_private_exact_and_non_deploying(tmp_path):
    repo, rollback, commit = _committed_copy(tmp_path)
    db, _, _ = _legacy_ledger(tmp_path)
    output_parent = tmp_path / "prepared"
    output_parent.mkdir(mode=0o700)
    output_parent.chmod(0o700)
    output = output_parent / "bundle"
    live_hash = _sha256(db)
    malicious = tmp_path / "ambient" / "janus"
    malicious.mkdir(parents=True)
    (malicious / "__init__.py").write_text("raise RuntimeError('ambient Janus imported')\n")
    environment = {**os.environ, "PYTHONPATH": str(malicious.parent)}

    result = _invoke(repo, db, output, rollback, env=environment)

    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    Draft202012Validator.check_schema(_manifest_schema())
    Draft202012Validator(_manifest_schema()).validate(manifest)
    assert manifest["schema"] == "janus.upgrade-preparation.v1"
    assert manifest["source"]["commit"] == commit
    assert manifest["artifacts"]["rollback"]["commit"] == rollback
    assert rollback != commit
    assert manifest["deployment_performed"] is False
    assert manifest["live_source"]["logical_writes"] == 0
    assert manifest["rehearsal"]["candidate"]["counts"] == manifest["backup"]["counts"]
    assert manifest["rehearsal"]["rollback"]["counts"] == manifest["backup"]["counts"]
    assert (
        manifest["rehearsal"]["candidate"]["content_sha256"]
        == manifest["backup"]["content_sha256"]
    )
    assert (
        manifest["rehearsal"]["rollback"]["content_sha256"]
        == manifest["backup"]["content_sha256"]
    )
    assert [item["version"] for item in manifest["backup"]["migrations"]] == [
        "0001_initial",
        "0002_check_revisions",
    ]
    assert [item["version"] for item in manifest["rehearsal"]["candidate"]["migrations"]] == [
        "0001_initial",
        "0002_check_revisions",
        "0003_bound_rulings_require_digest",
        "0004_decision_learning_events",
    ]
    assert manifest["rehearsal"]["candidate"]["packaged_migrations"] == [
        "0001_initial",
        "0002_check_revisions",
        "0003_bound_rulings_require_digest",
        "0004_decision_learning_events",
    ]
    assert manifest["rehearsal"]["rollback"]["packaged_migrations"] == [
        "0001_initial",
        "0002_check_revisions",
        "0003_bound_rulings_require_digest",
    ]
    assert "confidential incident" not in (output / "manifest.json").read_text()
    assert _sha256(db) == live_hash
    assert not (output / ".work").exists()

    for artifact_group in manifest["artifacts"].values():
        for artifact in artifact_group["files"]:
            path = output / artifact["path"]
            assert path.is_file()
            assert _sha256(path) == artifact["sha256"]
    assert _sha256(output / manifest["backup"]["path"]) == manifest["backup"]["sha256"]
    directories = [output, *[path for path in output.rglob("*") if path.is_dir()]]
    assert all(_mode(path) == 0o700 for path in directories)
    assert all(_mode(path) == 0o600 for path in output.rglob("*") if path.is_file())

    wrong_roles = deepcopy(manifest)
    wrong_roles["artifacts"]["candidate"]["files"] = deepcopy(
        manifest["artifacts"]["rollback"]["files"]
    )
    with pytest.raises(ValidationError):
        Draft202012Validator(_manifest_schema()).validate(wrong_roles)

    duplicate_wheels = deepcopy(manifest)
    wheel = next(
        item
        for item in duplicate_wheels["artifacts"]["candidate"]["files"]
        if item["path"].endswith(".whl")
    )
    duplicate_wheels["artifacts"]["candidate"]["files"] = [
        wheel,
        {**wheel, "sha256": "0" * 64},
    ]
    with pytest.raises(ValidationError):
        Draft202012Validator(_manifest_schema()).validate(duplicate_wheels)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("dirty", "repository is dirty"),
        ("existing", "output already exists"),
        ("broad-parent", "output parent mode 0755"),
        ("short-rollback", "full lowercase 40-character SHA"),
    ],
)
def test_refusals_publish_nothing(tmp_path, case, expected):
    repo, rollback, _ = _committed_copy(tmp_path)
    db, _, _ = _legacy_ledger(tmp_path)
    output_parent = tmp_path / "prepared"
    output_parent.mkdir(mode=0o700)
    output_parent.chmod(0o700)
    output = output_parent / "bundle"
    if case == "dirty":
        (repo / "untracked").write_text("not committed")
    elif case == "existing":
        output.mkdir()
    elif case == "broad-parent":
        output_parent.chmod(0o755)
    elif case == "short-rollback":
        rollback = rollback[:12]

    result = _invoke(repo, db, output, rollback)

    assert result.returncode == 2
    assert expected in result.stderr
    if case != "existing":
        assert not output.exists()
    assert not list(output_parent.glob(".bundle.preparing-*"))


def test_relative_paths_symlinks_and_hardlinks_are_refused(tmp_path):
    repo, rollback, _ = _committed_copy(tmp_path)
    db, _, _ = _legacy_ledger(tmp_path)
    output_parent = tmp_path / "prepared"
    output_parent.mkdir(mode=0o700)
    output_parent.chmod(0o700)

    with pytest.raises(prepare_upgrade.PreparationError, match="--db must be an absolute path"):
        prepare_upgrade.prepare(repo, Path("relative.db"), output_parent / "one", rollback)

    db_link = tmp_path / "linked.db"
    db_link.symlink_to(db)
    with pytest.raises(prepare_upgrade.PreparationError, match="database is a symbolic link"):
        prepare_upgrade.prepare(repo, db_link, output_parent / "two", rollback)

    hardlink = db.parent / "second-name.db"
    os.link(db, hardlink)
    with pytest.raises(prepare_upgrade.PreparationError, match="database has 2 hard links"):
        prepare_upgrade.prepare(repo, db, output_parent / "three", rollback)


def test_source_family_refuses_cross_account_replacement_and_writes(tmp_path, monkeypatch):
    db, _, _ = _legacy_ledger(tmp_path)
    db.parent.chmod(0o777)
    with pytest.raises(prepare_upgrade.PreparationError, match="replace a child entry"):
        prepare_upgrade._source_family(db)

    db.parent.chmod(0o700)
    db.chmod(0o602)
    with pytest.raises(prepare_upgrade.PreparationError, match="writable by another OS user"):
        prepare_upgrade._source_family(db)

    db.chmod(0o660)
    db.parent.chmod(0o770)
    monkeypatch.setattr(prepare_upgrade, "_other_group_accounts", lambda group_id: [])
    assert prepare_upgrade._source_family(db)["database"]["mode"] == "0660"


def test_failure_removes_the_sensitive_staging_tree(tmp_path, monkeypatch):
    repo, rollback, _ = _committed_copy(tmp_path)
    db, _, _ = _legacy_ledger(tmp_path)
    output_parent = tmp_path / "prepared"
    output_parent.mkdir(mode=0o700)
    output_parent.chmod(0o700)
    output = output_parent / "bundle"

    def fail_rehearsal(*args, **kwargs):
        raise prepare_upgrade.PreparationError("deliberate rehearsal failure")

    monkeypatch.setattr(prepare_upgrade, "_rehearse", fail_rehearsal)
    with pytest.raises(prepare_upgrade.PreparationError, match="deliberate rehearsal failure"):
        prepare_upgrade.prepare(repo, db, output, rollback)

    assert not output.exists()
    assert not list(output_parent.glob(".bundle.preparing-*"))
