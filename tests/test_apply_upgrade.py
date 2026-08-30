"""Invariant tests for the receipt-bound rollout seam."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / "scripts"
SCRIPT = SCRIPTS / "apply_upgrade.py"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(PROJECT / "src"))

from janus import core  # noqa: E402


def _load_script():
    spec = importlib.util.spec_from_file_location("janus_apply_upgrade", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


apply_upgrade = _load_script()


def _run(*argv: str, cwd: Path, check: bool = True):
    return subprocess.run(
        argv,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=True,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _legacy_ledger(root: Path) -> Path:
    directory = root / "ledger-source"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    db = directory / "janus.db"
    descriptor = os.open(db, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    with sqlite3.connect(db) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            "CREATE TABLE schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)"
        )
        for migration in sorted((PROJECT / "src/janus/migrations").glob("*.sql"))[:2]:
            sql = migration.read_text()
            connection.executescript(sql)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at, checksum) VALUES (?,?,?)",
                (migration.stem, core.now(), hashlib.sha256(sql.encode()).hexdigest()),
            )
            connection.commit()
        core.raise_gate(
            connection,
            question="Approve the rollout fixture?",
            kind="authority",
            decay="the fixture remains intentionally old",
            consumer="test: verify only after independently checking the receipt",
            actor="tester",
        )
        connection.commit()
    return db


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
    _run("git", "config", "user.name", "Janus rollout test", cwd=repo)
    _run("git", "config", "user.email", "janus-rollout@example.invalid", cwd=repo)
    newest = repo / "src/janus/migrations/0003_bound_rulings_require_digest.sql"
    newest_bytes = newest.read_bytes()
    newest.unlink()
    _run("git", "add", ".", cwd=repo)
    _run("git", "commit", "-qm", "rollback fixture", cwd=repo)
    rollback = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    newest.write_bytes(newest_bytes)
    _run("git", "add", str(newest.relative_to(repo)), cwd=repo)
    _run("git", "commit", "-qm", "candidate fixture", cwd=repo)
    candidate = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    return repo, rollback, candidate


@pytest.fixture(scope="session")
def prepared_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("janus-rollout-template")
    repo, rollback, candidate = _committed_copy(root)
    db = _legacy_ledger(root)
    parent = root / "prepared"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    bundle = parent / "bundle"
    result = _run(
        sys.executable,
        str(repo / "scripts/prepare_upgrade.py"),
        "--db",
        str(db),
        "--output",
        str(bundle),
        "--rollback-commit",
        rollback,
        cwd=repo,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return {"repo": repo, "bundle": bundle, "rollback": rollback, "candidate": candidate}


def _case(prepared_template: dict, tmp_path: Path) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    bundle = tmp_path / "bundle"
    shutil.copytree(prepared_template["bundle"], bundle)
    bundle.chmod(0o700)
    for path in bundle.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o775)
    ledger.chmod(0o775)
    manifest = json.loads((bundle / "manifest.json").read_text())
    db = ledger / "janus.db"
    shutil.copyfile(bundle / manifest["backup"]["path"], db)
    db.chmod(0o644)
    manifest["live_source"]["database"] = str(db)
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (bundle / "manifest.json").chmod(0o600)

    install_root = tmp_path / "install"
    install_root.mkdir(mode=0o775)
    install_root.chmod(0o775)
    active = install_root / "venv"
    (active / "bin").mkdir(parents=True, mode=0o700)
    active.chmod(0o775)
    (active / "bin/janus").write_text("#!/bin/sh\nexit 0\n")
    (active / "bin/janus").chmod(0o700)
    installed = install_root / "INSTALLED"
    installed.write_text(
        "installed_from  fixture\n"
        f"commit          {prepared_template['rollback']}\n"
        "installed_at    2026-08-29T00:00:00Z\n"
        "reinstall       fixture\n"
    )
    installed.chmod(0o664)
    binary = tmp_path / "bin"
    binary.mkdir(mode=0o700)
    wrapper = binary / "janus"
    wrapper.write_text(f'#!/bin/sh\nexec {active}/bin/janus "$@"\n')
    wrapper.chmod(0o775)
    return {
        **prepared_template,
        "bundle": bundle,
        "db": db,
        "install_root": install_root,
        "active": active,
        "wrapper": wrapper,
        "manifest": manifest,
    }


def _preflight(case: dict):
    return apply_upgrade.preflight(
        bundle=case["bundle"],
        db=case["db"],
        install_root=case["install_root"],
        active=case["active"],
        wrapper=case["wrapper"],
        repo=case["repo"],
    )


def _invoke(case: dict, command: str, *extra: str):
    return _run(
        sys.executable,
        str(case["repo"] / "scripts/apply_upgrade.py"),
        command,
        "--bundle",
        str(case["bundle"]),
        "--db",
        str(case["db"]),
        "--install-root",
        str(case["install_root"]),
        "--active",
        str(case["active"]),
        "--wrapper",
        str(case["wrapper"]),
        *extra,
        cwd=case["repo"],
        check=False,
    )


def test_preflight_binds_exact_artifacts_and_changes_no_logical_state(
    prepared_template, tmp_path
):
    case = _case(prepared_template, tmp_path)
    before_installed = (case["install_root"] / "INSTALLED").read_bytes()

    result = _preflight(case)

    assert result["manifest"]["source"]["commit"] == case["candidate"]
    assert result["installed"]["commit"] == case["rollback"]
    assert result["facts"]["counts"] == case["manifest"]["backup"]["counts"]
    assert result["facts"]["content_sha256"] == case["manifest"]["backup"][
        "content_sha256"
    ]
    assert (case["install_root"] / "INSTALLED").read_bytes() == before_installed
    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert not (case["install_root"] / "releases").exists()
    assert _mode(case["install_root"]) == 0o775
    assert _mode(case["wrapper"]) == 0o775
    assert _mode(case["db"].parent) == 0o775
    assert _mode(case["db"]) == 0o644


def test_preflight_refuses_a_live_ledger_that_advanced_after_preparation(
    prepared_template, tmp_path
):
    case = _case(prepared_template, tmp_path)
    with sqlite3.connect(case["db"]) as connection:
        connection.execute(
            "INSERT INTO gates (id, raised_at, raised_by, question, kind, decay, consumer) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                "gnewer000000",
                "2026-08-29T00:00:01Z",
                "tester",
                "A later gate?",
                "authority",
                "the prepared recovery point becomes stale",
                "test: do nothing",
            ),
        )
        connection.commit()

    with pytest.raises(apply_upgrade.RolloutError, match="changed after preparation"):
        _preflight(case)

    assert not (case["install_root"] / "releases").exists()


def test_preflight_refuses_tampered_artifacts_and_wrong_installed_provenance(
    prepared_template, tmp_path
):
    tampered = _case(prepared_template, tmp_path / "tampered")
    candidate = next(
        tampered["bundle"].glob("artifacts/candidate/*.whl")
    )
    candidate.write_bytes(candidate.read_bytes() + b"tampered")
    candidate.chmod(0o600)
    with pytest.raises(apply_upgrade.RolloutError, match="artifact hash"):
        _preflight(tampered)

    wrong = _case(prepared_template, tmp_path / "wrong")
    record = wrong["install_root"] / "INSTALLED"
    record.write_text(record.read_text().replace(wrong["rollback"], "0" * 40))
    record.chmod(0o600)
    with pytest.raises(apply_upgrade.RolloutError, match="installed commit"):
        _preflight(wrong)


def test_preflight_refuses_symlinked_bundle_content_and_wrong_wrapper(
    prepared_template, tmp_path
):
    linked = _case(prepared_template, tmp_path / "linked")
    wheel = next(linked["bundle"].glob("artifacts/rollback/*.whl"))
    original = tmp_path / "rollback.whl"
    shutil.copyfile(wheel, original)
    original.chmod(0o600)
    wheel.unlink()
    wheel.symlink_to(original)
    with pytest.raises(apply_upgrade.RolloutError, match="regular file"):
        _preflight(linked)

    wrong = _case(prepared_template, tmp_path / "wrapper")
    wrong["wrapper"].write_text("#!/bin/sh\nexit 0\n")
    wrong["wrapper"].chmod(0o700)
    with pytest.raises(apply_upgrade.RolloutError, match="does not exec"):
        _preflight(wrong)

    commented = _case(prepared_template, tmp_path / "commented-wrapper")
    expected = f'exec {commented["active"]}/bin/janus "$@"'
    commented["wrapper"].write_text(f"#!/bin/sh\n# {expected}\nexit 0\n")
    commented["wrapper"].chmod(0o700)
    with pytest.raises(apply_upgrade.RolloutError, match="does not exec"):
        _preflight(commented)


def test_preflight_binds_an_active_symlink_to_installed_release(
    prepared_template, tmp_path
):
    case = _case(prepared_template, tmp_path)
    releases = case["install_root"] / "releases"
    releases.mkdir(mode=0o700)
    target = releases / case["rollback"]
    os.replace(case["active"], target)
    target.chmod(0o700)
    marker = target / "JANUS_RELEASE.json"
    marker.write_text(
        json.dumps(
            {
                "commit": case["rollback"],
                "wheel_sha256": apply_upgrade._wheel_digest(
                    case["manifest"], "rollback"
                ),
            }
        )
        + "\n"
    )
    marker.chmod(0o600)
    case["active"].symlink_to(target)

    _preflight(case)

    marker.write_text(
        json.dumps({"commit": case["rollback"], "wheel_sha256": "0" * 64}) + "\n"
    )
    marker.chmod(0o600)
    with pytest.raises(apply_upgrade.RolloutError, match="marker does not match"):
        _preflight(case)


def test_apply_requires_explicit_effect_confirmation(prepared_template, tmp_path):
    case = _case(prepared_template, tmp_path)
    before = (case["install_root"] / "INSTALLED").read_bytes()

    result = _invoke(case, "apply")

    assert result.returncode == 2
    assert "requires --yes" in result.stderr
    assert "authority      external to Janus" in result.stdout
    assert (case["install_root"] / "INSTALLED").read_bytes() == before
    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert not (case["install_root"] / "releases").exists()


def test_database_holder_refusal_restores_the_original_active_environment(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    original_inode = case["active"].stat().st_ino
    before = (case["install_root"] / "INSTALLED").read_bytes()
    fake_candidate = tmp_path / "candidate"
    fake_rollback = tmp_path / "rollback"
    fake_candidate.mkdir()
    fake_rollback.mkdir()
    releases = iter((fake_candidate, fake_rollback))
    monkeypatch.setattr(apply_upgrade, "_stage_release", lambda **kwargs: next(releases))
    monkeypatch.setattr(apply_upgrade, "_open_database_holders", lambda db: ["4242"])

    with pytest.raises(apply_upgrade.RolloutError, match="still open"):
        apply_upgrade.apply_upgrade(
            bundle=case["bundle"],
            db=case["db"],
            install_root=case["install_root"],
            active=case["active"],
            wrapper=case["wrapper"],
            repo=case["repo"],
        )

    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert case["active"].stat().st_ino == original_inode
    assert (case["install_root"] / "INSTALLED").read_bytes() == before
    assert not (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").exists()


@pytest.mark.parametrize("active_kind", ["directory", "symlink"])
def test_hard_crash_after_maintenance_switch_leaves_exact_previous_environment(
    prepared_template, tmp_path, monkeypatch, active_kind
):
    case = _case(prepared_template, tmp_path)
    prior_target = None
    if active_kind == "symlink":
        releases_root = case["install_root"] / "releases"
        releases_root.mkdir(mode=0o700)
        releases_root.chmod(0o700)
        prior_target = releases_root / case["rollback"]
        os.replace(case["active"], prior_target)
        marker = prior_target / "JANUS_RELEASE.json"
        marker.write_text(
            json.dumps(
                {
                    "commit": case["rollback"],
                    "wheel_sha256": apply_upgrade._wheel_digest(
                        case["manifest"], "rollback"
                    ),
                }
            )
            + "\n"
        )
        marker.chmod(0o600)
        case["active"].symlink_to(prior_target)
    original = case["active"].lstat()
    fake_candidate = tmp_path / "candidate"
    fake_rollback = tmp_path / "rollback"
    fake_candidate.mkdir()
    fake_rollback.mkdir()
    releases = iter((fake_candidate, fake_rollback))
    monkeypatch.setattr(apply_upgrade, "_stage_release", lambda **kwargs: next(releases))
    real_enter_maintenance = apply_upgrade._enter_maintenance

    def crash_after_switch(*args, **kwargs):
        real_enter_maintenance(*args, **kwargs)
        os._exit(91)

    monkeypatch.setattr(apply_upgrade, "_enter_maintenance", crash_after_switch)
    child = os.fork()
    if child == 0:
        try:
            apply_upgrade.apply_upgrade(
                bundle=case["bundle"],
                db=case["db"],
                install_root=case["install_root"],
                active=case["active"],
                wrapper=case["wrapper"],
                repo=case["repo"],
            )
        except BaseException:
            os._exit(92)
        os._exit(93)

    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 91
    journal = json.loads(
        (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").read_text()
    )
    previous = journal["previous"]
    assert journal["step"] == "entering_maintenance"
    assert previous == {
        "kind": active_kind,
        "target": str(prior_target) if prior_target is not None else None,
        "legacy": previous["legacy"],
        "device": original.st_dev,
        "inode": original.st_ino,
    }
    if active_kind == "directory":
        legacy = Path(previous["legacy"])
        assert legacy.is_dir()
        assert legacy.stat().st_dev == original.st_dev
        assert legacy.stat().st_ino == original.st_ino
    else:
        assert previous["legacy"] is None
    assert case["active"].is_symlink()
    assert case["active"].resolve() == case["install_root"] / "maintenance"


def test_maintenance_switch_fsync_failure_restores_legacy_directory(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    original_inode = case["active"].stat().st_ino
    maintenance = case["install_root"] / "maintenance"
    maintenance.mkdir(mode=0o700)
    legacy = case["install_root"] / "legacy"
    legacy.mkdir(mode=0o700)
    real_fsync = apply_upgrade.prepare_upgrade._fsync_directory
    calls = 0

    def fail_first_fsync(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected directory fsync failure")
        real_fsync(path)

    monkeypatch.setattr(
        apply_upgrade.prepare_upgrade,
        "_fsync_directory",
        fail_first_fsync,
    )
    previous = apply_upgrade._plan_maintenance(
        case["active"], legacy, case["rollback"]
    )

    with pytest.raises(OSError, match="injected"):
        apply_upgrade._enter_maintenance(case["active"], maintenance, previous)

    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert case["active"].stat().st_ino == original_inode
    assert list(legacy.iterdir()) == []


def test_maintenance_refuses_when_active_changes_after_journal_plan(
    prepared_template, tmp_path
):
    case = _case(prepared_template, tmp_path)
    maintenance = case["install_root"] / "maintenance"
    maintenance.mkdir(mode=0o700)
    legacy = case["install_root"] / "legacy"
    legacy.mkdir(mode=0o700)
    previous = apply_upgrade._plan_maintenance(
        case["active"], legacy, case["rollback"]
    )
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    original = tmp_path / "original-active"
    os.replace(case["active"], original)
    os.replace(replacement, case["active"])

    with pytest.raises(apply_upgrade.RolloutError, match="changed after recovery state"):
        apply_upgrade._enter_maintenance(case["active"], maintenance, previous)

    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert list(legacy.iterdir()) == []


def test_maintenance_restore_preserves_relative_symlink_target(
    prepared_template, tmp_path
):
    case = _case(prepared_template, tmp_path)
    prior_target = case["install_root"] / "prior"
    os.replace(case["active"], prior_target)
    case["active"].symlink_to("prior", target_is_directory=True)
    maintenance = case["install_root"] / "maintenance"
    maintenance.mkdir(mode=0o700)
    legacy = case["install_root"] / "legacy"
    legacy.mkdir(mode=0o700)
    previous = apply_upgrade._plan_maintenance(
        case["active"], legacy, case["rollback"]
    )

    apply_upgrade._enter_maintenance(case["active"], maintenance, previous)
    apply_upgrade._restore_active(case["active"], previous)

    assert os.readlink(case["active"]) == "prior"
    assert case["active"].resolve() == prior_target


def test_initial_journal_failure_does_not_mutate_active(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    original_inode = case["active"].stat().st_ino
    fake_candidate = tmp_path / "candidate"
    fake_rollback = tmp_path / "rollback"
    fake_candidate.mkdir()
    fake_rollback.mkdir()
    releases = iter((fake_candidate, fake_rollback))
    monkeypatch.setattr(apply_upgrade, "_stage_release", lambda **kwargs: next(releases))
    real_json = apply_upgrade._atomic_json

    def fail_after_initial_journal(path, document, schema=None):
        real_json(path, document, schema)
        if path.name == "ROLLOUT_IN_PROGRESS.json":
            raise OSError("injected initial journal fsync failure")

    monkeypatch.setattr(apply_upgrade, "_atomic_json", fail_after_initial_journal)

    with pytest.raises(OSError, match="injected initial journal"):
        apply_upgrade.apply_upgrade(
            bundle=case["bundle"],
            db=case["db"],
            install_root=case["install_root"],
            active=case["active"],
            wrapper=case["wrapper"],
            repo=case["repo"],
        )

    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert case["active"].stat().st_ino == original_inode
    assert not (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").exists()


def test_apply_preserves_journal_on_active_identity_race(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    original = case["active"].stat()
    fake_candidate = tmp_path / "candidate"
    fake_rollback = tmp_path / "rollback"
    fake_candidate.mkdir()
    fake_rollback.mkdir()
    releases = iter((fake_candidate, fake_rollback))
    monkeypatch.setattr(apply_upgrade, "_stage_release", lambda **kwargs: next(releases))
    real_json = apply_upgrade._atomic_json
    replacement_inode = None

    def replace_active_after_initial_journal(path, document, schema=None):
        nonlocal replacement_inode
        real_json(path, document, schema)
        if path.name == "ROLLOUT_IN_PROGRESS.json" and replacement_inode is None:
            original_path = tmp_path / "original-active"
            replacement = tmp_path / "replacement"
            replacement.mkdir()
            os.replace(case["active"], original_path)
            os.replace(replacement, case["active"])
            replacement_inode = case["active"].stat().st_ino

    monkeypatch.setattr(
        apply_upgrade,
        "_atomic_json",
        replace_active_after_initial_journal,
    )

    with pytest.raises(apply_upgrade.RolloutError, match="recovery also failed"):
        apply_upgrade.apply_upgrade(
            bundle=case["bundle"],
            db=case["db"],
            install_root=case["install_root"],
            active=case["active"],
            wrapper=case["wrapper"],
            repo=case["repo"],
        )

    journal_path = case["install_root"] / "ROLLOUT_IN_PROGRESS.json"
    journal = json.loads(journal_path.read_text())
    assert journal["step"] == "entering_maintenance"
    assert journal["previous"]["device"] == original.st_dev
    assert journal["previous"]["inode"] == original.st_ino
    assert case["active"].stat().st_ino == replacement_inode


@pytest.mark.parametrize("failure_point", ["installed", "receipt"])
def test_post_migration_write_failure_restores_code_and_removes_success_receipt(
    prepared_template, tmp_path, monkeypatch, failure_point
):
    case = _case(prepared_template, tmp_path)
    original_inode = case["active"].stat().st_ino
    installed_before = (case["install_root"] / "INSTALLED").read_bytes()
    monkeypatch.setattr(apply_upgrade, "_open_database_holders", lambda db: [])
    injected = False

    if failure_point == "installed":
        real_write = apply_upgrade._atomic_private_file

        def fail_after_installed_write(path, content, *, mode=0o600):
            nonlocal injected
            real_write(path, content, mode=mode)
            if path.name == "INSTALLED" and not injected:
                injected = True
                raise OSError("injected installed-record fsync failure")

        monkeypatch.setattr(
            apply_upgrade,
            "_atomic_private_file",
            fail_after_installed_write,
        )
    else:
        real_json = apply_upgrade._atomic_json

        def fail_after_receipt_write(path, document, schema=None):
            nonlocal injected
            real_json(path, document, schema)
            if path.parent.name == "receipts" and not injected:
                injected = True
                raise OSError("injected receipt fsync failure")

        monkeypatch.setattr(apply_upgrade, "_atomic_json", fail_after_receipt_write)

    with pytest.raises(OSError, match="injected"):
        apply_upgrade.apply_upgrade(
            bundle=case["bundle"],
            db=case["db"],
            install_root=case["install_root"],
            active=case["active"],
            wrapper=case["wrapper"],
            repo=case["repo"],
        )

    assert injected
    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert case["active"].stat().st_ino == original_inode
    assert (case["install_root"] / "INSTALLED").read_bytes() == installed_before
    assert list((case["install_root"] / "receipts").iterdir()) == []
    assert not (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").exists()


def test_hard_exit_after_installed_write_journals_exact_prior_provenance(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    installed = case["install_root"] / "INSTALLED"
    active_inode_before = case["active"].stat().st_ino
    installed_before = installed.read_bytes()
    installed_mode_before = _mode(installed)
    monkeypatch.setattr(apply_upgrade, "_open_database_holders", lambda db: [])
    real_write = apply_upgrade._atomic_private_file

    def hard_exit_after_installed_write(path, content, *, mode=0o600):
        real_write(path, content, mode=mode)
        if path == installed:
            os._exit(91)

    monkeypatch.setattr(
        apply_upgrade,
        "_atomic_private_file",
        hard_exit_after_installed_write,
    )
    child = os.fork()
    if child == 0:
        try:
            apply_upgrade.apply_upgrade(
                bundle=case["bundle"],
                db=case["db"],
                install_root=case["install_root"],
                active=case["active"],
                wrapper=case["wrapper"],
                repo=case["repo"],
            )
        except BaseException:
            os._exit(92)
        os._exit(93)

    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 91
    monkeypatch.setattr(apply_upgrade, "_atomic_private_file", real_write)
    journal = json.loads(
        (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").read_text()
    )
    assert journal["step"] == "activating"
    assert case["active"].is_symlink()
    assert installed.read_bytes() != installed_before
    assert journal["installed_record_before"] == {
        "content_base64": base64.b64encode(installed_before).decode("ascii"),
        "mode": f"{installed_mode_before:04o}",
        "path": str(installed),
        "sha256": hashlib.sha256(installed_before).hexdigest(),
    }
    apply_upgrade._restore_active(case["active"], journal["previous"])
    apply_upgrade._restore_installed_record(
        journal["installed_record_before"],
        expected_path=installed,
    )
    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert case["active"].stat().st_ino == active_inode_before
    assert installed.read_bytes() == installed_before
    assert _mode(installed) == installed_mode_before


def test_rollout_lock_and_unfinished_journal_fail_closed(prepared_template, tmp_path):
    case = _case(prepared_template, tmp_path)
    with apply_upgrade._rollout_lock(case["install_root"]):
        with pytest.raises(apply_upgrade.RolloutError, match="another Janus rollout"):
            with apply_upgrade._rollout_lock(case["install_root"]):
                pass

    journal = case["install_root"] / "ROLLOUT_IN_PROGRESS.json"
    journal.write_text("{}\n")
    journal.chmod(0o600)
    with pytest.raises(apply_upgrade.RolloutError, match="unfinished rollout journal"):
        apply_upgrade.apply_upgrade(
            bundle=case["bundle"],
            db=case["db"],
            install_root=case["install_root"],
            active=case["active"],
            wrapper=case["wrapper"],
            repo=case["repo"],
        )


def test_end_to_end_rollout_activates_exact_candidate_and_retains_rollback(
    prepared_template, tmp_path
):
    case = _case(prepared_template, tmp_path)
    before_counts = case["manifest"]["backup"]["counts"]
    before_digests = case["manifest"]["backup"]["content_sha256"]

    result = _invoke(case, "apply", "--yes")

    assert result.returncode == 0, result.stderr
    assert "receipt" in result.stdout
    assert case["active"].is_symlink()
    assert case["active"].resolve() == case["install_root"] / "releases" / case["candidate"]
    assert _run(str(case["wrapper"]), "--help", cwd=tmp_path).returncode == 0
    installed = (case["install_root"] / "INSTALLED").read_text()
    assert f"commit          {case['candidate']}" in installed
    assert f"rollback_commit {case['rollback']}" in installed
    assert _mode(case["install_root"]) == 0o700
    assert _mode(case["db"].parent) == 0o700
    assert _mode(case["db"]) == 0o600
    assert not (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").exists()

    receipts = list((case["install_root"] / "receipts").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    schema = json.loads(
        (case["repo"] / "docs/spec/rollout-receipt-v1.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(receipt)
    assert receipt["preparation"]["candidate_commit"] == case["candidate"]
    assert receipt["preparation"]["rollback_commit"] == case["rollback"]
    assert receipt["semantics"] == {
        "authority": "external_to_janus",
        "receipt_is_authority": False,
    }
    assert receipt["deployment_performed"] is True
    assert receipt["before"]["database"]["counts"] == before_counts
    assert receipt["after"]["database"]["counts"] == before_counts
    assert receipt["before"]["database"]["content_sha256"] == before_digests
    assert receipt["after"]["database"]["content_sha256"] == before_digests
    assert [item["version"] for item in receipt["after"]["database"]["migrations"]] == [
        "0001_initial",
        "0002_check_revisions",
        "0003_bound_rulings_require_digest",
    ]
    assert Path(receipt["rollback"]["code_environment"]).is_dir()
    assert Path(receipt["rollback"]["database_backup"]).is_file()
    assert Path(receipt["releases"]["legacy_environment"]).is_dir()
