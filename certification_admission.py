"""Certification-only candidate admission. No device or UI dependencies.

Disabled by default; environment variables cannot activate this module. The
external launcher must verify/admit a run and explicitly configure its permit.
"""
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import hashlib
import json
import os
import re
import tempfile
from threading import RLock

IDENTITY_UNPROVED = 'certification_candidate_identity_unproved_pre_transport'
_REQUESTED = False
_SESSION = None


class CertificationStop(BaseException):
    """Not caught by candidate-local Exception recovery/skip handlers."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
        self.summary = {'first_causal_reason': reason, 'root_failure_code': reason,
                        'certification_only': True, 'safe_to_continue_ui': False}


def _utc(value):
    # Strict PostgreSQL/ISO representation, also on Python 3.9.
    if type(value) is not str:
        raise CertificationStop('PERMIT_INVALID')
    match = re.fullmatch(r'(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)', value)
    if not match:
        raise CertificationStop('PERMIT_INVALID')
    body, fraction, zone = match.groups()
    if zone != 'Z' and (int(zone[1:3]) > 23 or int(zone[4:]) > 59):
        raise CertificationStop('PERMIT_INVALID')
    try:
        return datetime.fromisoformat(body + ('.' + fraction.ljust(6, '0') if fraction else '') + ('+00:00' if zone == 'Z' else zone))
    except ValueError:
        raise CertificationStop('PERMIT_INVALID')


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class _PermitStore:
    """Internal durable primitive; session owns its lock for the entire execution.

    Separate permanent lock inode survives atomic state replacement. O_EXCL lock
    creation is the issuance tombstone: a missing state on restart never resets.
    """
    def __init__(self, directory, binding):
        self.binding = json.loads(_bytes(binding))
        self.pid = os.getpid()
        self.fd = None
        self.mutex = RLock()
        key = hashlib.sha256(binding['permit_id'].encode()).hexdigest()
        directory = Path(directory)
        if not directory.is_dir() or directory.is_symlink():
            raise CertificationStop('PERMIT_INVALID')
        self.path = directory / (key + '.json')
        lock = directory / (key + '.lock')
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
        created = False
        try:
            try:
                fd = os.open(lock, flags | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                fd = os.open(lock, flags)
            self.fd = fd
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if created:
                if self.path.exists():
                    raise CertificationStop('PERMIT_INVALID')
                os.fsync(fd)
                self._sync_parent()
                self._write({'binding': self.binding, 'state': 'AVAILABLE', 'candidate_key': None})
            self._read()
        except (OSError, ValueError, TypeError, CertificationStop) as exc:
            self.close()
            if isinstance(exc, CertificationStop):
                raise
            raise CertificationStop('PERMIT_INVALID') from exc

    def _sync_parent(self):
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write(self, value):
        fd, name = tempfile.mkstemp(prefix=self.path.name + '.', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(_bytes(value)); stream.flush(); os.fsync(stream.fileno())
            os.replace(name, self.path)
            self._sync_parent()
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _read(self):
        if self.fd is None or self.pid != os.getpid():
            raise CertificationStop('BINDING_MISMATCH')
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd) as stream:
                value = json.load(stream)
            if value.get('binding') != self.binding:
                raise CertificationStop('BINDING_MISMATCH')
            if value.get('state') not in ('AVAILABLE', 'CONSUMED_BOUND', 'TERMINAL'):
                raise CertificationStop('PERMIT_INVALID')
            if value['state'] == 'CONSUMED_BOUND' and not isinstance(value.get('candidate_key'), list):
                raise CertificationStop('PERMIT_INVALID')
            return value
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise CertificationStop('PERMIT_INVALID') from exc

    def admit(self, key):
        with self.mutex:
            return self._admit_locked(key)

    def _admit_locked(self, key):
        value = self._read()
        if value['state'] == 'TERMINAL':
            raise CertificationStop('PERMIT_ALREADY_TERMINAL')
        if value['state'] == 'AVAILABLE':
            value.update(state='CONSUMED_BOUND', candidate_key=list(key))
            self._write(value)
            return 'FIRST_ADMISSION_GRANTED'
        if value['candidate_key'] == list(key):
            return 'SAME_CANDIDATE_REENTRY_GRANTED'
        self.terminal('SECOND_CANDIDATE_BLOCKED')
        raise CertificationStop('SECOND_CANDIDATE_BLOCKED')

    def terminal(self, reason):
        with self.mutex:
            return self._terminal_locked(reason)

    def _terminal_locked(self, reason):
        value = self._read()
        if value['state'] != 'TERMINAL':
            value.update(state='TERMINAL', terminal_reason=reason)
            self._write(value)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _canonical_authority(binding):
    """Existing read-only canonical authorities, never caller-supplied validators."""
    from auto_restart_device_lock import _load_device_lock
    from account_run_control import get_account_run_request
    from supabase_client import load_run_row
    lease = _load_device_lock(binding['device_id']) or {}
    request = get_account_run_request(binding['request_id'], bounded_pre_device=True) or {}
    run = load_run_row(binding['run_id'], request_timeout=5.0, max_retries=0) or {}
    for key in ('device_id', 'worker_id', 'request_id', 'account_id', 'lease_id'):
        if lease.get(key) != binding[key]:
            raise CertificationStop('BINDING_MISMATCH')
    if _utc(lease.get('lease_expires_at')) <= datetime.now(timezone.utc):
        raise CertificationStop('BINDING_MISMATCH')
    if (request.get('id'), request.get('account_id'), request.get('run_id'), request.get('claimed_by')) != (
            binding['request_id'], binding['account_id'], binding['run_id'], binding['worker_id']):
        raise CertificationStop('BINDING_MISMATCH')
    request_lease = (request.get('metadata_safe') or {}).get('device_lock_binding') or {}
    for key in ('device_id', 'worker_id', 'request_id', 'account_id', 'lease_id'):
        if request_lease.get(key) != binding[key]:
            raise CertificationStop('BINDING_MISMATCH')
    if request_lease.get('generation') != binding['lease_id']:
        raise CertificationStop('BINDING_MISMATCH')
    if (request.get('cancel_requested_at') or request.get('cancel_reason')
            or _utc(request.get('lease_expires_at')) <= datetime.now(timezone.utc)):
        raise CertificationStop('PERMIT_ALREADY_TERMINAL')
    if request.get('status') not in ('starting', 'running') or run.get('status') != 'running':
        raise CertificationStop('PERMIT_ALREADY_TERMINAL')
    if (run.get('id'), run.get('account_id')) != (binding['run_id'], binding['account_id']):
        raise CertificationStop('BINDING_MISMATCH')


def configure_from_signed_permit(*, manifest_path, signature_path, permit_id,
                                 execution_binding, state_directory):
    global _REQUESTED, _SESSION
    if _REQUESTED:
        raise CertificationStop('BINDING_MISMATCH')
    _REQUESTED = True  # Invalid configuration must never revert to production.
    try:
        from follow60_deployment_gate_v1 import verify_deployment_candidate
        root = Path(__file__).resolve().parent
        raw = Path(manifest_path).read_bytes()
        result = verify_deployment_candidate(root, manifest_path=Path(manifest_path), signature_path=Path(signature_path))
        if not result.get('ok') or Path(manifest_path).read_bytes() != raw:
            raise CertificationStop('PERMIT_INVALID')
        manifest = json.loads(raw)
        matches = [p for p in manifest.get('a0_candidate_admission_permits', []) if p.get('permit_id') == permit_id]
        if len(matches) != 1:
            raise CertificationStop('PERMIT_INVALID')
        claims = matches[0]
        required = {'permit_id', 'nonce', 'candidate_sha', 'account_id', 'serial', 'package',
                    'device_id', 'version_name', 'certification_not_before', 'certification_expires_at',
                    'max_candidate_admissions'}
        if set(claims) != required or type(claims['max_candidate_admissions']) is not int or claims['max_candidate_admissions'] != 1:
            raise CertificationStop('PERMIT_INVALID')
        if any(type(claims[k]) is not str or not claims[k] or claims[k].strip() != claims[k]
               for k in required - {'max_candidate_admissions'}):
            raise CertificationStop('PERMIT_INVALID')
        if claims['candidate_sha'] != manifest.get('certified_candidate_sha'):
            raise CertificationStop('BINDING_MISMATCH')
        expected = {'run_id', 'request_id', 'worker_id', 'lease_id', 'account_id', 'serial',
                    'package', 'device_id', 'version_name', 'candidate_sha'}
        if set(execution_binding) != expected or any(type(v) is not str or not v or v.strip() != v for v in execution_binding.values()):
            raise CertificationStop('BINDING_MISMATCH')
        for key in expected & required:
            if claims[key] != execution_binding[key]:
                raise CertificationStop('BINDING_MISMATCH')
        canonical = Path.home() / 'Library/Application Support/BotApp/follow60-lock-v3.1/a0-candidate-admissions'
        directory = Path(state_directory)
        if directory != canonical or any(p.is_symlink() for p in (directory, *directory.parents)):
            raise CertificationStop('PERMIT_INVALID')
        now = datetime.now(timezone.utc)
        if not _utc(claims['certification_not_before']) <= now < _utc(claims['certification_expires_at']):
            raise CertificationStop('PERMIT_INVALID')
        binding = dict(execution_binding, permit_id=permit_id, permit_hash=hashlib.sha256(_bytes(claims)).hexdigest())
        _canonical_authority(binding)
        # Root creation belongs to the governed launcher, never implicit issuance recovery.
        store = _PermitStore(directory, binding)
        _SESSION = (claims, binding, store)
        validate_execution_context()
    except CertificationStop:
        raise
    except Exception as exc:
        raise CertificationStop('PERMIT_INVALID') from exc


def is_enabled():
    return _REQUESTED


def validate_execution_context():
    if not _REQUESTED:
        return
    if _SESSION is None:
        raise CertificationStop('PERMIT_INVALID')
    claims, binding, store = _SESSION
    now = datetime.now(timezone.utc)
    if not _utc(claims['certification_not_before']) <= now < _utc(claims['certification_expires_at']):
        raise CertificationStop('PERMIT_INVALID')
    if store._read()['state'] == 'TERMINAL':
        raise CertificationStop('PERMIT_ALREADY_TERMINAL')
    try:
        _canonical_authority(binding)
    except CertificationStop:
        raise
    except Exception as exc:
        raise CertificationStop('BINDING_MISMATCH') from exc


def before_candidate_transport(*, run_id, request_id, account_id, source_ct, candidate,
                               normalize_handle, valid_handle):
    if not _REQUESTED:
        return 'DISABLED'
    try:
        return _before_candidate_transport(
            run_id=run_id, request_id=request_id, account_id=account_id,
            source_ct=source_ct, candidate=candidate,
            normalize_handle=normalize_handle, valid_handle=valid_handle)
    except CertificationStop:
        raise
    except Exception as exc:
        # Storage/read/validation failures must bypass candidate-local recovery.
        raise CertificationStop('PERMIT_INVALID') from exc


def _before_candidate_transport(*, run_id, request_id, account_id, source_ct, candidate,
                                normalize_handle, valid_handle):
    if not _REQUESTED:
        return 'DISABLED'
    validate_execution_context()
    _, binding, store = _SESSION
    if (run_id, request_id, account_id) != (binding['run_id'], binding['request_id'], binding['account_id']):
        store.terminal('BINDING_MISMATCH')
        raise CertificationStop('BINDING_MISMATCH')
    # Existing runner's normalization/validation; no UI parsing or new reader.
    candidate = candidate if isinstance(candidate, dict) else {}
    username = candidate.get('username')
    hint = candidate.get('resolved_username_hint')
    key = None
    if (type(username) is str and type(source_ct) is str and valid_handle(username)
            and valid_handle(source_ct) and not candidate.get('username_pending_profile_read')
            and not candidate.get('visual_candidate_id')
            and (not hint or (type(hint) is str and normalize_handle(hint) == normalize_handle(username)))):
        key = (run_id, account_id, normalize_handle(source_ct), normalize_handle(username))
    if key is None:
        store.terminal(IDENTITY_UNPROVED)
        raise CertificationStop(IDENTITY_UNPROVED)
    return store.admit(key)


def mark_terminal(reason):
    if _SESSION is not None:
        store = _SESSION[2]
        try:
            store.terminal(reason)
        finally:
            store.close()
