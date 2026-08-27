"""Root-owned external seal evidence and a process-bound promotion transaction.

No environment override, no signing, no business calls. Candidate admission is
read-only; only the canonical promotion path acquires a lease. A dead owner's
lease is never silently removed. Physical OS flags remain mandatory.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid

from follow60_deployment_gate_v1 import verify_deployment_candidate, MANIFEST_RELATIVE

STORE = Path('/Users/admin/phonefarm-runtime/run/follow60-lock-v2')
PROTOCOL_VERSION = 'FOLLOW60_EXTERNAL_PHYSICAL_DEPLOYMENT_LOCK_V2'
IMMUTABLE = getattr(stat, 'UF_IMMUTABLE', 2)


class LockFailure(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def _root_only():
    if os.geteuid() != 0:
        raise LockFailure('external_deployment_lock_root_required')


def _outside(root):
    if STORE.resolve() != STORE.absolute():
        raise LockFailure('external_lock_store_symlink_forbidden')
    for path in (STORE, Path(root).resolve()):
        other = Path(root).resolve() if path == STORE else STORE
        if path == other or other in path.parents:
            raise LockFailure('external_lock_store_overlaps_candidate')


def _trusted(path, *, directory=False, immutable=True):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & (0o022 if directory else 0o222):
        raise LockFailure('external_lock_metadata_invalid:' + path.name)
    if not directory and info.st_nlink != 1:
        raise LockFailure('external_lock_hardlink_forbidden')
    if immutable and not info.st_flags & IMMUTABLE:
        raise LockFailure('external_lock_immutable_missing:' + path.name)


def initialize_store(root):
    """Installer-only initialization. No existing record is overwritten."""
    _root_only()
    _outside(root)
    if not STORE.exists():
        STORE.mkdir(mode=0o755)
        for name in ('seals', 'transactions', 'history'):
            (STORE / name).mkdir(mode=0o755 if name == 'seals' else 0o700)
        with (STORE / 'guard').open('xb') as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(STORE / 'guard', 0o444)
        os.chflags(STORE / 'guard', IMMUTABLE)
        os.chflags(STORE, IMMUTABLE)
    _storage(root)


def _storage(root):
    _outside(root)
    _trusted(STORE, directory=True)
    for name in ('seals', 'transactions', 'history'):
        _trusted(STORE / name, directory=True, immutable=False)
    _trusted(STORE / 'guard')


@contextmanager
def storage_guard(root):
    _root_only()
    _storage(root)
    fd = os.open(STORE / 'guard', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(fd).st_ino != (STORE / 'guard').lstat().st_ino:
            raise LockFailure('external_lock_guard_replaced')
        yield
    except BlockingIOError as exc:
        raise LockFailure('external_promotion_already_owned') from exc
    finally:
        os.close(fd)


def atomic_record(path, payload):
    """Exclusive, fsynced publication in a root-controlled, guarded directory."""
    data = (json.dumps(payload, indent=2, sort_keys=True) + '\n').encode()
    _atomic_bytes(path, data)


def _atomic_bytes(path, data):
    _root_only()
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o444)
        os.link(temporary, path, follow_symlinks=False)  # Never overwrite.
        temporary.unlink()
        os.chflags(path, IMMUTABLE)
        _sync_directory(path.parent)
        _trusted(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def seal_path(root):
    _outside(root)
    return STORE / 'seals' / (hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest() + '.json')


def owner_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except OSError as exc:
        raise LockFailure('owner_liveness_unknown') from exc
    proc = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'lstart=', '-o', 'uid='],
                          text=True, capture_output=True)
    if proc.returncode or not proc.stdout.strip():
        raise LockFailure('owner_identity_unknown')
    boot = subprocess.check_output(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'], text=True).strip()
    return {'pid': pid, 'process_start_and_uid': ' '.join(proc.stdout.split()), 'boot_session': boot}


def exact_admission(root):
    value = verify_deployment_candidate(Path(root))
    if not value.get('ok'):
        raise LockFailure('candidate_gate:' + str(value.get('reason')))
    identity = value['candidate_identity']
    return {key: identity[key] for key in ('candidate_commit_sha', 'candidate_tree_sha', 'canonical_diff_sha256')} | {
        'manifest_sha256': value['manifest_sha256'],
        'signature_sha256': digest(Path(root) / 'docs/governance/FOLLOW60_MAINLINE_LOCK_V3.sig'),
        'candidate_root': str(Path(root).resolve()),
    }


def verify_seal(root):
    from follow60_write_lock_v3_1 import verify_physical_write_lock
    value = verify_physical_write_lock(Path(root), Path(root) / MANIFEST_RELATIVE)
    if not value.get('ok'):
        raise LockFailure('physical_seal:' + str(value.get('reason') or value.get('failures')))
    return value


def seal_payload(root, admission, protected, directories):
    return {'schema': 'FOLLOW60_PHYSICAL_WRITE_LOCK_V3_1', 'lock_version': '3.1.0',
            'lock_state': 'LOCKED', 'protocol_version': PROTOCOL_VERSION, **admission,
            'verified_revision': admission['candidate_commit_sha'], 'created_at': now(),
            'owner': owner_identity(), 'protected_paths': list(protected),
            'protected_directories': list(directories),
            'unlock_contract': 'LIAM_SIGNED_EXACT_DIFF_TRANSACTION_ONLY'}


def read_record(path):
    _trusted(path)
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise LockFailure('external_lock_schema_invalid')
    return value


def validate_lease(value):
    keys = {'protocol_version', 'candidate_commit_sha', 'candidate_tree_sha',
            'canonical_diff_sha256', 'manifest_sha256', 'signature_sha256',
            'candidate_root', 'created_at', 'owner', 'seal_sha256'}
    if set(value) != keys or value['protocol_version'] != PROTOCOL_VERSION:
        raise LockFailure('external_lease_schema_invalid')
    for key in ('candidate_commit_sha', 'candidate_tree_sha', 'canonical_diff_sha256',
                'manifest_sha256', 'signature_sha256', 'seal_sha256'):
        length = 40 if key in ('candidate_commit_sha', 'candidate_tree_sha') else 64
        if not isinstance(value[key], str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', value[key]):
            raise LockFailure('external_lease_hash_invalid')
    owner = value['owner']
    if (not isinstance(owner, dict) or set(owner) != {'pid', 'process_start_and_uid', 'boot_session'}
            or type(owner['pid']) is not int or owner['pid'] <= 0
            or not all(isinstance(owner[key], str) and owner[key] for key in ('process_start_and_uid', 'boot_session'))):
        raise LockFailure('external_lease_owner_invalid')
    created = datetime.fromisoformat(value['created_at'])
    if created.tzinfo is None or created > datetime.now(timezone.utc):
        raise LockFailure('external_lease_timestamp_invalid')
    if not Path(value['candidate_root']).is_absolute():
        raise LockFailure('external_lease_root_invalid')


def _archive_active(expected, reason):
    active = STORE / 'transactions' / 'active.json'
    if digest(active) != expected:
        raise LockFailure('external_lock_changed_before_archive')
    target = STORE / 'history' / (reason + '-' + expected + '-' + uuid.uuid4().hex + '.json')
    # Preserve exact bytes durably BEFORE retiring the active path. Failure
    # before publication leaves the original blocking record untouched.
    _atomic_bytes(target, active.read_bytes())
    os.chflags(active, 0)
    try:
        active.unlink()
    except BaseException:
        os.chflags(active, IMMUTABLE)
        raise
    _sync_directory(active.parent)
    return target


def recover_stale(root, expected_sha256):
    """Explicit root-only forensic recovery; never time-only or automatic."""
    with storage_guard(root):
        path = STORE / 'transactions' / 'active.json'
        value = read_record(path)
        validate_lease(value)
        if value['candidate_root'] != str(Path(root).resolve()):
            raise LockFailure('stale_lock_candidate_root_mismatch')
        if digest(path) != expected_sha256:
            raise LockFailure('stale_lock_expected_hash_mismatch')
        owner = value.get('owner')
        actual = owner_identity(owner['pid'])
        if actual == owner:
            raise LockFailure('stale_lock_owner_still_alive')
        # None = proved ESRCH; differing boot/start identity = proved PID reuse.
        return _archive_active(expected_sha256, 'stale-owner-proved-dead')


class DeploymentTransaction:
    """Same-process lease held across zero-gate, switch and immutable receipt."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.fd_guard = None
        self.expected = None
        self.record_hash = None
        self.owner = None

    def __enter__(self):
        self.expected = exact_admission(self.root)
        verify_seal(self.root)
        self.fd_guard = storage_guard(self.root)
        self.fd_guard.__enter__()
        try:
            active = STORE / 'transactions' / 'active.json'
            if active.exists() or active.is_symlink():
                raise LockFailure('existing_deployment_lock_requires_explicit_recovery')
            self.owner = owner_identity()
            self.payload = {'protocol_version': PROTOCOL_VERSION, **self.expected,
                            'created_at': now(), 'owner': self.owner,
                            'seal_sha256': digest(seal_path(self.root))}
            atomic_record(active, self.payload)
            self.record_hash = digest(active)
            self.recheck()
            return self
        except BaseException:
            self.fd_guard.__exit__(None, None, None)
            self.fd_guard = None
            raise

    def recheck(self):
        if self.fd_guard is None or self.record_hash is None:
            raise LockFailure('deployment_lock_not_acquired')
        _storage(self.root)
        active = STORE / 'transactions' / 'active.json'
        payload = read_record(active)
        validate_lease(payload)
        if digest(active) != self.record_hash or payload != self.payload:
            raise LockFailure('deployment_lock_tampered')
        if owner_identity() != self.owner or payload['owner'] != self.owner:
            raise LockFailure('deployment_lock_owner_mismatch')
        if digest(seal_path(self.root)) != payload['seal_sha256']:
            raise LockFailure('physical_seal_changed_after_lock')
        if exact_admission(self.root) != self.expected:
            raise LockFailure('candidate_changed_after_lock')
        verify_seal(self.root)
        return {'ok': True, 'status': 'EXTERNAL_DEPLOYMENT_PREFLIGHT_PASS', **self.expected,
                'physical_write_lock': 'PASS', 'toctou_recheck_after_lock': True}

    def finish(self, receipt_path):
        self.recheck()
        path = Path(receipt_path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not info.st_flags & IMMUTABLE:
            raise LockFailure('promotion_receipt_not_immutable')
        receipt = json.loads(path.read_text())
        if (receipt.get('schema') != 'PHONE_FARM_IMMUTABLE_PROMOTION_RECEIPT_V1'
                or receipt.get('receipt_state') != 'PROMOTED'
                or receipt.get('candidate_sha') != self.expected['candidate_commit_sha']
                or receipt.get('manifest_sha256') != self.expected['manifest_sha256']
                or receipt.get('runtime_root') != str(self.root)):
            raise LockFailure('promotion_receipt_lock_binding_mismatch')
        return _archive_active(self.record_hash, 'promoted')

    def __exit__(self, *exc):
        # Failure leaves the immutable record intact for explicit stale recovery.
        if self.fd_guard is not None:
            self.fd_guard.__exit__(*exc)
            self.fd_guard = None
