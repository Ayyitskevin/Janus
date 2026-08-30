"""Janus canonical JSON primitives shared by ledger and export boundaries."""

from __future__ import annotations

import json
from typing import Any

from .core import JanusError

CANONICALIZER = "janus.canonical-json.v1"


def validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise JanusError(f"{CANONICALIZER} refuses invalid Unicode at {path}") from exc
        return
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise JanusError(f"{CANONICALIZER} requires a signed 64-bit integer at {path}")
        return
    if isinstance(value, float):
        raise JanusError(f"{CANONICALIZER} refuses floating-point value at {path}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise JanusError(f"{CANONICALIZER} requires string key at {path}")
            validate_json_value(key, f"{path}.<key>")
            validate_json_value(item, f"{path}.{key}")
        return
    raise JanusError(f"{CANONICALIZER} cannot encode {type(value).__name__} at {path}")


def canonical_json(value: Any) -> bytes:
    """Return the exact bytes defined by ``janus.canonical-json.v1``."""
    validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
