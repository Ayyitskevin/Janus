#!/usr/bin/env python3
"""Apply one prepared Janus upgrade without consulting Janus for authority.

The default ``preflight`` subcommand is logically read-only. ``apply`` is a
human-gated maintenance operation: it consumes exact local artifacts, proves
the prepared snapshot is still current, stages both code directions, migrates,
and only then changes the active installed environment.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

import prepare_upgrade

RECEIPT_SCHEMA = "janus.rollout-receipt.v1"
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
EXECUTABLE_FILE_MODE = 0o700
COMPLETED_STEPS = [
    "validated_preparation",
    "matched_live_snapshot",
    "staged_candidate",
    "staged_rollback",
    "entered_maintenance",
    "repaired_storage",
    "migrated_candidate",
    "verified_rollback_reader",
    "activated_candidate",
    "wrote_installed_provenance",
]


class RolloutError(RuntimeError):
    """A fail-closed rollout refusal intended for the operator."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        raise RolloutError(f"command failed: {argv[0]}: {detail.strip()}") from exc


def _git(repo: Path, *args: str) -> str:
    return _run(
        ["git", "-C", str(repo), *args], cwd=repo, capture=True
    ).stdout.strip()


def _require_absolute(path: Path, option: str) -> Path:
    if not path.is_absolute():
        raise RolloutError(f"{option} must be an absolute path")
    if path != Path(os.path.abspath(path)):
        raise RolloutError(f"{option} must not contain unresolved path components")
    return path


