#!/usr/bin/env bash
set -uo pipefail

python -m compileall -q src &&
python -m pytest -q
