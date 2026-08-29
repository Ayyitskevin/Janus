"""Janus core — the ledger, seat attribution, and bindings.

> Janus records pending authority; it does not grant authority.

Nothing in this module answers "is this authorized?". It answers "did a human
rule, on what bytes, and when". Keeping that line is the whole point: an
approval record is evidence, and evidence does not confer authority.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("irreversible", "authority", "taste", "resource")
RULED_STATES = ("approved", "refused")
TERMINAL_STATES = ("approved", "refused", "expired", "withdrawn", "superseded")
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_DB = Path(os.environ.get("JANUS_DB", Path.home() / ".janus" / "janus.db"))
_SEAT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class JanusError(RuntimeError):
    """A refusal the operator should read, not a stack trace."""


class StorageBoundaryError(JanusError):
    """A storage refusal, with the unadorned finding retained for diagnostics."""

    def __init__(self, message: str, *, finding: str | None = None) -> None:
        super().__init__(message)
        self.finding = finding or message


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- seat ----
def seat_actor(declared: str | None = None) -> str:
    """Attribution is `<os_user>` or `<os_user>+<seat>` — never the seat alone.

    A declared seat is a *claim* appended to an OS identity the caller could not
    forge locally. It never replaces that identity, so a seat label can add
    detail but can never impersonate. M1 has no remote caller by design; if one
    ever exists it must not reach this function.
    """
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    seat = declared if declared is not None else os.environ.get("JANUS_SEAT")
    if not seat:
        return user
    seat = seat.strip().lower()
    if not _SEAT_RE.match(seat):
        raise JanusError(
            f"seat {seat!r} is not a plain lowercase label (a-z0-9._-, max 32)"
        )
    return f"{user}+{seat}"


# ------------------------------------------------------------- binding ----
@dataclass(frozen=True)
class Binding:
    kind: str
    locator: str
    sha256: str


def digest_file(path: str | Path) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise JanusError(f"cannot bind: {p} is not a readable file")
    h = hashlib.sha256()
    try:
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError as e:
        detail = e.strerror or type(e).__name__
        raise JanusError(f"cannot bind: {p} is not readable ({detail})") from e
    return h.hexdigest()


def git_commit_sha(repo: str | Path, rev: str) -> str:
    """The full commit id a revision names right now, or a JanusError."""
    repo = Path(repo).expanduser()
    p = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{rev}^{{commit}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if p.returncode != 0:
        raise JanusError(f"cannot bind: git could not resolve {rev!r} in {repo}")
    return p.stdout.strip()


def digest_git_object(repo: str | Path, rev: str) -> str:
    """Bind a git revision by the commit's own object id, resolved to full sha.

    Binding a *commit* rather than a worktree digest is deliberate: the artifact
    under decision is usually "this exact commit", and a dirty worktree must not
    silently change what was approved.
    """
    # A git object id is sha-1 (40) today; the column wants a 64-char digest, so
    # we store the sha256 OF the object id. It still pins exact bytes, and it
    # keeps one digest column honest instead of two half-used ones.
    return hashlib.sha256(git_commit_sha(repo, rev).encode()).hexdigest()


def resolve_binding(kind: str | None, locator: str | None) -> Binding | None:
    if kind is None and locator is None:
        return None
    if kind is None or locator is None:
        raise JanusError("a binding needs both --bind-kind and --bind (or neither)")
    if kind == "file":
        # ABSOLUTE, resolved at raise time. A relative locator is meaningless to
        # every reader but the one process that raised the gate: the first gate
        # raised by another seat (2026-08-24) bound "docs/adr/0054-....md" and
        # reads CANNOT VERIFY to anyone whose cwd differs. A binding exists to be
        # re-checked by someone else later, so it must not depend on where the
        # raiser happened to be standing.
        return Binding(kind, str(Path(locator).expanduser().resolve()),
                       digest_file(locator))
    if kind == "git":
        if "@" not in locator:
            raise JanusError("git binding locator must be '<repo>@<rev>'")
        repo, rev = locator.rsplit("@", 1)
        # Resolved at raise time in BOTH dimensions, and for two different
        # reasons. The repo path is made absolute for the same reason the file
        # branch above is. The revision is pinned to a concrete commit because
        # "<repo>@HEAD" binds a NAME, and invariant 2 is that a ruling binds a
        # digest and not a name: every later commit moves HEAD, so such a gate
        # reads BINDING NO LONGER MATCHES because the world moved at all, not
        # because the reviewed bytes changed. Drift that fires on unrelated
        # commits teaches the reader to ignore drift, which is worse than no
        # drift check. Found by adoption: gate g55daf244a78 bound "<repo>@HEAD"
        # and went void one unrelated commit later.
        repo_abs = Path(repo).expanduser().resolve()
        sha = git_commit_sha(repo_abs, rev)
        return Binding(kind, f"{repo_abs}@{sha}", hashlib.sha256(sha.encode()).hexdigest())
    if kind == "text":
        return Binding(kind, "inline", hashlib.sha256(locator.encode()).hexdigest())
    raise JanusError(f"unknown binding kind {kind!r} (file | git | text)")


def verify_binding(kind: str, locator: str, expected: str) -> tuple[bool | None, str]:
    """Re-derive a binding. Returns (matches, human sentence).

    `None` means "cannot tell" — which must never read as "fine". `janus show`
    prints this verbatim; Janus states the drift and stops there, because
    enforcing it would put Janus in the permission path.
    """
    try:
        if kind == "file":
            actual = digest_file(locator)
        elif kind == "git":
            repo, rev = locator.rsplit("@", 1)
            actual = digest_git_object(repo, rev)
        elif kind == "text":
            return None, "inline text binding — nothing live to re-check"
        else:
            return None, f"unknown binding kind {kind!r}"
    except JanusError as e:
        return None, f"CANNOT VERIFY — {e}"
    if actual == expected:
        return True, "binding matches: the bound bytes are the live bytes"
    return False, (
        "BINDING NO LONGER MATCHES — the artifact changed since it was bound. "
        "The binding still identifies the earlier bytes; it does not follow "
        "later changes."
    )


def binding_basis(gate: dict) -> tuple[str | None, str]:
    """Return the digest current applicability is compared with, and its basis.

    An open gate names the bytes present when it was raised.  A ruled gate names
    the bytes present when the human actually ruled, which may be different.
    Confusing those two makes a legitimate pre-ruling edit look like post-ruling
    drift and is especially misleading once delivery evidence exists.
    """
    ruling = gate.get("ruling")
    if ruling and ruling["state"] in RULED_STATES and ruling["bound_sha256"]:
        return ruling["bound_sha256"], "ruling"
    return gate.get("binding_sha256"), "raise"


# ------------------------------------------------------------ database ----
def storage_path(db_path: Path | None = None) -> Path:
    """Return one absolute, lexically normalized path without following links."""
    expanded = Path(db_path or DEFAULT_DB).expanduser()
    return Path(os.path.abspath(expanded))


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        raise StorageBoundaryError(
            f"cannot inspect storage path {path}: {detail}"
        ) from exc


def _effective_uid() -> int:
    try:
        return os.geteuid()
    except AttributeError as exc:
        raise StorageBoundaryError(
            "ledger privacy requires POSIX ownership and mode semantics"
        ) from exc


def _directory_chain_finding(
    leaf: Path, *, private_leaf: bool, protect_child: bool = False
) -> str | None:
    """Explain why another OS user could replace an entry in ``leaf``'s path."""
    entries: list[tuple[Path, os.stat_result]] = []
    cursor = leaf
    while True:
        info = _lstat(cursor)
        if info is None:
            return f"directory does not exist: {cursor}"
        if stat.S_ISLNK(info.st_mode):
            return f"directory path contains a symbolic link: {cursor}"
        if not stat.S_ISDIR(info.st_mode):
            return f"directory path contains a non-directory: {cursor}"
        entries.append((cursor, info))
        if cursor == cursor.parent:
            break
        cursor = cursor.parent

    expected_uid = _effective_uid()
    for target, info in entries:
        if info.st_uid not in {0, expected_uid}:
            return (
                f"directory owner uid {info.st_uid} is neither root nor process uid "
                f"{expected_uid}: {target}"
            )

    leaf_mode = stat.S_IMODE(entries[0][1].st_mode)
    if private_leaf:
        if entries[0][1].st_uid != expected_uid:
            return f"ledger directory is not owned by process uid {expected_uid}: {leaf}"
        if leaf_mode != PRIVATE_DIRECTORY_MODE:
            return (
                f"ledger directory mode {leaf_mode:04o} (expected 0700): {leaf}"
            )

    # A writable parent controls the name of its child. Sticky directories such
    # as /tmp are the exception: another unprivileged user cannot replace a
    # child owned by this user (or root). This turns the pathname reopen between
    # os.open and SQLite into a same-user/root race, both inside the threat model.
    if protect_child and (leaf_mode & 0o022) and not (leaf_mode & stat.S_ISVTX):
        return f"directory permits another OS user to replace a child entry: {leaf}"
    for (child, child_info), (parent, parent_info) in zip(entries, entries[1:]):
        parent_mode = stat.S_IMODE(parent_info.st_mode)
        if not (parent_mode & 0o022):
            continue
        if parent_mode & stat.S_ISVTX and child_info.st_uid in {0, expected_uid}:
            continue
        return f"directory permits another OS user to replace {child.name}: {parent}"
    return None


