"""Canonical shared startup-skip token protocol.

The promotion controller may run as root while the dispatcher is a per-user
launchd service.  Token ownership is therefore derived from the canonical
runtime home and its shared dispatcher run directory, never from the creator's
effective uid and never from a hard-coded username.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import uuid
from typing import Any


TOKEN_FILENAME = "auto-restart-skip-startup-tick.once"
TOKEN_MARKER = b"phonefarm-auto-restart-startup-skip-v1\n"
TOKEN_MODE = 0o600
CONSUMPTION_RECEIPT_SCHEMA = "PHONEFARM_STARTUP_SKIP_CONSUMPTION_V1"


class StartupSkipTokenError(RuntimeError):
    """Stable fail-closed protocol error."""


@dataclass(frozen=True)
class RuntimeIdentity:
    uid: int
    gid: int


@dataclass(frozen=True)
class TokenEvidence:
    path: str
    device: int
    inode: int
    uid: int
    gid: int
    mode: int
    size: int
    created: bool


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lstat_directory(path: Path, reason: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StartupSkipTokenError(reason) from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise StartupSkipTokenError(reason)
    return info


def canonical_runtime_identity(runtime_home: Path, token_parent: Path) -> RuntimeIdentity:
    """Resolve the launchd service identity from canonical runtime metadata."""

    runtime_home = Path(runtime_home)
    token_parent = Path(token_parent)
    if not runtime_home.is_absolute() or not token_parent.is_absolute():
        raise StartupSkipTokenError("startup_token_identity_path_not_absolute")
    home_info = _lstat_directory(runtime_home, "startup_token_runtime_home_invalid")
    parent_info = _lstat_directory(token_parent, "startup_token_run_directory_invalid")
    identity = RuntimeIdentity(uid=home_info.st_uid, gid=home_info.st_gid)
    if (parent_info.st_uid, parent_info.st_gid) != (identity.uid, identity.gid):
        raise StartupSkipTokenError("startup_token_run_directory_identity_mismatch")
    if stat.S_IMODE(parent_info.st_mode) & 0o022:
        raise StartupSkipTokenError("startup_token_run_directory_writable_by_others")
    return identity


def _validate_metadata(info: os.stat_result, identity: RuntimeIdentity, *, size: int) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StartupSkipTokenError("startup_token_not_private_regular_file")
    if stat.S_IMODE(info.st_mode) != TOKEN_MODE:
        raise StartupSkipTokenError("startup_token_mode_mismatch")
    if (info.st_uid, info.st_gid) != (identity.uid, identity.gid):
        raise StartupSkipTokenError("startup_token_owner_group_mismatch")
    if info.st_size != size:
        raise StartupSkipTokenError("startup_token_size_mismatch")


def _read_exact(path: Path, identity: RuntimeIdentity, expected: bytes) -> os.stat_result:
    try:
        before = path.lstat()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise StartupSkipTokenError("startup_token_read_failed") from exc
    try:
        current = os.fstat(fd)
        _validate_metadata(current, identity, size=len(expected))
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise StartupSkipTokenError("startup_token_inode_changed")
        if os.read(fd, len(expected) + 1) != expected:
            raise StartupSkipTokenError("startup_token_marker_mismatch")
        return current
    finally:
        os.close(fd)


def consumption_receipt_path(token_path: Path) -> Path:
    return token_path.with_name(token_path.name + ".last-consumed.json")


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise StartupSkipTokenError("startup_token_short_write")
        offset += written


def _apply_private_identity(fd: int, identity: RuntimeIdentity) -> None:
    current = os.fstat(fd)
    if (current.st_uid, current.st_gid) != (identity.uid, identity.gid):
        try:
            os.fchown(fd, identity.uid, identity.gid)
        except OSError as exc:
            raise StartupSkipTokenError("startup_token_owner_group_setup_failed") from exc
    try:
        os.fchmod(fd, TOKEN_MODE)
    except OSError as exc:
        raise StartupSkipTokenError("startup_token_mode_setup_failed") from exc


def _atomic_private_bytes(path: Path, payload: bytes, identity: RuntimeIdentity) -> os.stat_result:
    temporary = path.with_name(f".{path.name}.prepare.{os.getpid()}.{uuid.uuid4().hex}")
    fd = -1
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            TOKEN_MODE,
        )
        _write_all(fd, payload)
        _apply_private_identity(fd, identity)
        os.fsync(fd)
        _validate_metadata(os.fstat(fd), identity, size=len(payload))
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return _read_exact(path, identity, payload)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _remove_previous_receipt(receipt: Path, identity: RuntimeIdentity) -> None:
    try:
        info = receipt.lstat()
    except FileNotFoundError:
        return
    _validate_metadata(info, identity, size=info.st_size)
    try:
        receipt.unlink()
    except OSError as exc:
        raise StartupSkipTokenError("startup_token_previous_receipt_cleanup_failed") from exc


def _validate_token_path(token_path: Path) -> None:
    if not token_path.is_absolute() or token_path.name != TOKEN_FILENAME:
        raise StartupSkipTokenError("startup_token_path_invalid")


def prepare_startup_skip_token(token_path: Path, runtime_home: Path) -> dict[str, Any]:
    """Create one fully-written private token or validate the existing one."""

    token_path = Path(token_path)
    _validate_token_path(token_path)
    identity = canonical_runtime_identity(Path(runtime_home), token_path.parent)
    directory_fd = os.open(token_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        try:
            info = _read_exact(token_path, identity, TOKEN_MARKER)
            created = False
        except FileNotFoundError:
            _remove_previous_receipt(consumption_receipt_path(token_path), identity)
            info = _atomic_private_bytes(token_path, TOKEN_MARKER, identity)
            created = True
        evidence = TokenEvidence(
            path=str(token_path),
            device=info.st_dev,
            inode=info.st_ino,
            uid=info.st_uid,
            gid=info.st_gid,
            mode=stat.S_IMODE(info.st_mode),
            size=info.st_size,
            created=created,
        )
        return {
            "ok": True,
            "status": "STARTUP_TOKEN_PREPARED" if created else "STARTUP_TOKEN_ALREADY_PREPARED",
            "runtime_identity": asdict(identity),
            "token": asdict(evidence),
            "atomic": True,
        }
    finally:
        fcntl.flock(directory_fd, fcntl.LOCK_UN)
        os.close(directory_fd)


def cancel_prepared_startup_skip_token(
    token_path: Path,
    runtime_home: Path,
    expected_token: dict[str, Any],
) -> dict[str, Any]:
    """Remove only the exact token prepared by the interrupted promotion."""

    token_path = Path(token_path)
    _validate_token_path(token_path)
    identity = canonical_runtime_identity(Path(runtime_home), token_path.parent)
    directory_fd = os.open(token_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        try:
            info = _read_exact(token_path, identity, TOKEN_MARKER)
        except FileNotFoundError:
            return {"ok": True, "status": "STARTUP_TOKEN_ALREADY_ABSENT"}
        if (
            info.st_dev != expected_token.get("device")
            or info.st_ino != expected_token.get("inode")
        ):
            raise StartupSkipTokenError("startup_token_cancel_identity_mismatch")
        token_path.unlink()
        os.fsync(directory_fd)
        return {"ok": True, "status": "STARTUP_TOKEN_CANCELLED"}
    finally:
        fcntl.flock(directory_fd, fcntl.LOCK_UN)
        os.close(directory_fd)


def _atomic_consumption_receipt(
    path: Path,
    identity: RuntimeIdentity,
    token_info: os.stat_result,
) -> None:
    payload = {
        "schema": CONSUMPTION_RECEIPT_SCHEMA,
        "consumed_at": _utc_now(),
        "consumer_pid": os.getpid(),
        "token_device": token_info.st_dev,
        "token_inode": token_info.st_ino,
        "token_uid": token_info.st_uid,
        "token_gid": token_info.st_gid,
        "token_mode": stat.S_IMODE(token_info.st_mode),
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _atomic_private_bytes(path, encoded, identity)


def consume_startup_skip_token(token_path: Path, runtime_home: Path) -> dict[str, Any]:
    """Atomically claim, prove and remove the pending token exactly once."""

    token_path = Path(token_path)
    _validate_token_path(token_path)
    identity = canonical_runtime_identity(Path(runtime_home), token_path.parent)
    try:
        token_info = _read_exact(token_path, identity, TOKEN_MARKER)
    except FileNotFoundError:
        return {"consumed": False, "reason": "guard_absent"}
    except StartupSkipTokenError as exc:
        return {"consumed": False, "reason": str(exc)}

    claimed = token_path.with_name(
        f"{token_path.name}.consumed.{os.getpid()}.{uuid.uuid4().hex}"
    )
    try:
        os.replace(token_path, claimed)
    except FileNotFoundError:
        return {"consumed": False, "reason": "guard_already_consumed"}
    except OSError:
        return {"consumed": False, "reason": "guard_consume_failed"}

    try:
        claimed_info = _read_exact(claimed, identity, TOKEN_MARKER)
        if (claimed_info.st_dev, claimed_info.st_ino) != (token_info.st_dev, token_info.st_ino):
            raise StartupSkipTokenError("guard_changed_during_consume")
        _atomic_consumption_receipt(consumption_receipt_path(token_path), identity, token_info)
        claimed.unlink()
        directory_fd = os.open(token_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (OSError, StartupSkipTokenError) as exc:
        try:
            if claimed.exists() and not token_path.exists():
                os.replace(claimed, token_path)
        except OSError:
            pass
        reason = str(exc) if isinstance(exc, StartupSkipTokenError) else "guard_cleanup_failed"
        return {"consumed": False, "reason": reason, "cleanup_ok": False}
    return {
        "consumed": True,
        "reason": "guard_consumed",
        "cleanup_ok": True,
        "token_device": token_info.st_dev,
        "token_inode": token_info.st_ino,
    }


def verify_startup_skip_consumed(
    token_path: Path,
    runtime_home: Path,
    expected_token: dict[str, Any],
) -> dict[str, Any]:
    """Prove the exact prepared inode was consumed and cleaned up."""

    token_path = Path(token_path)
    _validate_token_path(token_path)
    identity = canonical_runtime_identity(Path(runtime_home), token_path.parent)
    if token_path.exists() or token_path.is_symlink():
        return {"ok": False, "status": "STARTUP_TOKEN_PENDING", "reason": "startup_token_not_consumed"}
    receipt = consumption_receipt_path(token_path)
    try:
        info = receipt.lstat()
        _validate_metadata(info, identity, size=info.st_size)
        value = json.loads(receipt.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ok": False, "status": "STARTUP_TOKEN_PROOF_MISSING", "reason": "consumption_receipt_missing"}
    except (OSError, ValueError, StartupSkipTokenError):
        return {"ok": False, "status": "STARTUP_TOKEN_PROOF_INVALID", "reason": "consumption_receipt_invalid"}
    if value.get("schema") != CONSUMPTION_RECEIPT_SCHEMA:
        return {"ok": False, "status": "STARTUP_TOKEN_PROOF_INVALID", "reason": "consumption_receipt_schema"}
    if (
        value.get("token_device") != expected_token.get("device")
        or value.get("token_inode") != expected_token.get("inode")
        or value.get("token_uid") != expected_token.get("uid")
        or value.get("token_gid") != expected_token.get("gid")
        or value.get("token_mode") != expected_token.get("mode")
    ):
        return {"ok": False, "status": "STARTUP_TOKEN_PROOF_MISMATCH", "reason": "consumed_token_identity_mismatch"}
    return {
        "ok": True,
        "status": "STARTUP_TOKEN_GATE_PASS",
        "token_consumed": True,
        "token_cleanup": True,
        "consumer_pid": value.get("consumer_pid"),
        "runtime_identity": asdict(identity),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canonical startup-skip token protocol")
    parser.add_argument("command", choices=("prepare",))
    parser.add_argument("--token-path", required=True)
    parser.add_argument("--runtime-home", required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare_startup_skip_token(Path(args.token_path), Path(args.runtime_home))
    except StartupSkipTokenError as exc:
        print(json.dumps({"ok": False, "status": "STARTUP_TOKEN_PERMISSION_SETUP_FAILURE", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
