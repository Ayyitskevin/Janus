"""Focused tests for process-wide command-line behavior."""

from __future__ import annotations

import pytest

from janus import __version__, cli


def test_version_reports_the_importable_package_version(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--version"])

    assert stopped.value.code == 0
    assert capsys.readouterr().out == f"janus {__version__}\n"