def _missing_parents(parent: Path) -> tuple[list[Path], Path]:
    missing: list[Path] = []
    cursor = parent
    while _lstat(cursor) is None:
        missing.append(cursor)
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    return missing, cursor


def _new_storage_parent_finding(parent: Path) -> str | None:
    missing, anchor = _missing_parents(parent)
    finding = _directory_chain_finding(
        anchor,
        private_leaf=not missing,
        protect_child=bool(missing),
    )
    return finding


def _create_private_parents(parent: Path) -> None:
    """Create every missing parent privately without changing existing paths.

    ``Path.mkdir(parents=True, mode=...)`` deliberately ignores ``mode`` for
    intermediate parents, so using it here would expose a nested ledger when
    the caller has a permissive umask.
    Source: https://docs.python.org/3.11/library/pathlib.html#pathlib.Path.mkdir
    """
    missing, _ = _missing_parents(parent)
    finding = _new_storage_parent_finding(parent)
    if finding:
        raise StorageBoundaryError(
            f"cannot create a private ledger: {finding}", finding=finding
        )
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        except FileExistsError:
            info = _lstat(directory)
            if (
                info is None
                or not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != _effective_uid()
                or stat.S_IMODE(info.st_mode) != PRIVATE_DIRECTORY_MODE
            ):
                finding = f"unsafe directory appeared: {directory}"
                raise StorageBoundaryError(
                    f"cannot create a private ledger: {finding}", finding=finding
                )
            continue
        except OSError as exc:
            detail = exc.strerror or type(exc).__name__
            raise StorageBoundaryError(
                f"cannot create ledger directory {directory}: {detail}"
            ) from exc
        # mkdir combines mode with umask. Widen only the directory this process
        # just created to the exact owner-only contract; existing paths are never
        # repaired as a side effect of opening Janus.
        try:
            directory.chmod(PRIVATE_DIRECTORY_MODE)
        except OSError as exc:
            detail = exc.strerror or type(exc).__name__
            raise StorageBoundaryError(
                f"cannot secure new ledger directory {directory}: {detail}"
            ) from exc
    finding = _directory_chain_finding(parent, private_leaf=True)
    if finding:
        raise StorageBoundaryError(
            f"cannot create a private ledger: {finding}", finding=finding
        )


