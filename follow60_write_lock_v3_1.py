"""Physical write-lock verification for Follow60 Lock V3.1.

The cryptographic manifest protects content.  This module verifies the second,
OS-level layer installed on an immutable release: protected files must be
root-owned, non-writable and user-immutable, and every containing directory
listed by the installer must be root-owned and non-writable.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any


STATE_NAME = ".follow60-write-lock-v3.1.json"
LOCK_VERSION = "3.1.0"


def _immutable_flag() -> int:
    return int(getattr(stat, "UF_IMMUTABLE", 0))


def load_protected_paths(manifest_path: Path) -> tuple[str, ...]:
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    entries = payload.get("protected_entries") or {}
    if payload.get("lock_version") != LOCK_VERSION or not isinstance(entries, dict) or not entries:
        raise ValueError("follow60_write_lock_manifest_invalid")
    return tuple(sorted(str(path) for path in entries))


def verify_physical_write_lock(
    root: Path,
    manifest_path: Path,
    *,
    require_root_owner: bool = True,
    require_immutable: bool = True,
) -> dict[str, Any]:
    root = Path(root).resolve()
    state_path = root / STATE_NAME
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        protected = load_protected_paths(manifest_path)
    except Exception as exc:
        return {"ok": False, "reason": "physical_write_lock_state_unreadable", "error_type": type(exc).__name__}
    if state.get("schema") != "FOLLOW60_PHYSICAL_WRITE_LOCK_V3_1" or state.get("lock_state") != "LOCKED":
        return {"ok": False, "reason": "physical_write_lock_state_invalid"}
    if tuple(state.get("protected_paths") or ()) != protected:
        return {"ok": False, "reason": "physical_write_lock_scope_mismatch"}

    failures: dict[str, str] = {}
    immutable = _immutable_flag()
    for relative in protected:
        candidate = root / relative
        try:
            info = candidate.stat()
        except OSError:
            failures[relative] = "missing"
            continue
        if not stat.S_ISREG(info.st_mode):
            failures[relative] = "not_regular_file"
        elif info.st_mode & 0o222:
            failures[relative] = "write_bit_present"
        elif require_root_owner and info.st_uid != 0:
            failures[relative] = "not_root_owned"
        elif require_immutable and (not immutable or not (getattr(info, "st_flags", 0) & immutable)):
            failures[relative] = "immutable_flag_missing"

    for relative in tuple(state.get("protected_directories") or ()):
        directory = root / relative
        try:
            info = directory.stat()
        except OSError:
            failures[f"dir:{relative}"] = "missing"
            continue
        if info.st_mode & 0o222:
            failures[f"dir:{relative}"] = "write_bit_present"
        elif require_root_owner and info.st_uid != 0:
            failures[f"dir:{relative}"] = "not_root_owned"
        elif require_immutable and (not immutable or not (getattr(info, "st_flags", 0) & immutable)):
            failures[f"dir:{relative}"] = "immutable_flag_missing"

    try:
        root_info = root.stat()
        if root_info.st_mode & 0o222:
            failures["dir:."] = "write_bit_present"
        elif require_root_owner and root_info.st_uid != 0:
            failures["dir:."] = "not_root_owned"
        elif require_immutable and (not immutable or not (getattr(root_info, "st_flags", 0) & immutable)):
            failures["dir:."] = "immutable_flag_missing"
    except OSError:
        failures["dir:."] = "missing"

    try:
        state_info = state_path.stat()
        if state_info.st_mode & 0o222:
            failures[STATE_NAME] = "write_bit_present"
        elif require_root_owner and state_info.st_uid != 0:
            failures[STATE_NAME] = "not_root_owned"
    except OSError:
        failures[STATE_NAME] = "missing"

    return {
        "ok": not failures,
        "status": "FOLLOW60_PHYSICAL_WRITE_LOCK_V3_1_OK" if not failures else "FOLLOW60_PHYSICAL_WRITE_LOCK_V3_1_FAILED",
        "lock_version": LOCK_VERSION,
        "protected_file_count": len(protected),
        "failures": failures,
        "write_lock_bypass_available_to_codex": False if not failures and require_root_owner and require_immutable else None,
    }


def direct_write_attempt(path: Path, payload: bytes) -> bool:
    """Test helper: return True only if a direct, non-chmod write succeeds."""
    try:
        with Path(path).open("ab") as handle:
            handle.write(payload)
        return True
    except OSError:
        return False
