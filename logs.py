"""Structured JSON-style logs to stdout."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def log(level: str, event: str, **fields: Any) -> None:
    """Emit one JSON object per line (level, event, optional fields)."""
    include_null = bool(fields.pop("_include_null_fields", False))
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "event": event,
    }
    for k, v in fields.items():
        if include_null or v is not None:
            payload[k] = v
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()