def _unlink_created_file(path: Path, created: os.stat_result) -> bool:
    """Remove only the directory entry that still names our exact new inode."""
    try:
        current = _lstat(path)
    except StorageBoundaryError:
        # Cleanup uncertainty must preserve both the entry and the primary
        # hardening refusal. It can never justify unlinking an unverified name.
        return False
    if current is None:
        return True
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_dev != created.st_dev
        or current.st_ino != created.st_ino
        or current.st_nlink != 1
    ):
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _create_private_database(path: Path) -> None:
    """Reserve a new ledger as owner-only before SQLite can create it broadly."""
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    except FileExistsError as exc:
        finding = f"database path appeared during private creation: {path}"
        raise StorageBoundaryError(
            f"{finding}; inspect and retry", finding=finding
        ) from exc
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        raise StorageBoundaryError(
            f"cannot create ledger file {path}: {detail}"
        ) from exc

    def close_descriptor() -> OSError | None:
        nonlocal descriptor
        current = descriptor
        descriptor = -1
        try:
            os.close(current)
        except OSError as exc:
            return exc
        return None

    try:
        created = os.fstat(descriptor)
    except OSError as exc:
        close_error = close_descriptor()
        detail = exc.strerror or type(exc).__name__
        close_detail = ""
        if close_error:
            close_detail = f"; descriptor close also failed: {close_error}"
        raise StorageBoundaryError(
            f"cannot inspect newly created ledger {path}: {detail}{close_detail}; "
            "the private entry was retained for operator inspection"
        ) from exc

    # os.open applies umask to its mode. fchmod operates on the descriptor that
    # won O_EXCL, avoiding a close/reopen pathname race and making the new file
    # exactly 0600 even under an over-restrictive ambient umask.
    # Sources: https://docs.python.org/3.11/library/os.html#os.open
    #          https://docs.python.org/3.11/library/os.html#os.fchmod
    hardening_error: BaseException | None = None
    close_error: OSError | None = None
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    except (AttributeError, OSError) as exc:
        hardening_error = exc
    finally:
        close_error = close_descriptor()

    if hardening_error:
        removed = _unlink_created_file(path, created)
        suffix = "" if removed else "; the created entry could not be safely removed"
        if close_error:
            suffix += f"; descriptor close also failed: {close_error}"
        raise StorageBoundaryError(
            f"cannot secure new ledger {path}{suffix}"
        ) from hardening_error
    if close_error:
        detail = close_error.strerror or type(close_error).__name__
        raise StorageBoundaryError(
            f"cannot finalize new ledger {path}: {detail}; "
            "the private entry was retained for operator inspection"
        ) from close_error

    current = _lstat(path)
    if (
        current is None
        or not stat.S_ISREG(current.st_mode)
        or current.st_dev != created.st_dev
        or current.st_ino != created.st_ino
        or current.st_uid != _effective_uid()
        or current.st_nlink != 1
        or stat.S_IMODE(current.st_mode) != PRIVATE_FILE_MODE
    ):
        removed = _unlink_created_file(path, created)
        suffix = "" if removed else "; the created entry could not be safely removed"
        raise StorageBoundaryError(
            f"new ledger failed its identity check: {path}{suffix}"
        )


