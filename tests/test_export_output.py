"""File publication through the same CLI and read-only export used by operators."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from janus import cli, core
from janus import export as stable_export


@pytest.fixture
def ledger(tmp_path):
    db = tmp_path / "ledger" / "janus.db"
    with closing(core.connect(db)) as conn:
        gate = core.raise_gate(
            conn, request_id="saved-key", question="Publish this delivery?", kind="authority",
            decay="the delivery changes", consumer="reader", actor="tester",
        )
    return db, gate


@pytest.fixture
def export(capfdbinary):
    def run(*args, db=None, status=0):
        prefix = ["--db", str(db)] if db is not None else []
        try:
            actual = cli.main([*prefix, "export", *map(str, args)])
        except SystemExit as exc:
            actual = exc.code
        out, err = capfdbinary.readouterr()
        assert actual == status, err
        if status:
            assert not out and err and b"unrecognized arguments" not in err
            assert b"\x1b" not in err and b"\r" not in err
            return err
        assert not err
        return out

    return run


def test_output_selectors_and_atomic_private_replacement(ledger, export, tmp_path):
    db, gate = ledger
    delivery = tmp_path / "delivery.json"
    for selector in ([], [gate], ["--request-id", "saved-key"]):
        expected = export(*selector, db=db)
        assert expected == stable_export.export_gates(db, gate if selector else None) + b"\n"
        assert export(*selector, "--output", "-", db=db) == expected
        if delivery.exists():
            delivery.chmod(0o644)
        old_mode = os.umask(0)
        try:
            assert export(*selector, "--output", delivery, db=db) == b""
        finally:
            os.umask(old_mode)
        assert delivery.read_bytes() == expected
        assert stat.S_IMODE(delivery.stat().st_mode) == 0o600

    with delivery.open("rb") as old_reader:
        complete = export(db=db)
        assert export("--output", delivery, db=db) == b""
        assert old_reader.read() == expected and complete != expected
        assert delivery.read_bytes() == complete


def test_literal_hyphen_and_repeated_output(ledger, export, tmp_path, monkeypatch):
    db, _ = ledger
    monkeypatch.chdir(tmp_path)
    expected = export(db=db)
    assert export("--output", "./-", db=db) == b""
    assert Path("-").read_bytes() == expected
    export("--output", "first", "--output", "second", db=db, status=2)
    assert not Path("first").exists() and not Path("second").exists()


def test_producer_receipt_selectors_share_v1_bytes_and_pins(ledger, export, tmp_path, monkeypatch):
    db, gate = ledger
    monkeypatch.chdir(tmp_path)
    expected = export(gate, db=db)
    digest = json.loads(expected)["integrity"]["document_sha256"]
    for selector in ([gate], ["--request-id", "saved-key"]):
        out = export(*selector, "--output", "./-", "--receipt-json", "--receipt-json", db=db)
        assert out.endswith(b"\n") and len(out.splitlines()) == 1
        assert json.loads(out) == {"gate_id": gate, "document_sha256": digest}
        artifact = Path("-")
        assert artifact.read_bytes() == expected and stat.S_IMODE(artifact.stat().st_mode) == 0o600
        verified = stable_export.verify_export_receipt(
            expected, expect_gate=gate, expect_document_sha256=digest,
        )
        assert verified["decision"]["state"] == "open"


def test_receipt_argument_or_lookup_failure_preserves_prior_file(ledger, export, tmp_path):
    db, gate = ledger
    delivery = tmp_path / "decision"
    previous = export(gate, db=db)
    delivery.write_bytes(previous)
    other = tmp_path / "other"
    missing = tmp_path / "unavailable" / "ledger.db"
    for source, args in (
        (db, [gate]),
        (db, [gate, "--output", "-"]),
        (db, ["--output", delivery]),
        (db, ["", "--output", delivery]),
        (db, ["--request-id", "", "--output", delivery]),
        (db, [gate, "--request-id", "saved-key", "--output", delivery]),
        (db, ["--request-id", "saved-key", "--request-id", "saved-key", "--output", delivery]),
        (db, [gate, "--output", delivery, "--output", other]),
        (db, ["no-such-gate", "--output", delivery]),
        (db, ["--request-id", "unknown", "--output", delivery]),
        (missing, [gate, "--output", delivery]),
    ):
        export(*args, "--receipt-json", db=source, status=2)
        assert delivery.read_bytes() == previous and not other.exists()
    assert not missing.parent.exists()


def test_receipts_follow_their_own_publication_when_another_writer_replaces_it(
    ledger, export, tmp_path, monkeypatch, capfdbinary,
):
    db, gate = ledger
    delivery = tmp_path / "decision"
    first_bytes = export(gate, db=db)
    real_replace = os.replace
    real_snapshot = stable_export.read_snapshot
    snapshots = []
    second_receipts = []

    def count_snapshot(*args, **kwargs):
        snapshots.append(args)
        return real_snapshot(*args, **kwargs)

    def publish_competitor(source, target, **kwargs):
        assert capfdbinary.readouterr() == (b"", b"")
        real_replace(source, target, **kwargs)
        assert delivery.read_bytes() == first_bytes
        with closing(core.connect(db)) as conn:
            core.close_gate(conn, gate, state="refused", reason="Hold delivery.", actor="owner")
        with monkeypatch.context() as patch:
            patch.setattr(os, "replace", real_replace)
            second_receipts.append(export(gate, "--output", delivery, "--receipt-json", db=db))

    monkeypatch.setattr(stable_export, "read_snapshot", count_snapshot)
    monkeypatch.setattr(os, "replace", publish_competitor)
    first = json.loads(export("--request-id", "saved-key", "--output", delivery,
                              "--receipt-json", db=db))
    assert len(second_receipts) == 1
    second = json.loads(second_receipts[0])
    assert len(snapshots) == 2
    assert first["gate_id"] == second["gate_id"] == gate
    assert first["document_sha256"] != second["document_sha256"]
    versions = [(first_bytes, first, "open"), (delivery.read_bytes(), second, "refused")]
    for data, pins, state in versions:
        verified = stable_export.verify_export_receipt(
            data, expect_gate=gate, expect_document_sha256=pins["document_sha256"],
        )
        assert verified["decision"]["state"] == state
    with pytest.raises(core.JanusError, match="document digest does not match"):
        stable_export.verify_export_receipt(first_bytes, expect_gate=gate,
                                           expect_document_sha256=second["document_sha256"])


@pytest.mark.parametrize("blocked", [False, True], ids=["closed-reader", "unbuffered-would-block"])
def test_receipt_stream_failure_reports_committed_file(ledger, export, tmp_path, blocked):
    db, gate = ledger
    expected = export(gate, db=db)
    delivery = tmp_path / "decision"
    delivery.write_bytes(b"old delivery")
    reader, writer = os.pipe()
    with os.fdopen(reader, "rb") as receiver, os.fdopen(writer, "wb") as pipe:
        python = [sys.executable, "-E"]
        if blocked:
            python.append("-u")
            os.set_blocking(writer, False)
            for chunk in (b"x" * 4096, b"x"):
                with pytest.raises(BlockingIOError):
                    while True:
                        os.write(writer, chunk)
        else:
            receiver.close()
        result = subprocess.run(
            [*python, "-m", "janus.cli", "--db", str(db), "export", gate,
             "--output", str(delivery), "--receipt-json"],
            cwd=Path(__file__).resolve().parents[1] / "src",
            stdout=pipe, stderr=subprocess.PIPE, timeout=10,
        )
    assert result.returncode != 0
    assert delivery.read_bytes() == expected
    assert len(result.stderr.splitlines()) == 1
    assert b"published" in result.stderr.lower() and b"receipt" in result.stderr.lower()


@pytest.mark.parametrize("prior", [False, True])
def test_failed_recovery_leaves_delivery_and_missing_paths_alone(ledger, export, tmp_path, prior):
    db, _ = ledger
    out = tmp_path / "out"
    out.mkdir(mode=0o700)
    delivery = out / "decision"
    if prior:
        delivery.write_bytes(b"previous delivery")
    missing = tmp_path / "missing ledger" / "janus.db"
    assert b"no Janus ledger" in export("--output", delivery, db=missing, status=2)
    assert not missing.parent.exists()
    assert b"no gate for request" in export("--request-id", "unknown", "--output", delivery,
                                            db=db, status=2)
    export("--output", out / "absent\x1b[2J" / "decision", db=db, status=2)
    assert list(out.iterdir()) == ([delivery] if prior else [])
    if prior:
        assert delivery.read_bytes() == b"previous delivery"


def test_effective_ledger_and_sidecar_names_refused_before_sqlite(
    ledger, export, tmp_path, monkeypatch,
):
    db, _ = ledger
    alias = tmp_path / "alias"
    alias.symlink_to(db.parent, target_is_directory=True)
    monkeypatch.setattr(core, "DEFAULT_DB", db)
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_kw: pytest.fail("snapshot opened"))
    for suffix in ("", "-wal", "-shm", "-journal"):
        protected = db.with_name(db.name + suffix)
        if suffix:
            assert not protected.exists()
            export("--output", alias / protected.name, status=2)
            assert not protected.exists()
            protected.write_bytes(b"existing sidecar")
            protected.chmod(0o600)
        original = protected.read_bytes()
        for directory in (db.parent, alias):
            export("--output", directory / protected.name, status=2)
        assert protected.read_bytes() == original


@pytest.mark.parametrize("suffix", ["", "-wal"])
def test_existing_hard_link_to_source_storage_refused(ledger, export, tmp_path, suffix):
    db, _ = ledger
    with closing(core.connect(db)):
        source = db.with_name(db.name + suffix)
        alias = tmp_path / "delivery"
        os.link(source, alias)
        original = source.read_bytes()
        assert b"hard link" in export("--output", alias, db=db, status=2)
        assert alias.samefile(source) and alias.read_bytes() == original


def test_refuses_final_links_directories_and_fifo(ledger, export, tmp_path):
    db, _ = ledger
    target = tmp_path / "target"
    target.write_bytes(b"untouched")
    symlink = tmp_path / "link"
    symlink.symlink_to(target)
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "absent")
    directory = tmp_path / "directory"
    directory.mkdir()
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    # A regressing writer must fail the test without blocking on a pipe reader.
    with closing(os.fdopen(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK), "rb")):
        for path in (symlink, dangling, directory, fifo):
            before = path.lstat()
            export("--output", path, db=db, status=2)
            assert os.path.samestat(path.lstat(), before)
    assert target.read_bytes() == b"untouched" and not dangling.exists()


@pytest.mark.parametrize("suffix", ["", "-WAL", "-SHM", "-JOURNAL"])
def test_reserved_names_include_case_and_unicode_variants(tmp_path, export, monkeypatch, suffix):
    ledger = tmp_path / "Café.db"
    with closing(core.connect(ledger)):
        pass
    equivalent = "CAFE\u0301.DB" + suffix
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_kw: pytest.fail("snapshot opened"))
    error = export("--output", tmp_path / equivalent, db=ledger, status=2)
    assert b"source ledger or sidecar" in error


def test_directory_close_after_commit_preserves_success(ledger, tmp_path, export, monkeypatch):
    db, _ = ledger
    expected = export(db=db)
    destination = tmp_path / "decision"
    real_close = os.close
    directory_info = tmp_path.stat()
    closed = []

    def close(fd):
        is_directory = os.path.samestat(os.fstat(fd), directory_info)
        real_close(fd)
        if is_directory:
            closed.append(fd)
            raise OSError("injected directory cleanup error")

    monkeypatch.setattr(os, "close", close)
    assert export("--output", destination, db=db) == b""
    assert closed and destination.read_bytes() == expected


def test_missing_unicode_neighbor_is_unambiguous_or_in_separate_directory(ledger, export, tmp_path):
    db, _ = ledger
    expected = export(db=db)
    # Some filesystems ignore a soft hyphen when looking up a missing sidecar.
    ambiguous = "ja\u00adnus.db-wal"
    assert b".json suffix" in export("--output", db.parent / ambiguous, db=db, status=2)
    assert not (db.parent / ambiguous).exists()
    for destination in (db.parent / "résultat.json", tmp_path / ambiguous):
        assert export("--output", destination, db=db) == b""
        assert destination.read_bytes() == expected


@pytest.mark.parametrize("destination", [".JANUS-EXPORT-deadbeef", ".janus\u00adexportdeadbeef"])
def test_staging_name_remains_distinct_until_commit(ledger, tmp_path, export, monkeypatch,
                                                    destination):
    db, _ = ledger
    monkeypatch.setattr(cli.secrets, "token_hex", lambda _: "deadbeef")
    real_replace = os.replace
    commits = []

    def observe(source, target, **kwargs):
        assert ("-" in source) != ("-" in target)
        assert not (tmp_path / target).exists()
        commits.append(target)
        return real_replace(source, target, **kwargs)

    monkeypatch.setattr(os, "replace", observe)
    assert export("--output", tmp_path / destination, db=db) == b""
    assert commits == [destination]


@pytest.mark.parametrize("mode,status", [(0o775, 2), (0o777, 2), (0o1777, 0), (0o755, 0)])
def test_output_directory_chain_protects_child_entries(ledger, export, tmp_path, mode, status):
    db, _ = ledger
    ancestor = tmp_path / "ancestor"
    out = ancestor / "deliveries"
    out.mkdir(mode=0o700, parents=True)
    ancestor.chmod(mode)
    destination = out / "decision"
    export("--output", destination, db=db, status=status)
    if status:
        assert not list(out.iterdir())
    else:
        assert destination.read_bytes() == export(db=db)


def test_publication_keeps_its_selected_directory(ledger, export, tmp_path, monkeypatch):
    db, _ = ledger
    out = tmp_path / "out"
    out.mkdir(mode=0o700)
    moved = tmp_path / "moved"
    expected = export(db=db)
    real_export = stable_export.export_gates

    def move_directory(*args, **kwargs):
        result = real_export(*args, **kwargs)
        out.rename(moved)
        out.mkdir(mode=0o700)
        return result

    monkeypatch.setattr(stable_export, "export_gates", move_directory)
    assert export("--output", out / "decision", db=db) == b""
    assert (moved / "decision").read_bytes() == expected and not list(out.iterdir())


@pytest.mark.parametrize("stage", ["write", "flush", "close", "replace"])
@pytest.mark.parametrize("prior", [False, True])
@pytest.mark.parametrize("receipt", [False, True], ids=["plain", "receipt"])
def test_publisher_io_errors_preserve_delivery_and_clean_only_its_temp(
    ledger, export, tmp_path, monkeypatch, stage, prior, receipt,
):
    db, gate = ledger
    destination = tmp_path / "out" / "decision"
    destination.parent.mkdir(mode=0o700)
    neighbor = destination.with_name("other-publisher.tmp")
    neighbor.write_bytes(b"unrelated")
    if prior:
        destination.write_bytes(b"old")
    before = {path: path.read_bytes() for path in destination.parent.iterdir()}
    real_fdopen = os.fdopen

    def fail(*args, **kwargs):
        raise OSError(f"injected {stage} error")

    class FaultyWriter:
        def __init__(self, *args, **kwargs):
            self.file = real_fdopen(*args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self.close()

        def __getattr__(self, name):
            return fail if name == stage else getattr(self.file, name)

        def write(self, data):
            if stage == "write":
                self.file.write(data[:7])
                fail()
            return self.file.write(data)

        def close(self):
            self.file.close()
            if stage == "close":
                fail()

    if stage == "replace":
        monkeypatch.setattr(os, "replace", fail)
    else:
        monkeypatch.setattr(os, "fdopen", FaultyWriter)
    args = [gate, "--receipt-json"] if receipt else []
    error = export(*args, "--output", destination, db=db, status=2)
    assert f"injected {stage} error".encode() in error
    assert {path: path.read_bytes() for path in destination.parent.iterdir()} == before
