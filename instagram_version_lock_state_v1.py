"""Scoped, durable Instagram version baseline; never writes the sealed release.

This is not the privileged Follow60 physical write lock. Missing/corrupt or
conflicting state is never silently treated as permission to reset a baseline.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import secrets


class LockStateError(RuntimeError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise LockStateError("instagram_lock_state_symlink")


def _read(path: Path, *, private: bool = False) -> tuple[dict | None, str]:
    _safe_path(path)
    try:
        parent_fd = _directory_fd(path.parent, create=False)
    except FileNotFoundError:
        return None, ""
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            return None, ""
    finally:
        os.close(parent_fd)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 65536
                or (private and (info.st_uid != os.getuid() or info.st_nlink != 1
                                 or info.st_mode & 0o077))):
            raise LockStateError("instagram_lock_state_file_invalid")
        data = stream.read(65537)
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError()
    except (ValueError, UnicodeError) as exc:
        raise LockStateError("instagram_lock_state_json_invalid") from exc
    return value, _digest(data)


def _validate_baseline(state: dict) -> None:
    code, name = state.get("authorized_version_code"), state.get("authorized_version_name")
    if ((code is not None and (type(code) is not int or code <= 0))
            or (name is not None and (not isinstance(name, str) or not name.strip()))
            or (code is None and name is None)):
        raise LockStateError("instagram_lock_state_baseline_unproved")


def _directory_fd(path: Path, *, create: bool = True) -> int:
    """Create/open the external writer directory without following any symlink."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                os.fsync(fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


class VersionLockStore:
    def __init__(self, *, run_dir: Path, release_root: Path, serial: str, package: str,
                 host: str | None = None):
        if not serial or not package:
            raise LockStateError("instagram_lock_state_scope_missing")
        self.release_root = release_root.resolve()
        self.root = Path(os.path.abspath(run_dir)) / "instagram-version-lock-v1"
        _safe_path(self.root)
        if self.root == self.release_root or self.release_root in self.root.parents:
            raise LockStateError("instagram_lock_state_inside_release")
        self.scope = {"host": host or socket.gethostname(), "device_serial": serial, "package": package}
        key = _digest(json.dumps(self.scope, sort_keys=True).encode())
        self.directory = self.root / key
        self.path = self.directory / "lock_state.json"
        self.legacy = self.release_root / "lock_state.json"

    def read(self) -> dict:
        external, _ = _read(self.path, private=True)
        legacy, legacy_hash = _read(self.legacy)
        if legacy is not None:
            if not legacy.get("device_serial") or not legacy.get("package"):
                raise LockStateError("instagram_lock_state_legacy_scope_unproved")
            if any(legacy[k] != self.scope[k] for k in ("device_serial", "package")):
                legacy, legacy_hash = None, ""  # Proven other phone/clone, not this baseline.
            else:
                _validate_baseline(legacy)
        if external is not None:
            if (external.get("schema") != "instagram_version_lock_state_v1"
                    or external.get("scope") != self.scope
                    or type(external.get("generation")) is not int
                    or external["generation"] < 1
                    or not isinstance(external.get("state"), dict)):
                raise LockStateError("instagram_lock_state_external_invalid")
            if legacy is not None and external.get("legacy_sha256") != legacy_hash:
                raise LockStateError("instagram_lock_state_ambiguous_dual_state")
            state = dict(external["state"])
            _validate_baseline(state)
            if any(state.get(k) != self.scope[k] for k in ("device_serial", "package")):
                raise LockStateError("instagram_lock_state_binding_mismatch")
            return {**state, "_storage_generation": external["generation"],
                    "_legacy_sha256": external.get("legacy_sha256", "")}
        return {**(legacy or {}), "_storage_generation": 0, "_legacy_sha256": legacy_hash}

    @contextmanager
    def _writer(self):
        _safe_path(self.directory)
        directory_fd = _directory_fd(self.directory)
        try:
            directory_info = os.fstat(directory_fd)
            if directory_info.st_uid != os.getuid() or directory_info.st_mode & 0o077:
                raise LockStateError("instagram_lock_state_directory_permissions")
            fd = os.open("writer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
            try:
                writer_info = os.fstat(fd)
                if (not stat.S_ISREG(writer_info.st_mode) or writer_info.st_uid != os.getuid()
                        or writer_info.st_nlink != 1 or writer_info.st_mode & 0o077):
                    raise LockStateError("instagram_lock_state_writer_invalid")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise LockStateError("instagram_lock_state_writer_busy") from exc
                yield directory_fd
            finally:
                os.close(fd)
        finally:
            os.close(directory_fd)

    def write(self, state: dict) -> None:
        with self._writer() as directory_fd:
            current = self.read()
            _validate_baseline(state)
            if (state.get("_storage_generation") != current["_storage_generation"]
                    or state.get("_legacy_sha256") != current["_legacy_sha256"]):
                raise LockStateError("instagram_lock_state_generation_changed")
            if any(state.get(k) != self.scope[k] for k in ("device_serial", "package")):
                raise LockStateError("instagram_lock_state_binding_mismatch")
            payload = {"schema": "instagram_version_lock_state_v1", "scope": self.scope,
                       "generation": current["_storage_generation"] + 1,
                       "legacy_sha256": current["_legacy_sha256"],
                       "state": {k: v for k, v in state.items() if not k.startswith("_")}}
            # Append-only durable intent before changing the authoritative file.
            # Records no credentials; replay never treats a journal as authority.
            journal_fd = os.open("journal.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory_fd)
            with os.fdopen(journal_fd, "a") as journal:
                info = os.fstat(journal.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_nlink != 1 or info.st_mode & 0o077):
                    raise LockStateError("instagram_lock_state_journal_invalid")
                journal.write(json.dumps({"operation": "replace_state", "scope": self.scope,
                                          "previous_generation": current["_storage_generation"],
                                          "next_generation": payload["generation"],
                                          "legacy_sha256": current["_legacy_sha256"],
                                          "payload_sha256": _digest(json.dumps(payload, sort_keys=True).encode())},
                                         sort_keys=True) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
            os.fsync(directory_fd)
            temporary = ".lock-state-" + secrets.token_hex(12)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory_fd)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(payload, stream, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                _safe_path(self.path)
                live = self.directory.stat()
                held = os.fstat(directory_fd)
                if (live.st_dev, live.st_ino) != (held.st_dev, held.st_ino):
                    raise LockStateError("instagram_lock_state_directory_changed")
                os.replace(temporary, "lock_state.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                os.fsync(directory_fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass


def canonical_store(*, release_root: Path, serial: str | None, package: str) -> VersionLockStore:
    from phonefarm_runtime_control import runtime_paths
    return VersionLockStore(run_dir=runtime_paths().run_dir, release_root=release_root,
                            serial=serial or "", package=package)
