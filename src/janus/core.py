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


class JanusError(RuntimeError):
    """A refusal the operator should read, not a stack trace."""


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
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
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
        return True, "binding matches: the ruled bytes are the live bytes"
    return False, (
        "BINDING NO LONGER MATCHES — the artifact changed since it was bound. "
        "A ruling approves specific bytes; it does not follow them. Treat any "
        "ruling on this gate as void and raise a new gate."
    )


# ------------------------------------------------------------ database ----
def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) and migrate forward. Never migrates backward."""
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


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
    return gate


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


def observe(conn: sqlite3.Connection, gate_id: str, kind: str, actor: str) -> dict:
    """Run a decay or delivery check. An observation NEVER changes state."""
    gate = get_gate(conn, gate_id)
    if gate is None:
        raise JanusError(f"no such gate: {gate_id}")
    cmd = gate["decay_check"] if kind == "decay" else gate["delivery_check"]
    if not cmd:
        raise JanusError(f"gate {gate_id} carries no {kind} check")
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    conn.execute(
        "INSERT INTO observations (gate_id, at, kind, command, exit_code, note)"
        " VALUES (?,?,?,?,?,?)",
        (gate_id, now(), kind, cmd, p.returncode, (p.stdout or p.stderr)[:200]),
    )
    audit(conn, actor, f"observe:{kind}", gate_id, f"exit={p.returncode}")
    conn.commit()
    return {"kind": kind, "command": cmd, "exit_code": p.returncode,
            "output": (p.stdout or p.stderr)[:200]}
