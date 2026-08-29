#!/usr/bin/env bash
set -uo pipefail

python -m ruff check . &&
python -m compileall -q src scripts &&
python -m pytest -q &&
python scripts/check_distribution.py
