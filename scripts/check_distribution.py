#!/usr/bin/env python3
"""Build Janus as an operator receives it, then verify that installed copy."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(*argv: str, cwd: Path, env: dict[str, str], display: str | None = None) -> None:
    print("+", display or shlex.join(argv), flush=True)
    subprocess.run(argv, cwd=cwd, env=env, check=True)


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    source_migrations = project / "src" / "janus" / "migrations"
    clean_environment = os.environ.copy()
    clean_environment.pop("PYTHONPATH", None)

    with tempfile.TemporaryDirectory(prefix="janus-distribution-check-") as temporary:
        root = Path(temporary)
        source = root / "source"
        dist = root / "dist"
        environment = root / "venv"

        shutil.copytree(
            project,
            source,
            ignore=shutil.ignore_patterns(
                ".git",
                ".pytest_cache",
                ".ruff_cache",
                ".venv",
                "*.db",
                "*.db-shm",
                "*.db-wal",
                "*.egg-info",
                "__pycache__",
                "build",
                "dist",
            ),
        )

        # PyPA build's default path builds the wheel from the sdist. That makes
        # an incomplete source distribution fail before it can be published.
        _run(
            sys.executable,
            "-m",
            "build",
            "--outdir",
            str(dist),
            str(source),
            cwd=root,
            env=clean_environment,
        )

        wheels = sorted(dist.glob("*.whl"))
        if len(wheels) != 1:
            names = ", ".join(path.name for path in wheels) or "none"
            raise RuntimeError(f"expected exactly one wheel, found: {names}")

        _run(sys.executable, "-m", "venv", str(environment), cwd=root, env=clean_environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        janus = scripts / ("janus.exe" if os.name == "nt" else "janus")

        _run(
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            str(wheels[0]),
            cwd=root,
            env=clean_environment,
        )
        _run(str(janus), "--help", cwd=root, env=clean_environment)

        verifier = """
from pathlib import Path
import sys

from janus import core

source = sorted(path.name for path in Path(sys.argv[1]).glob("*.sql"))
module = Path(core.__file__).resolve()
if not module.is_relative_to(Path(sys.prefix).resolve()):
    raise SystemExit(f"Janus imported from outside the clean environment: {module}")
installed = sorted(path.name for path in core.MIGRATIONS_DIR.glob("*.sql"))
if not source or installed != source:
    raise SystemExit(f"installed migrations {installed!r} do not match source {source!r}")
"""
        _run(
            str(python),
            "-c",
            verifier,
            str(source_migrations),
            cwd=root,
            env=clean_environment,
            display=(
                f"{shlex.quote(str(python))} -c <installed-distribution-verifier> "
                f"{shlex.quote(str(source_migrations))}"
            ),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
