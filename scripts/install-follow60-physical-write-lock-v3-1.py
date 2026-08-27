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

from follow60_write_lock_v3_1 import load_protected_paths, protected_directories  # noqa: E402
from follow60_external_deployment_lock_v2 import (  # noqa: E402
    initialize_store, storage_guard, exact_admission, seal_path, seal_payload,
    atomic_record, verify_seal,
)


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
    if manifest != root / 'docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json':
        raise SystemExit('canonical_installed_manifest_required')
    before = exact_admission(root)  # Signature/identity/clean source BEFORE OS mutation.
    protected = load_protected_paths(manifest)
    directories = protected_directories(protected)
    initialize_store(root)
    with storage_guard(root):
        state_path = seal_path(root)
        if state_path.exists():
            raise SystemExit('external_physical_seal_already_exists')
        _install(root, protected, directories)
        after = exact_admission(root)
        if after != before:
            raise SystemExit('candidate_changed_during_physical_seal')
        atomic_record(state_path, seal_payload(root, after, protected, directories))
        result = verify_seal(root)
    print(json.dumps({**result, 'revision': revision}, sort_keys=True))
    return 0


def _install(root, protected, directories):
    # Reject symlink/file-type surprises before changing any ownership.
    for relative in protected:
        if not stat.S_ISREG((root / relative).lstat().st_mode):
            raise SystemExit('protected_regular_file_required:' + relative)
    for relative in directories:
        if not stat.S_ISDIR((root / relative).lstat().st_mode):
            raise SystemExit('protected_directory_required:' + relative)
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
    os.chown(root, 0, 0)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)
    _run("chflags", "uchg", str(root))


if __name__ == "__main__":
    raise SystemExit(main())