def _safe_owned_file(
    path: Path,
    *,
    label: str,
    expected_mode: int | None = None,
    allow_private_group: bool = False,
) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RolloutError(f"cannot inspect {label}: {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RolloutError(f"{label} is not an owned regular file: {path}")
    if info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise RolloutError(f"{label} has unsafe ownership or link count: {path}")
    mode = stat.S_IMODE(info.st_mode)
    if expected_mode is not None and mode != expected_mode:
        raise RolloutError(f"{label} mode {mode:04o} (expected {expected_mode:04o}): {path}")
    if allow_private_group:
        finding = prepare_upgrade._source_permission_finding(
            path, label=label, directory=False
        )
        if finding:
            raise RolloutError(finding)
    elif mode & 0o022:
        raise RolloutError(f"{label} is writable by another OS identity: {path}")


def _safe_private_directory(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RolloutError(f"cannot inspect {label}: {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RolloutError(f"{label} is not a directory: {path}")
    if info.st_uid != os.geteuid():
        raise RolloutError(f"{label} is not owned by this process: {path}")
    mode = stat.S_IMODE(info.st_mode)
    if mode != PRIVATE_DIRECTORY_MODE:
        raise RolloutError(f"{label} mode {mode:04o} (expected 0700): {path}")
    finding = prepare_upgrade._directory_chain_finding(path, private_leaf=False)
    if finding:
        raise RolloutError(f"unsafe {label}: {finding}")


def _contained_file(bundle: Path, relative: str, *, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RolloutError(f"{label} path escapes the preparation bundle: {relative}")
    path = bundle / candidate
    try:
        path.relative_to(bundle)
    except ValueError as exc:
        raise RolloutError(f"{label} path escapes the preparation bundle: {relative}") from exc
    _safe_owned_file(path, label=label, expected_mode=PRIVATE_FILE_MODE)
    return path


def _schema(repo: Path, relative: str) -> dict[str, object]:
    path = repo / relative
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RolloutError(f"cannot load repository schema {path}: {exc}") from exc


def _validate_json(document: dict, schema: dict, *, label: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(document)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise RolloutError(f"{label} is invalid at {location}: {exc.message}") from exc


def _load_manifest(bundle: Path, db: Path, repo: Path) -> tuple[dict, dict[str, Path]]:
    _safe_private_directory(bundle, label="preparation bundle")
    manifest_path = bundle / "manifest.json"
    _safe_owned_file(
        manifest_path,
        label="preparation manifest",
        expected_mode=PRIVATE_FILE_MODE,
    )
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RolloutError(f"cannot read preparation manifest: {exc}") from exc
    _validate_json(
        manifest,
        _schema(repo, "docs/spec/upgrade-preparation-v1.schema.json"),
        label="preparation manifest",
    )
    if manifest["deployment_performed"] is not False:
        raise RolloutError("preparation manifest already claims deployment")
    if Path(manifest["live_source"]["database"]) != db:
        raise RolloutError("--db does not match the database bound by the preparation manifest")
    if Path(manifest["source"]["repo"]).resolve() != repo.resolve():
        raise RolloutError("running repository does not match the prepared source repository")
    if manifest["source"]["commit"] != manifest["artifacts"]["candidate"]["commit"]:
        raise RolloutError("candidate artifact commit does not match the prepared source commit")
    if manifest["source"]["commit"] == manifest["artifacts"]["rollback"]["commit"]:
        raise RolloutError("candidate and rollback commits must be different")

    files: dict[str, Path] = {
        "manifest": manifest_path,
        "backup": _contained_file(
            bundle,
            manifest["backup"]["path"],
            label="database backup",
        ),
    }
    for role in ("candidate", "rollback"):
        wheels = []
        for item in manifest["artifacts"][role]["files"]:
            path = _contained_file(
                bundle,
                item["path"],
                label=f"{role} artifact",
            )
            if _sha256(path) != item["sha256"]:
                raise RolloutError(f"{role} artifact hash does not match manifest: {path}")
            if path.suffix == ".whl":
                wheels.append(path)
        if len(wheels) != 1:
            raise RolloutError(f"{role} must contain exactly one wheel")
        files[f"{role}_wheel"] = wheels[0]
    if _sha256(files["backup"]) != manifest["backup"]["sha256"]:
        raise RolloutError("database backup hash does not match manifest")
    return manifest, files


def _validate_repository(repo: Path, candidate_commit: str) -> None:
    if _git(repo, "status", "--porcelain"):
        raise RolloutError("repository is dirty; rollout only accepts reviewed committed bytes")
    if _git(repo, "rev-parse", "HEAD^{commit}") != candidate_commit:
        raise RolloutError("running repository commit does not match the prepared candidate")


def _parse_installed_record(path: Path) -> tuple[dict[str, str], bytes, int]:
    _safe_owned_file(
        path,
        label="installed provenance record",
        allow_private_group=True,
    )
    raw = path.read_bytes()
    fields: dict[str, str] = {}
    try:
        lines = raw.decode().splitlines()
    except UnicodeDecodeError as exc:
        raise RolloutError("installed provenance record is not UTF-8") from exc
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) != 2 or parts[0] in fields:
            raise RolloutError("installed provenance record has an invalid or duplicate field")
        fields[parts[0]] = parts[1]
    commit = fields.get("commit")
    invalid_commit = commit is None or len(commit) != 40 or any(
        char not in "0123456789abcdef" for char in commit
    )
    if invalid_commit:
        raise RolloutError("installed provenance record lacks an exact lowercase commit")
    return fields, raw, stat.S_IMODE(path.stat().st_mode)


def _validate_wrapper(wrapper: Path, active: Path) -> None:
    finding = prepare_upgrade._directory_chain_finding(
        wrapper.parent, private_leaf=False
    )
    if finding:
        raise RolloutError(f"unsafe Janus wrapper path: {finding}")
    _safe_owned_file(
        wrapper,
        label="Janus wrapper",
        allow_private_group=True,
    )
    expected = f'exec {active}/bin/janus "$@"'
    try:
        content = wrapper.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        raise RolloutError(f"cannot read Janus wrapper: {wrapper}: {exc}") from exc
    commands = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if commands != [expected]:
        raise RolloutError(f"Janus wrapper does not exec the active environment: {active}")


def _validate_active(
    active: Path,
    install_root: Path,
    *,
    expected_commit: str,
    expected_wheel_sha256: str,
) -> None:
    try:
        info = active.lstat()
    except OSError as exc:
        raise RolloutError(f"cannot inspect active environment: {active}: {exc}") from exc
    if info.st_uid != os.geteuid():
        raise RolloutError(f"active environment is not owned by this process: {active}")
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        return
    if not stat.S_ISLNK(info.st_mode):
        raise RolloutError(f"active environment is neither a directory nor a symlink: {active}")
    target = active.resolve(strict=True)
    releases = install_root / "releases"
    try:
        target.relative_to(releases)
    except ValueError as exc:
        raise RolloutError(f"active environment points outside release storage: {target}") from exc
    if not target.is_dir():
        raise RolloutError(f"active release is not a directory: {target}")
    expected_target = releases / expected_commit
    if target != expected_target:
        raise RolloutError(
            "active release does not match the installed provenance commit: "
            f"{target}"
        )
    marker = _release_marker(target)
    if marker != {
        "commit": expected_commit,
        "wheel_sha256": expected_wheel_sha256,
    }:
        raise RolloutError("active release marker does not match the prepared rollback artifact")


def _snapshot_facts(db: Path) -> tuple[dict, dict]:
    stage = Path(tempfile.mkdtemp(prefix="janus-rollout-snapshot-"))
    stage.chmod(PRIVATE_DIRECTORY_MODE)
    try:
        return prepare_upgrade._backup_database(db, stage / "janus.db")
    except prepare_upgrade.PreparationError as exc:
        raise RolloutError(str(exc)) from exc
    finally:
        shutil.rmtree(stage)


def _require_fresh_snapshot(manifest: dict, db: Path) -> tuple[dict, dict]:
    facts, family = _snapshot_facts(db)
    prepared = manifest["backup"]
    for field in ("migrations", "counts", "content_sha256"):
        if facts[field] != prepared[field]:
            raise RolloutError(
                f"live ledger changed after preparation ({field}); prepare a new bundle"
            )
    if facts["integrity_check"] != "ok":
        raise RolloutError("live ledger integrity check failed")
    return facts, family


def _validate_install_root(install_root: Path) -> None:
    finding = prepare_upgrade._directory_chain_finding(install_root, private_leaf=False)
    if finding:
        raise RolloutError(f"unsafe install root: {finding}")
    try:
        info = install_root.lstat()
    except OSError as exc:
        raise RolloutError(f"cannot inspect install root: {install_root}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RolloutError(f"install root is not a directory: {install_root}")
    if info.st_uid != os.geteuid():
        raise RolloutError(f"install root is not owned by this process: {install_root}")
    finding = prepare_upgrade._source_permission_finding(
        install_root, label="install root", directory=True
    )
    if finding:
        raise RolloutError(finding)


def preflight(
    *,
    bundle: Path,
    db: Path,
    install_root: Path,
    active: Path,
    wrapper: Path,
    repo: Path,
) -> dict:
    for path, option in (
        (bundle, "--bundle"),
        (db, "--db"),
        (install_root, "--install-root"),
        (active, "--active"),
        (wrapper, "--wrapper"),
    ):
        _require_absolute(path, option)
    if active.parent != install_root:
        raise RolloutError("--active must be a direct child of --install-root")
    _validate_install_root(install_root)
    manifest, files = _load_manifest(bundle, db, repo)
    _validate_repository(repo, manifest["source"]["commit"])
    installed, installed_bytes, installed_mode = _parse_installed_record(
        install_root / "INSTALLED"
    )
    if installed["commit"] != manifest["artifacts"]["rollback"]["commit"]:
        raise RolloutError("installed commit does not match the prepared rollback commit")
    _validate_active(
        active,
        install_root,
        expected_commit=installed["commit"],
        expected_wheel_sha256=_wheel_digest(manifest, "rollback"),
    )
    _validate_wrapper(wrapper, active)
    facts, family = _require_fresh_snapshot(manifest, db)
    return {
        "manifest": manifest,
        "files": files,
        "manifest_sha256": _sha256(files["manifest"]),
        "installed": installed,
        "installed_bytes": installed_bytes,
        "installed_mode": installed_mode,
        "facts": facts,
        "family": family,
    }


def _ensure_private_directory(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _safe_private_directory(path, label="rollout directory")
        return
    prepare_upgrade._private_directory(path)


def _atomic_private_file(path: Path, content: bytes, *, mode: int = PRIVATE_FILE_MODE) -> None:
    temporary = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        prepare_upgrade._fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _release_marker(release: Path) -> dict:
    marker = release / "JANUS_RELEASE.json"
    _safe_owned_file(marker, label="release marker", expected_mode=PRIVATE_FILE_MODE)
    try:
        return json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RolloutError(f"invalid release marker: {marker}: {exc}") from exc


def _stage_release(
    *,
    wheel: Path,
    wheel_sha256: str,
    commit: str,
    releases: Path,
) -> Path:
    release = releases / commit
    if release.exists() or release.is_symlink():
        _safe_private_directory(release, label="existing release")
        marker = _release_marker(release)
        if marker != {"commit": commit, "wheel_sha256": wheel_sha256}:
            raise RolloutError(f"existing release does not match requested artifact: {release}")
        return release

    prepare_upgrade._private_directory(release)
    published = False
    try:
        # Virtual environments embed absolute paths in entry-point shebangs and
        # pyvenv.cfg, so building elsewhere and renaming creates a broken
        # release. The exact commit directory is private and never activated
        # until the completion marker is durable.
        _run([sys.executable, "-m", "venv", str(release)], cwd=releases, isolated_python=True)
        python = release / "bin" / "python"
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(wheel),
            ],
            cwd=releases,
            isolated_python=True,
        )
        _atomic_private_file(
            release / "JANUS_RELEASE.json",
            (json.dumps({"commit": commit, "wheel_sha256": wheel_sha256}, sort_keys=True)
             + "\n").encode(),
        )
        published = True
        prepare_upgrade._fsync_directory(releases)
        return release
    finally:
        if not published:
            shutil.rmtree(release, ignore_errors=True)


def _wheel_digest(manifest: dict, role: str) -> str:
    return next(
        item["sha256"]
        for item in manifest["artifacts"][role]["files"]
        if item["path"].endswith(".whl")
    )


def _maintenance_environment(install_root: Path) -> Path:
    maintenance = install_root / "maintenance"
    if maintenance.exists():
        _safe_private_directory(maintenance, label="maintenance environment")
        binary = maintenance / "bin" / "janus"
        _safe_owned_file(
            binary,
            label="maintenance refusal",
            expected_mode=EXECUTABLE_FILE_MODE,
        )
        if "rollout maintenance in progress" not in binary.read_text():
            raise RolloutError("existing maintenance environment has unexpected content")
        return maintenance
    prepare_upgrade._private_directory(maintenance)
    binary = maintenance / "bin"
    prepare_upgrade._private_directory(binary)
    message = (
        "#!/bin/sh\n"
        "echo 'janus: rollout maintenance in progress; no command was run' >&2\n"
        "exit 75\n"
    ).encode()
    _atomic_private_file(binary / "janus", message, mode=EXECUTABLE_FILE_MODE)
    return maintenance


def _switch_symlink(active: Path, target: Path) -> None:
    temporary = active.parent / f".{active.name}.switch-{uuid.uuid4().hex}"
    os.symlink(str(target), temporary)
    try:
        os.replace(temporary, active)
        prepare_upgrade._fsync_directory(active.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _plan_maintenance(active: Path, legacy_root: Path, rollback: str) -> dict:
    info = active.lstat()
    if stat.S_ISLNK(info.st_mode):
        return {
            "kind": "symlink",
            "target": os.readlink(active),
            "legacy": None,
            "device": info.st_dev,
            "inode": info.st_ino,
        }
    if not stat.S_ISDIR(info.st_mode):
        raise RolloutError("active environment changed type before maintenance")
    legacy = legacy_root / f"{rollback}-{_now().replace(':', '').replace('-', '')}"
    if legacy.exists() or legacy.is_symlink():
        raise RolloutError(f"legacy preservation path already exists: {legacy}")
    return {
        "kind": "directory",
        "target": None,
        "legacy": str(legacy),
        "device": info.st_dev,
        "inode": info.st_ino,
    }


def _require_active_matches_plan(active: Path, previous: dict) -> None:
    try:
        info = active.lstat()
    except OSError as exc:
        raise RolloutError(f"active environment changed before maintenance: {exc}") from exc
    if (info.st_dev, info.st_ino) != (previous["device"], previous["inode"]):
        raise RolloutError("active environment changed after recovery state was recorded")
    if previous["kind"] == "symlink":
        if not stat.S_ISLNK(info.st_mode) or os.readlink(active) != previous["target"]:
            raise RolloutError("active environment changed after recovery state was recorded")
        return
    if previous["kind"] != "directory" or not stat.S_ISDIR(info.st_mode):
        raise RolloutError("active environment changed after recovery state was recorded")


def _active_matches_previous(active: Path, previous: dict) -> bool:
    try:
        info = active.lstat()
    except OSError:
        return False
    if previous["kind"] == "symlink":
        return stat.S_ISLNK(info.st_mode) and os.readlink(active) == previous["target"]
    return (
        previous["kind"] == "directory"
        and stat.S_ISDIR(info.st_mode)
        and (info.st_dev, info.st_ino) == (previous["device"], previous["inode"])
    )


def _enter_maintenance(active: Path, maintenance: Path, previous: dict) -> None:
    _require_active_matches_plan(active, previous)
    if previous["kind"] == "symlink":
        try:
            _switch_symlink(active, maintenance)
        except BaseException as primary:
            try:
                _restore_active(active, previous)
            except BaseException as recovery:
                raise RolloutError(
                    f"{primary}; failed to restore prior active symlink: {recovery}"
                ) from primary
            raise
        return
    legacy = Path(previous["legacy"])
    if legacy.exists() or legacy.is_symlink():
        raise RolloutError(f"legacy preservation path already exists: {legacy}")
    temporary = active.parent / f".{active.name}.switch-{uuid.uuid4().hex}"
    os.symlink(str(maintenance), temporary)
    moved = False
    switched = False
    try:
        os.replace(active, legacy)
        moved = True
        os.replace(temporary, active)
        switched = True
        prepare_upgrade._fsync_directory(active.parent)
    except BaseException as primary:
        temporary.unlink(missing_ok=True)
        if moved:
            try:
                if switched and active.is_symlink():
                    active.unlink()
                if not active.exists() and not active.is_symlink():
                    os.replace(legacy, active)
                    prepare_upgrade._fsync_directory(active.parent)
            except BaseException as recovery:
                raise RolloutError(
                    f"{primary}; failed to restore legacy active environment: {recovery}"
                ) from primary
        raise


def _restore_active(active: Path, previous: dict) -> None:
    if previous["kind"] == "symlink":
        _switch_symlink(active, Path(previous["target"]))
        return
    legacy = Path(previous["legacy"])
    if active.is_symlink():
        active.unlink()
    elif active.exists():
        raise RolloutError("cannot restore legacy environment over an unexpected active path")
    os.replace(legacy, active)
    prepare_upgrade._fsync_directory(active.parent)


@contextmanager
def _rollout_lock(install_root: Path):
    path = install_root / "rollout.lock"
    try:
        descriptor = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            PRIVATE_FILE_MODE,
        )
    except OSError as exc:
        raise RolloutError(f"cannot open rollout lock safely: {path}: {exc}") from exc
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise RolloutError(f"unsafe rollout lock: {path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RolloutError("another Janus rollout holds the install lock") from exc
        yield
    finally:
        os.close(descriptor)


def _open_database_holders(db: Path) -> list[str]:
    command = shutil.which("lsof")
    if command is None:
        raise RolloutError("lsof is required to prove the live database family is quiescent")
    family = [str(db), str(db) + "-wal", str(db) + "-shm", str(db) + "-journal"]
    completed = subprocess.run(
        [command, "-t", "--", *family], text=True, capture_output=True, check=False
    )
    holders = sorted({line for line in completed.stdout.splitlines() if line.isdigit()})
    if completed.returncode not in {0, 1}:
        raise RolloutError(
            f"lsof could not inspect the database family: {completed.stderr.strip()}"
        )
    return holders


def _repair_storage(db: Path) -> dict:
    try:
        prepare_upgrade._source_family(db)
    except prepare_upgrade.PreparationError as exc:
        raise RolloutError(str(exc)) from exc
    db.parent.chmod(PRIVATE_DIRECTORY_MODE)
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(db) + suffix)
        if path.exists() or path.is_symlink():
            _safe_owned_file(path, label="database family member")
            path.chmod(PRIVATE_FILE_MODE)
    try:
        family = prepare_upgrade._source_family(db)
    except prepare_upgrade.PreparationError as exc:
        raise RolloutError(str(exc)) from exc
    if family["directory"]["mode"] != "0700":
        raise RolloutError("ledger directory permission repair did not persist")
    if any(entry["mode"] != "0600" for name, entry in family.items() if name != "directory"):
        raise RolloutError("database-family permission repair did not persist")
    return family


def _verify_environment(environment: Path, code: str, db: Path, *, label: str) -> dict:
    completed = _run(
        [str(environment / "bin" / "python"), "-c", code, str(db)],
        cwd=environment,
        capture=True,
        isolated_python=True,
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RolloutError(f"{label} returned invalid verification output") from exc


def _atomic_json(path: Path, document: dict, schema: dict | None = None) -> None:
    if schema is not None:
        _validate_json(document, schema, label="rollout receipt")
    content = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    _atomic_private_file(path, content)


def _installed_content(
    *,
    candidate: str,
    candidate_release: Path,
    rollback: str,
    rollback_release: Path,
    manifest_sha256: str,
) -> bytes:
    return (
        f"installed_from  prepared bundle\n"
        f"commit          {candidate}\n"
        f"release         {candidate_release}\n"
        f"rollback_commit {rollback}\n"
        f"rollback_release {rollback_release}\n"
        f"manifest_sha256 {manifest_sha256}\n"
        f"installed_at    {_now()}\n"
        "reinstall       scripts/apply_upgrade.py\n"
    ).encode()


def _installed_recovery_record(path: Path, content: bytes, mode: int) -> dict[str, str]:
    return {
        "content_base64": base64.b64encode(content).decode("ascii"),
        "mode": f"{mode:04o}",
        "path": str(path),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _restore_installed_record(record: dict, *, expected_path: Path) -> None:
    if record.get("path") != str(expected_path):
        raise RolloutError("installed recovery record targets an unexpected path")
    encoded = record.get("content_base64")
    if not isinstance(encoded, str):
        raise RolloutError("installed recovery record lacks encoded content")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise RolloutError("installed recovery record content is not valid base64") from exc
    if hashlib.sha256(content).hexdigest() != record.get("sha256"):
        raise RolloutError("installed recovery record content digest does not match")
    mode_text = record.get("mode")
    try:
        mode = int(mode_text, 8)
    except (TypeError, ValueError) as exc:
        raise RolloutError("installed recovery record has an invalid mode") from exc
    if mode_text != f"{mode:04o}" or mode & ~0o777:
        raise RolloutError("installed recovery record has an invalid mode")
    _atomic_private_file(expected_path, content, mode=mode)


def apply_upgrade(
    *,
    bundle: Path,
    db: Path,
    install_root: Path,
    active: Path,
    wrapper: Path,
    repo: Path,
) -> Path:
    initial = preflight(
        bundle=bundle,
        db=db,
        install_root=install_root,
        active=active,
        wrapper=wrapper,
        repo=repo,
    )
    manifest = initial["manifest"]
    candidate = manifest["source"]["commit"]
    rollback = manifest["artifacts"]["rollback"]["commit"]
    install_root.chmod(PRIVATE_DIRECTORY_MODE)
    journal = install_root / "ROLLOUT_IN_PROGRESS.json"
    previous: dict | None = None
    maintenance_entered = False
    installed_changed = False
    completed = False
    receipt_path: Path | None = None
    receipt_attempted = False
    with _rollout_lock(install_root):
        if journal.exists() or journal.is_symlink():
            raise RolloutError(
                "unfinished rollout journal exists; inspect and recover before retrying: "
                f"{journal}"
            )
        releases = install_root / "releases"
        receipts = install_root / "receipts"
        legacy = install_root / "legacy"
        for directory in (releases, receipts, legacy):
            _ensure_private_directory(directory)
        candidate_release = _stage_release(
            wheel=initial["files"]["candidate_wheel"],
            wheel_sha256=_wheel_digest(manifest, "candidate"),
            commit=candidate,
            releases=releases,
        )
        rollback_release = _stage_release(
            wheel=initial["files"]["rollback_wheel"],
            wheel_sha256=_wheel_digest(manifest, "rollback"),
            commit=rollback,
            releases=releases,
        )
        maintenance = _maintenance_environment(install_root)
        try:
            previous = _plan_maintenance(active, legacy, rollback)
            installed_record_before = _installed_recovery_record(
                install_root / "INSTALLED",
                initial["installed_bytes"],
                initial["installed_mode"],
            )
            journal_document = {
                "schema": "janus.rollout-in-progress.v1",
                "candidate_commit": candidate,
                "rollback_commit": rollback,
                "active_environment": str(active),
                "started_at": _now(),
                "step": "entering_maintenance",
                "previous": previous,
                "installed_record_before": installed_record_before,
            }
            _atomic_json(journal, journal_document)
            _enter_maintenance(active, maintenance, previous)
            maintenance_entered = True
            journal_document["step"] = "maintenance"
            _atomic_json(journal, journal_document)
            holders = _open_database_holders(db)
            if holders:
                raise RolloutError(
                    "live database family is still open by process(es): " + ", ".join(holders)
                )
            facts_before, _ = _require_fresh_snapshot(manifest, db)
            holders = _open_database_holders(db)
            if holders:
                raise RolloutError(
                    "database family gained an open holder during freshness proof: "
                    + ", ".join(holders)
                )
            family_before = initial["family"]["before"]
            _repair_storage(db)
            journal_document["step"] = "migrating"
            _atomic_json(journal, journal_document)
            candidate_result = _verify_environment(
                candidate_release,
                prepare_upgrade._verifier_code(),
                db,
                label="candidate",
            )
            expected_candidate = manifest["rehearsal"]["candidate"]
            if candidate_result != expected_candidate:
                raise RolloutError("candidate live verification differs from the rehearsal")
            rollback_result = _verify_environment(
                rollback_release,
                prepare_upgrade._rollback_verifier_code(),
                db,
                label="rollback",
            )
            expected_rollback = manifest["rehearsal"]["rollback"]
            if rollback_result != expected_rollback:
                raise RolloutError("rollback reader differs from the rehearsal")
            facts_after, family_after = _snapshot_facts(db)
            if facts_after["counts"] != facts_before["counts"]:
                raise RolloutError("candidate migration changed ledger row counts")
            if facts_after["content_sha256"] != facts_before["content_sha256"]:
                raise RolloutError("candidate migration changed ledger content")
            if facts_after["migrations"] != expected_candidate["migrations"]:
                raise RolloutError("candidate migration history differs from the rehearsal")
            if family_after["after"]["directory"]["mode"] != "0700":
                raise RolloutError("ledger directory lost private mode after verification")
            if any(
                item["mode"] != "0600"
                for name, item in family_after["after"].items()
                if name != "directory"
            ):
                raise RolloutError("database family lost private mode after verification")

            journal_document["step"] = "activating"
            _atomic_json(journal, journal_document)
            _switch_symlink(active, candidate_release)
            installed_content = _installed_content(
                candidate=candidate,
                candidate_release=candidate_release,
                rollback=rollback,
                rollback_release=rollback_release,
                manifest_sha256=initial["manifest_sha256"],
            )
            installed_changed = True
            _atomic_private_file(install_root / "INSTALLED", installed_content)
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "completed_at": _now(),
                "preparation": {
                    "bundle": str(bundle),
                    "manifest_sha256": initial["manifest_sha256"],
                    "candidate_commit": candidate,
                    "rollback_commit": rollback,
                    "backup_sha256": manifest["backup"]["sha256"],
                },
                "target": {
                    "database": str(db),
                    "install_root": str(install_root),
                    "active_environment": str(active),
                    "wrapper": str(wrapper),
                },
                "before": {
                    "installed_commit": rollback,
                    "database": facts_before,
                    "storage": family_before,
                },
                "after": {
                    "installed_commit": candidate,
                    "database": facts_after,
                    "storage": family_after["after"],
                },
                "releases": {
                    "candidate": {
                        "commit": candidate,
                        "environment": str(candidate_release),
                        "wheel_sha256": _wheel_digest(manifest, "candidate"),
                    },
                    "rollback": {
                        "commit": rollback,
                        "environment": str(rollback_release),
                        "wheel_sha256": _wheel_digest(manifest, "rollback"),
                    },
                    "legacy_environment": previous["legacy"],
                },
                "steps": COMPLETED_STEPS,
                "rollback": {
                    "code_environment": str(rollback_release),
                    "database_backup": str(initial["files"]["backup"]),
                    "database_restore_automatic": False,
                },
                "semantics": {
                    "authority": "external_to_janus",
                    "receipt_is_authority": False,
                },
                "result": "completed",
                "deployment_performed": True,
            }
            receipt_name = f"{receipt['completed_at'].replace(':', '')}-{candidate}.json"
            receipt_path = receipts / receipt_name
            if receipt_path.exists() or receipt_path.is_symlink():
                raise RolloutError(f"rollout receipt path already exists: {receipt_path}")
            receipt_attempted = True
            _atomic_json(
                receipt_path,
                receipt,
                _schema(repo, "docs/spec/rollout-receipt-v1.schema.json"),
            )
            journal.unlink()
            prepare_upgrade._fsync_directory(install_root)
            completed = True
            return receipt_path
        except BaseException as primary:
            recovery_error: BaseException | None = None
            if previous is not None and maintenance_entered:
                try:
                    _restore_active(active, previous)
                    if installed_changed:
                        _restore_installed_record(
                            installed_record_before,
                            expected_path=install_root / "INSTALLED",
                        )
                except BaseException as exc:
                    recovery_error = exc
            elif previous is not None:
                try:
                    prior_state_is_intact = _active_matches_previous(active, previous)
                except OSError:
                    prior_state_is_intact = False
                if not prior_state_is_intact:
                    recovery_error = RolloutError(
                        "active environment changed after its prior state was recorded"
                    )
            if receipt_attempted and receipt_path is not None:
                try:
                    receipt_path.unlink(missing_ok=True)
                    prepare_upgrade._fsync_directory(receipt_path.parent)
                except BaseException as exc:
                    if recovery_error is None:
                        recovery_error = exc
            if recovery_error is None:
                journal.unlink(missing_ok=True)
                prepare_upgrade._fsync_directory(install_root)
            else:
                raise RolloutError(
                    f"{primary}; active-environment recovery also failed: {recovery_error}; "
                    f"inspect {journal}"
                ) from primary
            raise
        finally:
            if completed and not active.is_symlink():
                raise RolloutError("completed rollout lost its active-environment symlink")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "verify or apply an exact prepared Janus upgrade; this command never reads "
            "Janus as authority"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "apply"):
        command = subparsers.add_parser(name)
        command.add_argument("--bundle", required=True, type=Path)
        command.add_argument("--db", required=True, type=Path)
        command.add_argument("--install-root", required=True, type=Path)
        command.add_argument("--active", required=True, type=Path)
        command.add_argument("--wrapper", required=True, type=Path)
        if name == "apply":
            command.add_argument(
                "--yes",
                action="store_true",
                help="acknowledge the displayed live effects; not proof of authorization",
            )
    return parser


def _print_plan(result: dict, *, apply: bool) -> None:
    manifest = result["manifest"]
    print(f"candidate      {manifest['source']['commit']}")
    print(f"rollback       {manifest['artifacts']['rollback']['commit']}")
    print(f"database       {manifest['live_source']['database']}")
    print(f"backup sha256  {manifest['backup']['sha256']}")
    print("freshness      live migrations, counts, and content digests match preparation")
    print("authority      external to Janus; this evidence does not grant permission")
    if apply:
        print("effects        stage code; enter maintenance; chmod database family; migrate; ")
        print("               activate candidate; write installed provenance and receipt")
        print("rollback       code environment retained; database restore is never automatic")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        result = preflight(
            bundle=args.bundle,
            db=args.db,
            install_root=args.install_root,
            active=args.active,
            wrapper=args.wrapper,
            repo=repo,
        )
        _print_plan(result, apply=args.command == "apply")
        if args.command == "preflight":
            print("preflight only  no install, chmod, migration, activation, or receipt write")
            return 0
        if not args.yes:
            raise RolloutError("apply requires --yes after reviewing the effects above")
        receipt = apply_upgrade(
            bundle=args.bundle,
            db=args.db,
            install_root=args.install_root,
            active=args.active,
            wrapper=args.wrapper,
            repo=repo,
        )
    except RolloutError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 2
    print(f"receipt        {receipt}")
    print("deployed       candidate active; receipt remains evidence, never authority")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
