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
    newest = repo / "src/janus/migrations/0004_decision_learning_events.sql"
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


def _assert_candidate_state(case: dict) -> None:
    assert case["active"].is_symlink()
    assert case["active"].resolve() == case["install_root"] / "releases" / case[
        "candidate"
    ]
    assert f"commit          {case['candidate']}" in (
        case["install_root"] / "INSTALLED"
    ).read_text()
    assert len(list((case["install_root"] / "receipts").glob("*.json"))) == 1


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


def _invoke_recovery(case: dict, *extra: str):
    return _run(
        sys.executable,
        str(case["repo"] / "scripts/apply_upgrade.py"),
        "recover",
        "--install-root",
        str(case["install_root"]),
        "--active",
        str(case["active"]),
        *extra,
        cwd=case["repo"],
        check=False,
    )


def _tree_snapshot(root: Path) -> dict[str, tuple]:
    snapshot = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        relative = str(path.relative_to(root)) or "."
        if stat.S_ISLNK(info.st_mode):
            payload = ("symlink", os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            payload = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            payload = ("directory", None)
        snapshot[relative] = (
            *payload,
            info.st_dev,
            info.st_ino,
            stat.S_IMODE(info.st_mode),
            info.st_size,
            info.st_mtime_ns,
        )
    return snapshot


def _stub_staged_releases(case: dict, monkeypatch) -> tuple[Path, Path]:
    releases = case["install_root"] / "releases"
    releases.mkdir(mode=0o700, exist_ok=True)
    releases.chmod(0o700)
    paths = []
    for role, commit in (("candidate", case["candidate"]), ("rollback", case["rollback"])):
        release = releases / commit
        release.mkdir(mode=0o700, exist_ok=True)
        release.chmod(0o700)
        marker = release / "JANUS_RELEASE.json"
        marker.write_text(
            json.dumps(
                {
                    "commit": commit,
                    "wheel_sha256": apply_upgrade._wheel_digest(case["manifest"], role),
                },
                sort_keys=True,
            )
            + "\n"
        )
        marker.chmod(0o600)
        paths.append(release)
    staged = iter(paths)
    monkeypatch.setattr(apply_upgrade, "_stage_release", lambda **kwargs: next(staged))
    return paths[0], paths[1]


def _hard_crash_after_maintenance(case: dict, monkeypatch) -> None:
    _stub_staged_releases(case, monkeypatch)
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


def _rewrite_journal(case: dict, mutate) -> dict:
    path = case["install_root"] / "ROLLOUT_IN_PROGRESS.json"
    journal = json.loads(path.read_text())
    mutate(journal)
    path.write_text(json.dumps(journal, indent=2, sort_keys=True) + "\n")
    path.chmod(0o600)
    return journal


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


def test_preflight_refuses_a_ruling_recorded_after_preparation(prepared_template, tmp_path):
    case = _case(prepared_template, tmp_path)
    with sqlite3.connect(case["db"]) as connection:
        connection.row_factory = sqlite3.Row
        gate_id = connection.execute("SELECT id FROM gates ORDER BY raised_at LIMIT 1").fetchone()[
            "id"
        ]
        core.close_gate(
            connection,
            gate_id,
            state="approved",
            reason="the operator approved after the backup was prepared",
            actor="owner",
        )

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


def test_recovery_preview_is_read_only_and_yes_restores_hard_crash(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    original_inode = case["active"].stat().st_ino
    installed = case["install_root"] / "INSTALLED"
    installed_before = installed.read_bytes()
    installed_mode_before = _mode(installed)
    _hard_crash_after_maintenance(case, monkeypatch)
    journal_path = case["install_root"] / "ROLLOUT_IN_PROGRESS.json"
    journal = json.loads(journal_path.read_text())
    schema = json.loads(
        (case["repo"] / "docs/spec/rollout-in-progress-v1.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(journal)
    tree_before = _tree_snapshot(case["install_root"])
    database_before = hashlib.sha256(case["db"].read_bytes()).hexdigest()

    preview = _invoke_recovery(case)

    assert preview.returncode == 0, preview.stderr
    assert "reconciliation restore_prior_code" in preview.stdout
    assert "recovery only  no active" in preview.stdout
    assert _tree_snapshot(case["install_root"]) == tree_before
    assert hashlib.sha256(case["db"].read_bytes()).hexdigest() == database_before

    with pytest.raises(apply_upgrade.RolloutError, match="changed after its effects"):
        apply_upgrade.recover_upgrade(
            install_root=case["install_root"],
            active=case["active"],
            repo=case["repo"],
            expected_journal_sha256="0" * 64,
        )
    assert _tree_snapshot(case["install_root"]) == tree_before

    recovered = _invoke_recovery(case, "--yes")

    assert recovered.returncode == 0, recovered.stderr
    assert "reconciled     restore_prior_code" in recovered.stdout
    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert case["active"].stat().st_ino == original_inode
    assert installed.read_bytes() == installed_before
    assert _mode(installed) == installed_mode_before
    assert not journal_path.exists()
    assert hashlib.sha256(case["db"].read_bytes()).hexdigest() == database_before


def test_recovery_restores_a_directory_missing_after_its_legacy_move(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    original_inode = case["active"].stat().st_ino
    _hard_crash_after_maintenance(case, monkeypatch)
    case["active"].unlink()

    plan = apply_upgrade.inspect_recovery(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
    )

    assert plan["active_state"] == "missing_after_legacy_move"
    apply_upgrade.recover_upgrade(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
        expected_journal_sha256=plan["journal_sha256"],
    )
    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert case["active"].stat().st_ino == original_inode


def test_recovery_restores_the_exact_relative_rollback_symlink(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    releases = case["install_root"] / "releases"
    releases.mkdir(mode=0o700)
    rollback = releases / case["rollback"]
    os.replace(case["active"], rollback)
    rollback.chmod(0o700)
    marker = rollback / "JANUS_RELEASE.json"
    marker.write_text(
        json.dumps(
            {
                "commit": case["rollback"],
                "wheel_sha256": apply_upgrade._wheel_digest(
                    case["manifest"], "rollback"
                ),
            },
            sort_keys=True,
        )
        + "\n"
    )
    marker.chmod(0o600)
    relative_target = f"releases/{case['rollback']}"
    case["active"].symlink_to(relative_target, target_is_directory=True)
    _hard_crash_after_maintenance(case, monkeypatch)

    plan = apply_upgrade.inspect_recovery(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
    )
    apply_upgrade.recover_upgrade(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
        expected_journal_sha256=plan["journal_sha256"],
    )

    assert case["active"].is_symlink()
    assert os.readlink(case["active"]) == relative_target
    assert case["active"].resolve() == rollback


def test_recovery_only_clears_the_journal_when_prior_state_is_already_restored(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    _hard_crash_after_maintenance(case, monkeypatch)
    journal_path = case["install_root"] / "ROLLOUT_IN_PROGRESS.json"
    journal = json.loads(journal_path.read_text())
    apply_upgrade._restore_active(
        case["active"],
        journal["previous"],
        allowed_current_targets=(case["install_root"] / "maintenance",),
    )

    plan = apply_upgrade.inspect_recovery(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
    )

    assert plan["active_state"] == "previous"
    assert plan["installed_state"] == "before"
    assert plan["effects"] == [
        "remove the recovery journal after rechecking prior state"
    ]
    apply_upgrade.recover_upgrade(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
        expected_journal_sha256=plan["journal_sha256"],
    )
    assert not journal_path.exists()


def test_recovery_binds_candidate_bytes_not_the_original_checkout_location(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    _hard_crash_after_maintenance(case, monkeypatch)
    _rewrite_journal(
        case,
        lambda value: value["source"].__setitem__(
            "repository", "/original/candidate/checkout/is/gone"
        ),
    )

    plan = apply_upgrade.inspect_recovery(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
    )
    apply_upgrade.recover_upgrade(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
        expected_journal_sha256=plan["journal_sha256"],
    )

    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert not (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").exists()


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("unknown_field", "invalid at <root>"),
        ("target_escape", "does not match the recovery target"),
        ("candidate_marker", "candidate release marker does not match"),
        ("installed_bytes", "installed provenance does not match"),
        ("maintenance_content", "maintenance environment has unexpected content"),
        ("active_replacement", "active environment does not match"),
        ("legacy_replacement", "legacy environment identity does not match"),
        ("source_commit", "source commit does not match its candidate"),
        ("receipt_escape", "receipt path is outside"),
        ("legacy_escape", "legacy environment is outside"),
        ("receipt_symlink", "is not an owned regular file"),
        ("receipt_digest", "receipt digest does not match"),
        ("receipt_schema", "recorded rollout receipt is invalid"),
        ("receipt_size", "receipt exceeds the size limit"),
        ("journal_size", "journal exceeds the size limit"),
        ("journal_mode", "mode 0644"),
        ("lock_mode", "unsafe rollout lock"),
    ],
)
def test_recovery_refuses_tampered_or_unrecognized_state_without_mutation(
    prepared_template, tmp_path, monkeypatch, tamper, message
):
    case = _case(prepared_template, tmp_path)
    _hard_crash_after_maintenance(case, monkeypatch)
    journal_path = case["install_root"] / "ROLLOUT_IN_PROGRESS.json"
    journal = json.loads(journal_path.read_text())

    if tamper == "unknown_field":
        _rewrite_journal(case, lambda value: value.__setitem__("unknown", True))
    elif tamper == "target_escape":
        _rewrite_journal(
            case,
            lambda value: value["target"].__setitem__("installed_record", "/tmp/outside"),
        )
    elif tamper == "candidate_marker":
        marker = Path(journal["candidate"]["environment"]) / "JANUS_RELEASE.json"
        marker.write_text(json.dumps({"commit": "0" * 40, "wheel_sha256": "0" * 64}))
        marker.chmod(0o600)
    elif tamper == "installed_bytes":
        installed = case["install_root"] / "INSTALLED"
        installed.write_text("unrecognized provenance\n")
        installed.chmod(0o600)
    elif tamper == "maintenance_content":
        refusal = case["install_root"] / "maintenance" / "bin" / "janus"
        refusal.write_text("#!/bin/sh\nexit 75\n")
        refusal.chmod(0o700)
    elif tamper == "active_replacement":
        case["active"].unlink()
        case["active"].mkdir(mode=0o700)
    elif tamper == "legacy_replacement":
        legacy = Path(journal["previous"]["legacy"])
        os.replace(legacy, tmp_path / "original-legacy")
        legacy.mkdir(mode=0o700)
    elif tamper == "source_commit":
        _rewrite_journal(
            case,
            lambda value: value["source"].__setitem__("commit", "0" * 40),
        )
    elif tamper == "receipt_escape":
        def escape_receipt(value):
            value["step"] = "publishing_receipt"
            value["installed_record_after"] = value["installed_record_before"]
            value["receipt"] = {"path": "/tmp/outside", "sha256": "0" * 64}

        _rewrite_journal(
            case,
            escape_receipt,
        )
    elif tamper == "legacy_escape":
        _rewrite_journal(
            case,
            lambda value: value["previous"].__setitem__(
                "legacy", str(tmp_path / "outside-legacy")
            ),
        )
    elif tamper == "receipt_symlink":
        outside = tmp_path / "outside-receipt"
        outside.write_text("do not remove\n")
        receipt = case["install_root"] / "receipts" / "recorded.json"
        receipt.symlink_to(outside)

        def link_receipt(value):
            value["step"] = "publishing_receipt"
            value["installed_record_after"] = value["installed_record_before"]
            value["receipt"] = {
                "path": str(receipt),
                "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
            }

        _rewrite_journal(
            case,
            link_receipt,
        )
    elif tamper in {"receipt_digest", "receipt_schema"}:
        receipt = case["install_root"] / "receipts" / "recorded.json"
        receipt.write_text("{}\n" if tamper == "receipt_schema" else "not a receipt\n")
        receipt.chmod(0o600)

        def invalid_receipt(value):
            value["step"] = "publishing_receipt"
            value["installed_record_after"] = value["installed_record_before"]
            value["receipt"] = {
                "path": str(receipt),
                "sha256": (
                    hashlib.sha256(receipt.read_bytes()).hexdigest()
                    if tamper == "receipt_schema"
                    else "0" * 64
                ),
            }

        _rewrite_journal(case, invalid_receipt)
    elif tamper == "receipt_size":
        receipt = case["install_root"] / "receipts" / "recorded.json"
        receipt.write_bytes(b"x" * (apply_upgrade.MAX_RECOVERY_DOCUMENT_BYTES + 1))
        receipt.chmod(0o600)

        def oversized_receipt(value):
            value["step"] = "publishing_receipt"
            value["installed_record_after"] = value["installed_record_before"]
            value["receipt"] = {
                "path": str(receipt),
                "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            }

        _rewrite_journal(case, oversized_receipt)
    elif tamper == "journal_size":
        journal_path.write_bytes(b"x" * (apply_upgrade.MAX_RECOVERY_DOCUMENT_BYTES + 1))
        journal_path.chmod(0o600)
    elif tamper == "journal_mode":
        journal_path.chmod(0o644)
    elif tamper == "lock_mode":
        (case["install_root"] / "rollout.lock").chmod(0o644)
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(tamper)

    before = _tree_snapshot(case["install_root"])
    with pytest.raises(apply_upgrade.RolloutError, match=message):
        apply_upgrade.inspect_recovery(
            install_root=case["install_root"],
            active=case["active"],
            repo=case["repo"],
        )
    assert _tree_snapshot(case["install_root"]) == before


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
    apply_upgrade._restore_active(
        case["active"],
        previous,
        allowed_current_targets=(maintenance,),
    )

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


def test_post_migration_installed_write_failure_restores_prior_code(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    original_inode = case["active"].stat().st_ino
    installed_before = (case["install_root"] / "INSTALLED").read_bytes()
    monkeypatch.setattr(apply_upgrade, "_open_database_holders", lambda db: [])
    injected = False
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


def test_matching_receipt_after_publication_error_preserves_candidate_state(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    journal = case["install_root"] / "ROLLOUT_IN_PROGRESS.json"
    monkeypatch.setattr(apply_upgrade, "_open_database_holders", lambda db: [])
    real_json = apply_upgrade._atomic_json
    injected = False

    def fail_after_receipt_write(path, document, schema=None):
        nonlocal injected
        real_json(path, document, schema)
        if path.parent.name == "receipts" and not injected:
            injected = True
            raise OSError("injected receipt publication error")

    monkeypatch.setattr(apply_upgrade, "_atomic_json", fail_after_receipt_write)

    with pytest.raises(
        apply_upgrade.RolloutError,
        match="rollout completed but journal cleanup failed",
    ):
        apply_upgrade.apply_upgrade(
            bundle=case["bundle"],
            db=case["db"],
            install_root=case["install_root"],
            active=case["active"],
            wrapper=case["wrapper"],
            repo=case["repo"],
        )

    assert injected
    _assert_candidate_state(case)
    assert journal.exists()


def test_journal_cleanup_failure_preserves_committed_candidate_state(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    journal = case["install_root"] / "ROLLOUT_IN_PROGRESS.json"
    monkeypatch.setattr(apply_upgrade, "_open_database_holders", lambda db: [])
    real_unlink = Path.unlink
    injected = False

    def fail_final_journal_unlink(path, *args, **kwargs):
        nonlocal injected
        receipts = list((case["install_root"] / "receipts").glob("*.json"))
        if path == journal and receipts and not injected:
            injected = True
            raise OSError("injected final journal cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_final_journal_unlink)

    with pytest.raises(
        apply_upgrade.RolloutError,
        match="rollout completed but journal cleanup failed",
    ):
        apply_upgrade.apply_upgrade(
            bundle=case["bundle"],
            db=case["db"],
            install_root=case["install_root"],
            active=case["active"],
            wrapper=case["wrapper"],
            repo=case["repo"],
        )

    assert injected
    _assert_candidate_state(case)
    assert journal.exists()


def test_final_cleanup_fsync_failure_preserves_committed_candidate_state(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    journal = case["install_root"] / "ROLLOUT_IN_PROGRESS.json"
    monkeypatch.setattr(apply_upgrade, "_open_database_holders", lambda db: [])
    real_fsync = apply_upgrade.prepare_upgrade._fsync_directory
    injected = False

    def fail_final_cleanup_fsync(path):
        nonlocal injected
        receipts = list((case["install_root"] / "receipts").glob("*.json"))
        if path == case["install_root"] and receipts and not journal.exists() and not injected:
            injected = True
            raise OSError("injected final cleanup fsync failure")
        real_fsync(path)

    monkeypatch.setattr(
        apply_upgrade.prepare_upgrade,
        "_fsync_directory",
        fail_final_cleanup_fsync,
    )

    with pytest.raises(
        apply_upgrade.RolloutError,
        match="rollout completed but journal cleanup failed",
    ):
        apply_upgrade.apply_upgrade(
            bundle=case["bundle"],
            db=case["db"],
            install_root=case["install_root"],
            active=case["active"],
            wrapper=case["wrapper"],
            repo=case["repo"],
        )

    assert injected
    _assert_candidate_state(case)
    assert not journal.exists()


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
    assert journal["step"] == "candidate_active"
    assert case["active"].is_symlink()
    assert installed.read_bytes() != installed_before
    assert journal["installed_record_before"] == {
        "content_base64": base64.b64encode(installed_before).decode("ascii"),
        "mode": f"{installed_mode_before:04o}",
        "path": str(installed),
        "sha256": hashlib.sha256(installed_before).hexdigest(),
    }
    plan = apply_upgrade.inspect_recovery(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
    )
    assert plan["resolution"] == "restore_prior_code"
    assert plan["active_state"] == "candidate"
    assert plan["installed_state"] == "after"
    apply_upgrade.recover_upgrade(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
        expected_journal_sha256=plan["journal_sha256"],
    )
    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert case["active"].stat().st_ino == active_inode_before
    assert installed.read_bytes() == installed_before
    assert _mode(installed) == installed_mode_before
    assert not (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").exists()


def test_recovery_after_migration_restores_code_but_never_the_database(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    active_inode_before = case["active"].stat().st_ino
    installed = case["install_root"] / "INSTALLED"
    installed_before = installed.read_bytes()
    monkeypatch.setattr(apply_upgrade, "_open_database_holders", lambda db: [])
    real_verify = apply_upgrade._verify_environment

    def hard_exit_after_candidate(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        if kwargs["label"] == "candidate":
            os._exit(91)
        return result

    monkeypatch.setattr(apply_upgrade, "_verify_environment", hard_exit_after_candidate)
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
    with sqlite3.connect(case["db"]) as connection:
        migrations_before_recovery = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
    assert migrations_before_recovery[-1] == "0004_decision_learning_events"

    plan = apply_upgrade.inspect_recovery(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
    )
    apply_upgrade.recover_upgrade(
        install_root=case["install_root"],
        active=case["active"],
        repo=case["repo"],
        expected_journal_sha256=plan["journal_sha256"],
    )

    with sqlite3.connect(case["db"]) as connection:
        migrations_after_recovery = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
    assert migrations_after_recovery == migrations_before_recovery
    assert case["active"].is_dir() and not case["active"].is_symlink()
    assert case["active"].stat().st_ino == active_inode_before
    assert installed.read_bytes() == installed_before
    assert not (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").exists()


def test_recovery_finishes_forward_after_a_durable_success_receipt(
    prepared_template, tmp_path, monkeypatch
):
    case = _case(prepared_template, tmp_path)
    monkeypatch.setattr(apply_upgrade, "_open_database_holders", lambda db: [])
    real_json = apply_upgrade._atomic_json

    def hard_exit_after_receipt(path, document, schema=None):
        real_json(path, document, schema)
        if path.parent.name == "receipts":
            os._exit(91)

    monkeypatch.setattr(apply_upgrade, "_atomic_json", hard_exit_after_receipt)
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
    receipts = list((case["install_root"] / "receipts").glob("*.json"))
    assert len(receipts) == 1
    receipt_before = receipts[0].read_bytes()

    preview = _invoke_recovery(case)

    assert preview.returncode == 0, preview.stderr
    assert "reconciliation complete_forward" in preview.stdout
    assert "receipt        valid" in preview.stdout
    reconciled = _invoke_recovery(case, "--yes")
    assert reconciled.returncode == 0, reconciled.stderr
    assert "reconciled     complete_forward" in reconciled.stdout
    assert case["active"].is_symlink()
    assert case["active"].resolve() == case["install_root"] / "releases" / case[
        "candidate"
    ]
    assert receipts[0].read_bytes() == receipt_before
    assert f"commit          {case['candidate']}" in (
        case["install_root"] / "INSTALLED"
    ).read_text()
    assert not (case["install_root"] / "ROLLOUT_IN_PROGRESS.json").exists()


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
        "0004_decision_learning_events",
    ]
    assert Path(receipt["rollback"]["code_environment"]).is_dir()
    assert Path(receipt["rollback"]["database_backup"]).is_file()
    assert Path(receipt["releases"]["legacy_environment"]).is_dir()