def storage_open_blocker(db_path: Path | None = None) -> str | None:
    """Return a path-identity reason SQLite must not open, without repairing it."""
    path = storage_path(db_path)
    info = _lstat(path)
    if info is None:
        return f"database is missing: {path}"
    if stat.S_ISLNK(info.st_mode):
        return f"database is a symbolic link: {path}"
    if not stat.S_ISREG(info.st_mode):
        return f"database is not a regular file: {path}"
    expected_uid = _effective_uid()
    if info.st_uid != expected_uid:
        return (
            f"database owner uid {info.st_uid} does not match process uid "
            f"{expected_uid}: {path}"
        )
    if info.st_nlink != 1:
        return f"database has {info.st_nlink} hard links (expected 1): {path}"
    database_mode = stat.S_IMODE(info.st_mode)
    if database_mode != PRIVATE_FILE_MODE:
        return f"database mode {database_mode:04o} (expected 0600): {path}"
    chain_finding = _directory_chain_finding(
        path.parent,
        private_leaf=True,
    )
    if chain_finding:
        return chain_finding
    for suffix, label in (
        ("-wal", "WAL"),
        ("-shm", "shared memory"),
        ("-journal", "rollback journal"),
    ):
        sidecar = Path(f"{path}{suffix}")
        sidecar_info = _lstat(sidecar)
        if sidecar_info is None:
            continue
        if stat.S_ISLNK(sidecar_info.st_mode):
            return f"{label} is a symbolic link: {sidecar}"
        if not stat.S_ISREG(sidecar_info.st_mode):
            return f"{label} is not a regular file: {sidecar}"
        if sidecar_info.st_uid != expected_uid:
            return (
                f"{label} owner uid {sidecar_info.st_uid} does not match process uid "
                f"{expected_uid}: {sidecar}"
            )
        if sidecar_info.st_nlink != 1:
            return f"{label} has {sidecar_info.st_nlink} hard links (expected 1): {sidecar}"
        sidecar_mode = stat.S_IMODE(sidecar_info.st_mode)
        if sidecar_mode != PRIVATE_FILE_MODE:
            return f"{label} mode {sidecar_mode:04o} (expected 0600): {sidecar}"
    return None


