#!/usr/bin/env python3
"""Install the root-owned immutable Follow60 physical write lock.

This command is intentionally root-only.  It never unlocks files and it never
uses Liam's private key.  Future changes must be applied by a separately
reviewed signed-change transaction; raw chmod/chflags is not an approved path.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from follow60_write_lock_v3_1 import LOCK_VERSION, STATE_NAME, load_protected_paths  # noqa: E402


def _run(*args: str) -> None:
    subprocess.check_call(list(args))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--manifest", default="docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json")
    parser.add_argument("--verified-revision", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("root_required_for_follow60_physical_write_lock")
    root = Path(args.target_root).resolve()
    manifest = (root / args.manifest).resolve()
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != args.verified_revision:
        raise SystemExit("verified_revision_mismatch")
    protected = load_protected_paths(manifest)
    directories = sorted({str(Path(path).parent) for path in protected if str(Path(path).parent) != "."})
    state_path = root / STATE_NAME
    state_path.write_text(json.dumps({
        "schema": "FOLLOW60_PHYSICAL_WRITE_LOCK_V3_1",
        "lock_version": LOCK_VERSION,
        "lock_state": "LOCKED",
        "verified_revision": revision,
        "protected_paths": list(protected),
        "protected_directories": directories,
        "unlock_contract": "LIAM_SIGNED_EXACT_DIFF_TRANSACTION_ONLY",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Files are root-owned + read-only + user-immutable.  Root ownership makes
    # `chflags nouchg` unavailable to the normal Codex/CI user.
    for relative in protected:
        candidate = root / relative
        if not candidate.is_file():
            raise SystemExit(f"protected_file_missing:{relative}")
        os.chown(candidate, 0, 0)
        os.chmod(candidate, stat.S_IMODE(candidate.stat().st_mode) & ~0o222)
        _run("chflags", "uchg", str(candidate))
    for relative in directories:
        directory = root / relative
        os.chown(directory, 0, 0)
        os.chmod(directory, stat.S_IMODE(directory.stat().st_mode) & ~0o222)
        _run("chflags", "uchg", str(directory))
    os.chown(state_path, 0, 0)
    os.chmod(state_path, 0o444)
    _run("chflags", "uchg", str(state_path))
    os.chown(root, 0, 0)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)
    _run("chflags", "uchg", str(root))
    print(json.dumps({"ok": True, "status": "FOLLOW60_PHYSICAL_WRITE_LOCK_V3_1_INSTALLED", "revision": revision, "protected_file_count": len(protected)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
