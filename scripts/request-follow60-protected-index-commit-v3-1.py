#!/usr/bin/env python3
"""Create the deterministic authorization Liam signs for the staged diff."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from follow60_lock_v3 import sha256_bytes  # noqa: E402

OUTPUT = ROOT / "docs/governance/FOLLOW60_LOCK_COMMIT_AUTHORIZATION_V3_1.json"
EXCLUDED = {
    "docs/governance/FOLLOW60_LOCK_COMMIT_AUTHORIZATION_V3_1.json",
    "docs/governance/FOLLOW60_LOCK_COMMIT_AUTHORIZATION_V3_1.sig",
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def main() -> int:
    paths = sorted(set(git("diff", "--cached", "--name-only", "--diff-filter=ACMRD").decode().splitlines()) - EXCLUDED)
    if not paths:
        raise SystemExit("staged_change_missing")
    diff = git("diff", "--cached", "--binary", "HEAD", "--", *paths)
    now = datetime.now(timezone.utc)
    payload = {
        "schema": "FOLLOW60_SIGNED_COMMIT_AUTHORIZATION_V3_1",
        "change_id": "follow60-physical-write-lock-v3-1-20260816",
        "base_sha": git("rev-parse", "HEAD").decode().strip(),
        "files_requested": paths,
        "diff_sha256": sha256_bytes(diff),
        "requested_scope": "physical root-owned immutable write lock, signed staged-commit gate, package and activation enforcement",
        "requested_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=4)).isoformat(),
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
