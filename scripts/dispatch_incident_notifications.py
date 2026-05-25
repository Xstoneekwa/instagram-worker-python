#!/usr/bin/env python3
"""One-shot ORF-4 incident notification dispatcher CLI."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


def _redact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    from incident_notifications import _truncate_redact

    out = dict(summary)
    if "error" in out and out["error"] is not None:
        out["error"] = _truncate_redact(out["error"])
    return out


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parent.parent
    load_dotenv(repo_root / ".env")
    sys.path.insert(0, str(repo_root))

    try:
        import config
        import incident_notifications
    except Exception as exc:
        err = str(exc)
        try:
            from incident_notifications import _sanitize_error

            err = _sanitize_error(exc)
        except Exception:
            err = err[:500]
        print(
            json.dumps(
                {
                    "event": "incident_notification_cli_init_failed",
                    "error": err,
                },
                ensure_ascii=False,
            )
        )
        return 1

    try:
        summary = incident_notifications.dispatch_account_incident_notifications()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "incident_notification_cli_dispatch_failed",
                    "error": incident_notifications._sanitize_error(exc),
                },
                ensure_ascii=False,
            )
        )
        return 1

    safe = _redact_summary(summary)
    print(json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str))

    if summary.get("reason") == "dispatch_failed" and summary.get("errors_count", 0) > 0:
        if not bool(getattr(config, "INCIDENT_NOTIFICATIONS_FAIL_OPEN", True)):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
