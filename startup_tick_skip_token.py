"""Private, atomic startup-skip tokens with optional promotion binding.

Legacy unbound tokens remain readable for ordinary service restarts. Promotion
tokens are canonical JSON bound to transaction, phase, release and dispatcher
module identity. A consumption receipt copies that binding before the mutable
token inode is removed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid
from typing import Any


TOKEN_FILENAME = "auto-restart-skip-startup-tick.once"
TOKEN_MARKER = b"phonefarm-auto-restart-startup-skip-v1\n"
TOKEN_SCHEMA = "PHONEFARM_STARTUP_SKIP_TOKEN_V2"
TOKEN_MODE = 0o600
CONSUMPTION_RECEIPT_SCHEMA = "PHONEFARM_STARTUP_SKIP_CONSUMPTION_V2"
LEGACY_CONSUMPTION_RECEIPT_SCHEMA = "PHONEFARM_STARTUP_SKIP_CONSUMPTION_V1"
CANCELLATION_RECEIPT_SCHEMA = "PHONEFARM_STARTUP_SKIP_CANCELLATION_V2"


class StartupSkipTokenError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeIdentity:
    uid: int
    gid: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _binding_hash(binding: dict[str, Any] | None) -> str:
    return hashlib.sha256(_canonical(binding or {})).hexdigest()


def _validate_binding(binding: dict[str, Any]) -> dict[str, str]:
    required = {"transaction_id", "phase", "candidate_sha", "dispatcher_module_root"}
    if set(binding) != required:
        raise StartupSkipTokenError("startup_token_binding_schema_invalid")
    transaction_id = str(binding["transaction_id"])
    phase = str(binding["phase"])
    candidate_sha = str(binding["candidate_sha"]).lower()
    module_root = str(binding["dispatcher_module_root"])
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", transaction_id):
        raise StartupSkipTokenError("startup_token_transaction_id_invalid")
    if phase not in {"PROMOTE", "ROLLBACK"}:
        raise StartupSkipTokenError("startup_token_phase_invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", candidate_sha):
        raise StartupSkipTokenError("startup_token_candidate_sha_invalid")
    if not Path(module_root).is_absolute():
        raise StartupSkipTokenError("startup_token_dispatcher_root_invalid")
    return {
        "transaction_id": transaction_id,
        "phase": phase,
        "candidate_sha": candidate_sha,
        "dispatcher_module_root": str(Path(module_root).resolve(strict=False)),
    }


def _token_payload(binding: dict[str, Any] | None) -> tuple[bytes, dict[str, Any] | None]:
    if binding is None:
        return TOKEN_MARKER, None
    normalized = _validate_binding(binding)
    value = {"schema": TOKEN_SCHEMA, "token_id": uuid.uuid4().hex, "created_at": _utc_now(), "binding": normalized}
    return _canonical(value) + b"\n", value


def _lstat_directory(path: Path, reason: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StartupSkipTokenError(reason) from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise StartupSkipTokenError(reason)
    return info


def canonical_runtime_identity(runtime_home: Path, token_parent: Path) -> RuntimeIdentity:
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


def _validate_metadata(info: os.stat_result, identity: RuntimeIdentity, *, size: int | None = None) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StartupSkipTokenError("startup_token_not_private_regular_file")
    if stat.S_IMODE(info.st_mode) != TOKEN_MODE:
        raise StartupSkipTokenError("startup_token_mode_mismatch")
    if (info.st_uid, info.st_gid) != (identity.uid, identity.gid):
        raise StartupSkipTokenError("startup_token_owner_group_mismatch")
    if size is not None and info.st_size != size:
        raise StartupSkipTokenError("startup_token_size_mismatch")


def _read_private(path: Path, identity: RuntimeIdentity) -> tuple[os.stat_result, bytes]:
    try:
        before = path.lstat()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise StartupSkipTokenError("startup_token_read_failed") from exc
    try:
        current = os.fstat(fd)
        _validate_metadata(current, identity)
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise StartupSkipTokenError("startup_token_inode_changed")
        data = b""
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
            if len(data) > 65536:
                raise StartupSkipTokenError("startup_token_oversize")
        if len(data) != current.st_size:
            raise StartupSkipTokenError("startup_token_short_read")
        return current, data
    finally:
        os.close(fd)


def _read_exact(path: Path, identity: RuntimeIdentity, expected: bytes) -> os.stat_result:
    """Legacy exact-marker reader retained as a tested protocol boundary."""
    info, data = _read_private(path, identity)
    if data != expected:
        raise StartupSkipTokenError("startup_token_marker_mismatch")
    return info


def _decode_token(data: bytes) -> dict[str, Any]:
    if data == TOKEN_MARKER:
        return {"schema": "PHONEFARM_STARTUP_SKIP_TOKEN_V1", "token_id": None, "binding": None}
    try:
        value = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise StartupSkipTokenError("startup_token_marker_mismatch") from exc
    if not isinstance(value, dict) or value.get("schema") != TOKEN_SCHEMA:
        raise StartupSkipTokenError("startup_token_schema_invalid")
    if not re.fullmatch(r"[0-9a-f]{32}", str(value.get("token_id") or "")):
        raise StartupSkipTokenError("startup_token_id_invalid")
    value["binding"] = _validate_binding(value.get("binding") or {})
    if not isinstance(value.get("created_at"), str):
        raise StartupSkipTokenError("startup_token_created_at_invalid")
    return value


def _apply_private_identity(fd: int, identity: RuntimeIdentity) -> None:
    info = os.fstat(fd)
    if (info.st_uid, info.st_gid) != (identity.uid, identity.gid):
        os.fchown(fd, identity.uid, identity.gid)
    os.fchmod(fd, TOKEN_MODE)


def _atomic_private_bytes(path: Path, payload: bytes, identity: RuntimeIdentity) -> os.stat_result:
    temporary = path.with_name(f".{path.name}.prepare.{os.getpid()}.{uuid.uuid4().hex}")
    fd = -1
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, TOKEN_MODE)
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise StartupSkipTokenError("startup_token_short_write")
            offset += written
        _apply_private_identity(fd, identity)
        os.fsync(fd)
        _validate_metadata(os.fstat(fd), identity, size=len(payload))
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return _read_private(path, identity)[0]
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def consumption_receipt_path(token_path: Path) -> Path:
    return token_path.with_name(token_path.name + ".last-consumed.json")


def _validate_token_path(token_path: Path) -> None:
    if not token_path.is_absolute() or token_path.name != TOKEN_FILENAME:
        raise StartupSkipTokenError("startup_token_path_invalid")


def prepare_startup_skip_token(
    token_path: Path,
    runtime_home: Path,
    binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token_path = Path(token_path)
    _validate_token_path(token_path)
    identity = canonical_runtime_identity(Path(runtime_home), token_path.parent)
    expected_binding = _validate_binding(binding) if binding is not None else None
    directory_fd = os.open(token_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        created = False
        try:
            info, data = _read_private(token_path, identity)
            token = _decode_token(data)
            if expected_binding is not None and token.get("binding") != expected_binding:
                raise StartupSkipTokenError("startup_token_existing_binding_mismatch")
        except FileNotFoundError:
            receipt = consumption_receipt_path(token_path)
            try:
                receipt_info = receipt.lstat()
                _validate_metadata(receipt_info, identity)
                receipt.unlink()
            except FileNotFoundError:
                pass
            payload, token = _token_payload(expected_binding)
            info = _atomic_private_bytes(token_path, payload, identity)
            token = token or {"schema": "PHONEFARM_STARTUP_SKIP_TOKEN_V1", "token_id": None, "binding": None}
            created = True
        evidence = {
            "path": str(token_path), "device": info.st_dev, "inode": info.st_ino,
            "uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode),
            "size": info.st_size, "created": created, "token_id": token.get("token_id"),
            "binding": token.get("binding"), "binding_sha256": _binding_hash(token.get("binding")),
        }
        return {"ok": True, "status": "STARTUP_TOKEN_PREPARED" if created else "STARTUP_TOKEN_ALREADY_PREPARED", "runtime_identity": asdict(identity), "token": evidence, "atomic": True}
    finally:
        fcntl.flock(directory_fd, fcntl.LOCK_UN)
        os.close(directory_fd)


def cancel_prepared_startup_skip_token(token_path: Path, runtime_home: Path, expected_token: dict[str, Any]) -> dict[str, Any]:
    token_path = Path(token_path)
    _validate_token_path(token_path)
    identity = canonical_runtime_identity(Path(runtime_home), token_path.parent)
    directory_fd = os.open(token_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        try:
            info, data = _read_private(token_path, identity)
        except FileNotFoundError:
            receipt = consumption_receipt_path(token_path)
            try:
                _, receipt_data = _read_private(receipt, identity)
                value = json.loads(receipt_data.decode("utf-8"))
            except (FileNotFoundError, OSError, ValueError, StartupSkipTokenError):
                return {"ok": True, "status": "STARTUP_TOKEN_ALREADY_ABSENT"}
            if (
                value.get("schema") == CANCELLATION_RECEIPT_SCHEMA
                and value.get("token_id") == expected_token.get("token_id")
                and value.get("binding") == expected_token.get("binding")
            ):
                return {"ok": True, "status": "STARTUP_TOKEN_ALREADY_CANCELLED", "receipt": str(receipt)}
            return {"ok": True, "status": "STARTUP_TOKEN_ALREADY_ABSENT"}
        if info.st_dev != expected_token.get("device") or info.st_ino != expected_token.get("inode"):
            raise StartupSkipTokenError("startup_token_cancel_identity_mismatch")
        token = _decode_token(data)
        if token.get("token_id") != expected_token.get("token_id") or token.get("binding") != expected_token.get("binding"):
            raise StartupSkipTokenError("startup_token_cancel_binding_mismatch")
        receipt = consumption_receipt_path(token_path)
        receipt_value = {
            "schema": CANCELLATION_RECEIPT_SCHEMA,
            "cancelled_at": _utc_now(),
            "token_device": info.st_dev,
            "token_inode": info.st_ino,
            "token_uid": info.st_uid,
            "token_gid": info.st_gid,
            "token_mode": stat.S_IMODE(info.st_mode),
            "token_id": token.get("token_id"),
            "binding": token.get("binding"),
            "binding_sha256": _binding_hash(token.get("binding")),
        }
        _atomic_private_bytes(receipt, _canonical(receipt_value) + b"\n", identity)
        token_path.unlink()
        os.fsync(directory_fd)
        return {"ok": True, "status": "STARTUP_TOKEN_CANCELLED", "receipt": str(receipt)}
    finally:
        fcntl.flock(directory_fd, fcntl.LOCK_UN)
        os.close(directory_fd)


def consume_startup_skip_token(token_path: Path, runtime_home: Path) -> dict[str, Any]:
    token_path = Path(token_path)
    _validate_token_path(token_path)
    identity = canonical_runtime_identity(Path(runtime_home), token_path.parent)
    try:
        token_info, data = _read_private(token_path, identity)
        token = _decode_token(data)
        if data == TOKEN_MARKER:
            token_info = _read_exact(token_path, identity, TOKEN_MARKER)
    except FileNotFoundError:
        return {"consumed": False, "reason": "guard_absent"}
    except StartupSkipTokenError as exc:
        return {"consumed": False, "reason": str(exc)}
    claimed = token_path.with_name(f"{token_path.name}.consumed.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        os.replace(token_path, claimed)
        claimed_info, claimed_data = _read_private(claimed, identity)
        if data == TOKEN_MARKER:
            claimed_info = _read_exact(claimed, identity, TOKEN_MARKER)
            claimed_data = TOKEN_MARKER
        if (claimed_info.st_dev, claimed_info.st_ino, claimed_data) != (token_info.st_dev, token_info.st_ino, data):
            raise StartupSkipTokenError("guard_changed_during_consume")
        receipt_value = {
            "schema": CONSUMPTION_RECEIPT_SCHEMA,
            "consumed_at": _utc_now(), "consumer_pid": os.getpid(),
            "token_device": token_info.st_dev, "token_inode": token_info.st_ino,
            "token_uid": token_info.st_uid, "token_gid": token_info.st_gid,
            "token_mode": stat.S_IMODE(token_info.st_mode),
            "token_id": token.get("token_id"), "binding": token.get("binding"),
            "binding_sha256": _binding_hash(token.get("binding")),
        }
        _atomic_private_bytes(consumption_receipt_path(token_path), _canonical(receipt_value) + b"\n", identity)
        claimed.unlink()
        directory_fd = os.open(token_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
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
        return {"consumed": False, "reason": str(exc) if isinstance(exc, StartupSkipTokenError) else "guard_cleanup_failed", "cleanup_ok": False}
    return {"consumed": True, "reason": "guard_consumed", "cleanup_ok": True, "token_device": token_info.st_dev, "token_inode": token_info.st_ino, "token_id": token.get("token_id"), "binding": token.get("binding")}


def verify_startup_skip_consumed(token_path: Path, runtime_home: Path, expected_token: dict[str, Any]) -> dict[str, Any]:
    token_path = Path(token_path)
    _validate_token_path(token_path)
    identity = canonical_runtime_identity(Path(runtime_home), token_path.parent)
    if token_path.exists() or token_path.is_symlink():
        return {"ok": False, "status": "STARTUP_TOKEN_PENDING", "reason": "startup_token_not_consumed"}
    receipt = consumption_receipt_path(token_path)
    try:
        info, data = _read_private(receipt, identity)
        _validate_metadata(info, identity)
        value = json.loads(data.decode("utf-8"))
    except FileNotFoundError:
        return {"ok": False, "status": "STARTUP_TOKEN_PROOF_MISSING", "reason": "consumption_receipt_missing"}
    except Exception:
        return {"ok": False, "status": "STARTUP_TOKEN_PROOF_INVALID", "reason": "consumption_receipt_invalid"}
    if value.get("schema") not in {CONSUMPTION_RECEIPT_SCHEMA, LEGACY_CONSUMPTION_RECEIPT_SCHEMA}:
        return {"ok": False, "status": "STARTUP_TOKEN_PROOF_INVALID", "reason": "consumption_receipt_schema"}
    pairs = (("token_device", "device"), ("token_inode", "inode"), ("token_uid", "uid"), ("token_gid", "gid"), ("token_mode", "mode"))
    if any(value.get(receipt_key) != expected_token.get(expected_key) for receipt_key, expected_key in pairs):
        return {"ok": False, "status": "STARTUP_TOKEN_PROOF_MISMATCH", "reason": "consumed_token_identity_mismatch"}
    expected_binding = expected_token.get("binding")
    if expected_binding is not None:
        try:
            expected_binding = _validate_binding(expected_binding)
        except StartupSkipTokenError:
            return {"ok": False, "status": "STARTUP_TOKEN_PROOF_MISMATCH", "reason": "expected_binding_invalid"}
        if value.get("binding") != expected_binding or value.get("binding_sha256") != _binding_hash(expected_binding):
            return {"ok": False, "status": "STARTUP_TOKEN_PROOF_MISMATCH", "reason": "consumed_token_binding_mismatch"}
        if value.get("token_id") != expected_token.get("token_id"):
            return {"ok": False, "status": "STARTUP_TOKEN_PROOF_MISMATCH", "reason": "consumed_token_id_mismatch"}
    return {"ok": True, "status": "STARTUP_TOKEN_GATE_PASS", "token_consumed": True, "token_cleanup": True, "consumer_pid": value.get("consumer_pid"), "token_id": value.get("token_id"), "binding": value.get("binding"), "consumed_at": value.get("consumed_at"), "runtime_identity": asdict(identity)}


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