def storage_privacy_findings(db_path: Path | None = None) -> list[str]:
    """Return explicit ownership/type/mode findings for the active DB family.

    Inspection is deliberately non-repairing. Existing storage may be live;
    changing it inside ``doctor`` would turn a diagnostic into a migration.
    """
    path = storage_path(db_path)
    findings: list[str] = []
    expected_uid = _effective_uid()

    def inspect_file(
        label: str,
        target: Path,
        expected_mode: int,
        *,
        required: bool,
    ) -> None:
        try:
            info = _lstat(target)
        except StorageBoundaryError as exc:
            findings.append(exc.finding)
            return
        if info is None:
            if required:
                findings.append(f"{label} is missing: {target}")
            return
        if stat.S_ISLNK(info.st_mode):
            findings.append(f"{label} is a symbolic link: {target}")
            return
        if not stat.S_ISREG(info.st_mode):
            findings.append(f"{label} is not a regular file: {target}")
            return
        if info.st_uid != expected_uid:
            findings.append(
                f"{label} owner uid {info.st_uid} does not match process uid "
                f"{expected_uid}: {target}"
            )
        actual_mode = stat.S_IMODE(info.st_mode)
        if actual_mode != expected_mode:
            findings.append(
                f"{label} mode {actual_mode:04o} (expected {expected_mode:04o}): {target}"
            )
        if info.st_nlink != 1:
            findings.append(f"{label} has {info.st_nlink} hard links (expected 1): {target}")

    try:
        directory_finding = _directory_chain_finding(path.parent, private_leaf=True)
    except StorageBoundaryError as exc:
        findings.append(exc.finding)
    else:
        if directory_finding:
            findings.append(directory_finding)
    inspect_file("database", path, PRIVATE_FILE_MODE, required=True)
    for suffix, label in (
        ("-wal", "WAL"),
        ("-shm", "shared memory"),
        ("-journal", "rollback journal"),
    ):
        inspect_file(
            label,
            Path(f"{path}{suffix}"),
            PRIVATE_FILE_MODE,
            required=False,
        )
    return findings


def _connect_existing_path(path: Path) -> sqlite3.Connection:
    """Open and migrate one normalized existing path; never create its database."""
    blocker = storage_open_blocker(path)
    if blocker:
        raise StorageBoundaryError(
            f"refusing unsafe ledger path: {blocker}", finding=blocker
        )
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
    except sqlite3.Error as exc:
        raise StorageBoundaryError(f"cannot open ledger {path}: {exc}") from exc

    def close_after_failed_setup() -> None:
        try:
            conn.close()
        except sqlite3.Error:
            # Preserve the refusal that explains why setup failed. Cleanup must
            # not replace the primary operator-facing diagnosis.
            pass

    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
    except JanusError:
        close_after_failed_setup()
        raise
    except sqlite3.Error as exc:
        close_after_failed_setup()
        raise JanusError(f"cannot initialize ledger {path}: {exc}") from exc
    return conn


def connect_existing(db_path: Path | None = None) -> sqlite3.Connection:
    """Open an existing ledger only, preserving absence as a refusal."""
    return _connect_existing_path(storage_path(db_path))


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) and migrate forward. Never migrates backward."""
    path = storage_path(db_path)
    initially_missing = _lstat(path) is None
    if initially_missing:
        blocker = _new_storage_parent_finding(path.parent)
        if blocker:
            raise StorageBoundaryError(
                f"refusing unsafe new ledger path: {blocker}", finding=blocker
            )
        _create_private_parents(path.parent)
        _create_private_database(path)
    return _connect_existing_path(path)


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply pending migrations in order, recording each one's checksum.

    A migration whose recorded checksum no longer matches its file is a hard
    refusal: the migration history is a high-integrity surface, and silently
    accepting an edited migration is how two databases claiming the same version
    end up shaped differently.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)"
    )
    applied = {r["version"]: r["checksum"] for r in conn.execute(
        "SELECT version, checksum FROM schema_migrations")}
    run: list[str] = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version, sql = f.stem, f.read_text()
        digest = _checksum(sql)
        if version in applied:
            if applied[version] != digest:
                raise JanusError(
                    f"migration {version} has changed since it was applied "
                    f"(recorded {applied[version][:12]}, file {digest[:12]}). "
                    "Migrations are forward-only; add a new one."
                )
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at, checksum) VALUES (?,?,?)",
            (version, now(), digest),
        )
        conn.commit()
        run.append(version)
    return run


def audit(conn: sqlite3.Connection, actor: str, verb: str,
          gate_id: str | None = None, detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_events (at, actor, verb, gate_id, detail) VALUES (?,?,?,?,?)",
        (now(), actor, verb, gate_id, detail),
    )


# --------------------------------------------------------------- gates ----
def new_id() -> str:
    return "g" + uuid.uuid4().hex[:11]


def raise_gate(
    conn: sqlite3.Connection,
    *,
    question: str,
    kind: str,
    decay: str,
    consumer: str,
    actor: str,
    decay_check: str | None = None,
    horizon: str | None = None,
    delivery_check: str | None = None,
    binding: Binding | None = None,
    options: list[dict] | None = None,
    cites: str | None = None,
) -> str:
    if kind not in KINDS:
        raise JanusError(
            f"kind must be one of {', '.join(KINDS)} — it answers WHY a human is "
            "needed, not how dangerous the change is. A gate that fits none of "
            "them is evidence the enum is wrong: raise that as a finding."
        )
    question = " ".join(question.split())
    if len(question) > 280:
        raise JanusError(
            "question is longer than 280 characters — a gate a tired human "
            "cannot answer in one sentence has not been thought through yet"
        )
    gid = new_id()
    conn.execute(
        "INSERT INTO gates (id, raised_at, raised_by, question, kind, decay,"
        " decay_check, consumer, horizon, delivery_check, binding_kind,"
        " binding_locator, binding_sha256, cites)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (gid, now(), actor, question, kind, decay, decay_check, consumer, horizon,
         delivery_check,
         binding.kind if binding else None,
         binding.locator if binding else None,
         binding.sha256 if binding else None,
         cites),
    )
    for pos, opt in enumerate(options or []):
        conn.execute(
            "INSERT INTO gate_options (gate_id, option_id, position, label, detail,"
            " recommended) VALUES (?,?,?,?,?,?)",
            (gid, opt["id"], pos, opt["label"], opt.get("detail"),
             1 if opt.get("recommended") else 0),
        )
    audit(conn, actor, "raise", gid, question[:120])
    conn.commit()
    return gid


def close_gate(
    conn: sqlite3.Connection, gate_id: str, *, state: str, reason: str, actor: str,
    option_id: str | None = None, rebind: bool = True,
) -> dict:
    """Write the one terminal event. The UNIQUE PK on rulings enforces 'never both'."""
    if state not in TERMINAL_STATES:
        raise JanusError(f"state must be one of {', '.join(TERMINAL_STATES)}")
    gate = get_gate(conn, gate_id)
    if gate is None:
        raise JanusError(f"no such gate: {gate_id}")
    if gate["ruling"] is not None:
        r = gate["ruling"]
        raise JanusError(
            f"gate {gate_id} is already {r['state']} (ruled {r['ruled_at']} by "
            f"{r['ruled_by']}). A gate is open or closed and never both — a "
            "reversal is a NEW gate that cites this one."
        )
    # The digest is re-derived AT RULING TIME so the record says what was true
    # when the human ruled, not what was true when it was raised. If the artifact
    # drifted in between, the ruling honestly records the bytes actually ruled
    # on; the CLI warns before writing, and `show` reports later drift. Janus
    # records the binding and never enforces it.
    bound = digest_of_live(gate) if (
        rebind and state in RULED_STATES and gate["binding_sha256"]
    ) else None
    if state in RULED_STATES and gate["binding_sha256"] and bound is None:
        if not rebind:
            raise JanusError(
                f"cannot record {state}: gate {gate_id} is bound, but ruling-time "
                "rebinding was disabled, so Janus has no digest to record. The "
                "gate remains open. Re-enable rebinding, or supersede and "
                "re-raise the gate."
            )
        raise JanusError(
            f"cannot record {state}: gate {gate_id} is bound, but Janus cannot "
            "read the artifact to record the bytes ruled on. The gate remains "
            "open. Restore the artifact, or supersede and re-raise the gate with "
            "a readable binding."
        )
    try:
        conn.execute(
            "INSERT INTO rulings (gate_id, state, ruled_at, ruled_by, reason,"
            " option_id, bound_sha256) VALUES (?,?,?,?,?,?,?)",
            (gate_id, state, now(), actor, reason, option_id, bound),
        )
    except sqlite3.IntegrityError as e:
        raise JanusError(f"janus refused this ruling: {e}") from e
    audit(conn, actor, state, gate_id, reason[:120])
    conn.commit()
    return get_gate(conn, gate_id)


def digest_of_live(gate: dict) -> str | None:
    try:
        if gate["binding_kind"] == "file":
            return digest_file(gate["binding_locator"])
        if gate["binding_kind"] == "git":
            repo, rev = gate["binding_locator"].rsplit("@", 1)
            return digest_git_object(repo, rev)
        return gate["binding_sha256"]
    except JanusError:
        return None


def get_gate(conn: sqlite3.Connection, gate_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM gates WHERE id = ?", (gate_id,)).fetchone()
    if row is None:
        return None
    gate = dict(row)
    r = conn.execute("SELECT * FROM rulings WHERE gate_id = ?", (gate_id,)).fetchone()
    gate["ruling"] = dict(r) if r else None
    gate["state"] = r["state"] if r else "open"
    gate["options"] = [dict(o) for o in conn.execute(
        "SELECT * FROM gate_options WHERE gate_id = ? ORDER BY position", (gate_id,))]
    gate["observations"] = [dict(o) for o in conn.execute(
        "SELECT * FROM observations WHERE gate_id = ? ORDER BY at DESC LIMIT 5",
        (gate_id,))]
    gate["check_revisions"] = [dict(r) for r in conn.execute(
        "SELECT * FROM check_revisions WHERE gate_id = ? ORDER BY id", (gate_id,))]
    # Callers must never have to remember to ask for the revision. Reading
    # `decay_check` directly is how a corrected check gets quietly ignored, so
    # the effective value is computed here, once, for everyone.
    gate["effective_decay_check"] = effective_check(
        conn, gate_id, "decay", gate["decay_check"])
    gate["effective_delivery_check"] = effective_check(
        conn, gate_id, "delivery", gate["delivery_check"])
    return gate


def latest_observation(conn: sqlite3.Connection, gate_id: str, kind: str) -> dict | None:
    """The most recent observation OF THIS KIND, from a query that cannot truncate.

    `get_gate` attaches the last five observations of any kind, which is right
    for display and wrong for deriving a verdict: five decay checks push an
    older delivery result past the limit, and a caller reading that list would
    report a promise that HAS landed as never checked. Same family as every
    other bug this project has paid for — a check answering "unknown" in a way
    that reads as "fine".

    Ties on `at` are broken by insertion order; the column has second
    granularity and two checks can share a second.
    """
    row = conn.execute(
        "SELECT * FROM observations WHERE gate_id = ? AND kind = ?"
        " ORDER BY at DESC, id DESC LIMIT 1", (gate_id, kind)).fetchone()
    return dict(row) if row else None


def latest_delivery_observation(conn: sqlite3.Connection, gate_id: str) -> dict | None:
    """Return the latest delivery result eligible to explain an approved action.

    Delivery is post-ruling evidence.  Older Janus versions allowed a delivery
    command to run while a gate was still open; letting that result survive a
    later approval would make the future appear to have been observed early.

    Timestamps have second resolution and wall clocks can move, so ordering is
    taken from the append-only audit trail written in the same transactions. If
    that ordering evidence is absent, the conservative result is unknown.
    """
    ruling = conn.execute(
        "SELECT state, ruled_at FROM rulings WHERE gate_id = ?", (gate_id,)
    ).fetchone()
    if ruling is None or ruling["state"] != "approved":
        return None
    observation = latest_observation(conn, gate_id, "delivery")
    if observation is None:
        return None

    approved_audit = conn.execute(
        "SELECT id FROM audit_events WHERE gate_id = ? AND verb = 'approved'"
        " ORDER BY id DESC LIMIT 1", (gate_id,)
    ).fetchone()
    observed_audit = conn.execute(
        "SELECT id FROM audit_events WHERE gate_id = ? AND verb = 'observe:delivery'"
        " ORDER BY id DESC LIMIT 1", (gate_id,)
    ).fetchone()
    if (approved_audit is None or observed_audit is None
            or observed_audit["id"] <= approved_audit["id"]):
        return None
    return observation


def list_gates(conn: sqlite3.Connection, *, state: str = "open") -> list[dict]:
    if state == "all":
        rows = conn.execute("SELECT id FROM gates ORDER BY raised_at").fetchall()
    elif state == "open":
        rows = conn.execute(
            "SELECT g.id FROM gates g LEFT JOIN rulings r ON r.gate_id = g.id"
            " WHERE r.gate_id IS NULL ORDER BY g.raised_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT g.id FROM gates g JOIN rulings r ON r.gate_id = g.id"
            " WHERE r.state = ? ORDER BY g.raised_at", (state,)).fetchall()
    return [get_gate(conn, r["id"]) for r in rows]


# The shell's own convention for a command killed by `timeout(1)`. Borrowed
# rather than invented so the number means the same thing to anyone reading the
# ledger with no Janus knowledge.
def revise_check(conn: sqlite3.Connection, gate_id: str, kind: str, command: str,
                 actor: str, reason: str) -> dict:
    """Correct a check without rewriting anything.

    A check is executable text written once, at raise time, by someone guessing
    at a future they have not seen. When it turns out to measure something
    adjacent to the question, the append-only ledger cannot edit it — so a
    correction is a NEW ROW. The original stays on the gate and stays visible;
    this records who replaced it and why.

    Deliberately permitted on a CLOSED gate: the case that forced this was an
    approved resource gate whose delivery check could never pass, so the board
    reported a delivered promise as outstanding forever. A revision changes no
    state and touches no ruling.
    """
    if kind not in ("decay", "delivery"):
        raise JanusError("kind must be 'decay' or 'delivery'")
    if gate_id and get_gate(conn, gate_id) is None:
        raise JanusError(f"no such gate: {gate_id}")
    if not command.strip():
        raise JanusError("a revision needs a command")
    if not reason.strip():
        raise JanusError(
            "a revision needs a reason, and it should say what the old check "
            "actually measured instead of the question. That sentence is what "
            "makes the gap findable by the next person to write one."
        )
    conn.execute(
        "INSERT INTO check_revisions (gate_id, kind, command, at, revised_by, reason)"
        " VALUES (?,?,?,?,?,?)",
        (gate_id, kind, command, now(), actor, reason))
    audit(conn, actor, f"revise:{kind}", gate_id, reason[:120])
    conn.commit()
    return get_gate(conn, gate_id)


def effective_check(conn: sqlite3.Connection, gate_id: str, kind: str,
                    original: str | None) -> str | None:
    """The newest revision's command, or the gate's original when none exists."""
    row = conn.execute(
        "SELECT command FROM check_revisions WHERE gate_id = ? AND kind = ?"
        " ORDER BY id DESC LIMIT 1", (gate_id, kind)).fetchone()
    return row["command"] if row else original


TIMEOUT_EXIT = 124


def observe(conn: sqlite3.Connection, gate_id: str, kind: str, actor: str,
            timeout: int = 120, *, expected_command: str | None = None) -> dict:
    """Run a decay or delivery check. An observation NEVER changes state."""
    if kind not in ("decay", "delivery"):
        raise JanusError("kind must be 'decay' or 'delivery'")
    gate = get_gate(conn, gate_id)
    if gate is None:
        raise JanusError(f"no such gate: {gate_id}")
    if kind == "delivery" and gate["state"] != "approved":
        raise JanusError(
            "a delivery check is post-action evidence and can run only after "
            f"an approved ruling; gate {gate_id} is {gate['state']}. Nothing ran."
        )
    cmd = gate["effective_decay_check"] if kind == "decay" else gate["effective_delivery_check"]
    if not cmd:
        raise JanusError(f"gate {gate_id} carries no {kind} check")
    # A CLI confirmation is consent to the command that was DISPLAYED. Check
    # revisions are append-only but can arrive from another process while the
    # operator is reading that preview. A revision visible here invalidates the
    # preview. One committed after this read cannot replace the local `cmd`; it
    # becomes effective on the next run without making this displayed command
    # hold SQLite's global writer lock for the duration of an arbitrary shell
    # timeout.
    if expected_command is not None and cmd != expected_command:
        raise JanusError(
            f"gate {gate_id}'s {kind} check changed after preview; nothing ran. "
            "Review the new command and try again."
        )
    # A check that hangs is a FACT ABOUT THE CHECK and gets recorded as one.
    # Letting TimeoutExpired escape did two bad things: it crashed the caller
    # (it is not an OSError, so the handlers around this did not catch it), and
    # it wrote nothing — leaving a hung check indistinguishable from one that
    # was never run. Unknown must never be reachable by accident.
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout)
        code, note = p.returncode, (p.stdout or p.stderr)[:200]
    except subprocess.TimeoutExpired:
        code = TIMEOUT_EXIT
        note = f"no result in {timeout}s — the check was killed, not answered"
    conn.execute(
        "INSERT INTO observations (gate_id, at, kind, command, exit_code, note)"
        " VALUES (?,?,?,?,?,?)",
        (gate_id, now(), kind, cmd, code, note),
    )
    audit(conn, actor, f"observe:{kind}", gate_id, f"exit={code}")
    conn.commit()
    return {"kind": kind, "command": cmd, "exit_code": code, "output": note}
