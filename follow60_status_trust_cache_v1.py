"""Immutable diagnostic attestations; never a deployment or liveness gate.

Only the root-only installer can publish in the existing guarded external seal
store. Readers recheck current bytes, namespace, signature, registry and physical
metadata twice. No TTL, env override, caller-supplied verdict or runtime state.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys

from follow60_candidate_identity_v2 import commit_sha, commit_tree, require_clean
from follow60_deployment_gate_v1 import (
    verify_deployment_candidate, MANIFEST_RELATIVE, SIGNATURE_RELATIVE, PUBLIC_KEY_RELATIVE,
)
from follow60_lock_v3 import canonical_json, verify_detached_signature
import follow60_external_deployment_lock_v2 as physical
from follow60_write_lock_v3_1 import load_protected_paths, _verify_physical_metadata

SCHEMA = 'FOLLOW60_STATUS_TRUST_CACHE_V1'
CURRENT_LINK = Path('/Users/admin/phonefarm-worker-current')
RELEASES = Path('/Users/admin/phonefarm-worker-releases')
REGISTRY = Path('/Users/admin/Projects/phonefarm-golden-resume-plan-contract-v1/docs/governance/PRODUCTION_CANONICAL_DELTA_REGISTRY_V1.json')
PROTOCOL_FILES = (
    'follow60_status_trust_cache_v1.py', 'follow60_lock_v3.py',
    'follow60_candidate_identity_v2.py', 'follow60_deployment_gate_v1.py',
    'follow60_external_deployment_lock_v2.py', 'follow60_write_lock_v3_1.py',
    'scripts/verify-follow60-status-trust-v1.py',
    'scripts/install-follow60-status-trust-cache-v1.py',
    'scripts/run_control_dispatcher_service.sh',
)


class TrustFailure(ValueError):
    pass


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def trust_key(identity):
    """Cache serialization only; canonical Git diff remains the V2 implementation."""
    return _hash(canonical_json(identity))


def _regular_bytes(path):
    """Read one non-symlink inode and fail on observed replacement/write races."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise TrustFailure('trust_input_not_regular')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read()
        after = os.fstat(fd)
    finally:
        os.close(fd)
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, k) != getattr(after, k) for k in fields):
        raise TrustFailure('trust_input_changed_while_reading')
    final = path.lstat()
    if final.st_dev != after.st_dev or final.st_ino != after.st_ino:
        raise TrustFailure('trust_input_replaced_while_reading')
    return raw


def _source_namespace(root, protected):
    """Bind local .py resolution including ignored modules and directory types.

    Dot directories and __pycache__ cannot be selected by the existing source
    import resolver. Everything else is inventoried without following symlinks.
    Symlinked source directories conservatively disable cache admission.
    """
    entries = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__')
        for name in dirs:
            candidate = Path(directory) / name
            info = candidate.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise TrustFailure('source_namespace_symlink_directory')
            entries[str(candidate.relative_to(root)) + '/'] = {'mode': stat.S_IMODE(info.st_mode)}
        for name in sorted(files):
            candidate = Path(directory) / name
            relative = str(candidate.relative_to(root))
            if name.endswith('.py') or relative in protected:
                entries[relative] = {'sha256': _hash(_regular_bytes(candidate)),
                                     'mode': stat.S_IMODE(candidate.lstat().st_mode)}
    for relative, expected in protected.items():
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise TrustFailure('protected_path_invalid')
        candidate = root / relative
        raw = _regular_bytes(candidate)
        actual_mode = '100755' if candidate.lstat().st_mode & 0o111 else '100644'
        if _hash(raw) != expected['sha256'] or actual_mode != expected['mode'] or len(raw) != expected['size']:
            raise TrustFailure('protected_bytes_or_mode_mismatch')
        entries[relative] = {'sha256': _hash(raw), 'mode': stat.S_IMODE(candidate.lstat().st_mode)}
    return _hash(canonical_json(entries))


def _snapshot(target):
    target = Path(target).resolve()
    if not CURRENT_LINK.is_symlink() or CURRENT_LINK.resolve() != target:
        raise TrustFailure('status_target_not_current_release')
    if target.parent != RELEASES.resolve():
        raise TrustFailure('status_target_not_canonical_release')
    root_info = target.lstat()
    manifest_raw = _regular_bytes(target / MANIFEST_RELATIVE)
    signature_raw = _regular_bytes(target / SIGNATURE_RELATIVE)
    public_raw = _regular_bytes(target / PUBLIC_KEY_RELATIVE)
    signed, reason = verify_detached_signature(target / MANIFEST_RELATIVE,
                                              target / SIGNATURE_RELATIVE,
                                              target / PUBLIC_KEY_RELATIVE)
    if not signed:
        raise TrustFailure(reason)
    manifest = json.loads(manifest_raw)
    sha = commit_sha(target, 'HEAD')
    tree = commit_tree(target, sha)
    certified = manifest['candidate_identity']
    if (certified['candidate_commit_sha'] != sha or certified['candidate_tree_sha'] != tree
            or manifest['certified_candidate_sha'] != sha):
        raise TrustFailure('status_signed_candidate_mismatch')
    require_clean(target, installed_certification=True)

    registry_raw = _regular_bytes(REGISTRY)
    registry = json.loads(registry_raw)
    production = registry['components']['worker']['production']
    if (registry.get('schema') != 'PHONE_FARM_CANONICAL_DELTA_REGISTRY_V1'
            or production.get('verification_state') != 'VERIFIED'
            or production.get('sha') != sha or production.get('runtime_root') != str(target)):
        raise TrustFailure('status_registry_active_mismatch')

    physical._storage(target)
    seal_path = physical.seal_path(target)
    seal = physical.read_record(seal_path)
    admission = {name: certified[name] for name in (
        'candidate_commit_sha', 'candidate_tree_sha', 'canonical_diff_sha256')}
    admission.update(manifest_sha256=_hash(manifest_raw), signature_sha256=_hash(signature_raw),
                     candidate_root=str(target))
    metadata = _verify_physical_metadata(target, seal_path, seal,
                                        load_protected_paths(target / MANIFEST_RELATIVE), admission)
    if not metadata['ok']:
        raise TrustFailure('status_physical_lock_invalid')
    protected = manifest['protected_entries']
    if set(PROTOCOL_FILES) - set(protected):
        raise TrustFailure('status_verifier_not_in_signed_scope')
    protocol_hashes = {}
    for name in PROTOCOL_FILES:
        actual = _hash(_regular_bytes(Path(__file__).resolve().parent / name))
        if actual != protected[name]['sha256']:
            raise TrustFailure('status_executing_verifier_mismatch')
        protocol_hashes[name] = actual
    namespace = _source_namespace(target, protected)
    identity = {
        'schema': SCHEMA, 'active_release_commit_sha': sha, 'candidate_tree_sha': tree,
        'manifest_sha256': _hash(manifest_raw), 'signature_sha256': _hash(signature_raw),
        'public_key_sha256': _hash(public_raw), 'registry_active_sha': production['sha'],
        'registry_bytes_sha256': _hash(registry_raw),
        'registry_verification_state': production['verification_state'],
        'runtime_root': {'resolved_path': str(target), 'st_dev': root_info.st_dev,
                         'st_ino': root_info.st_ino, 'active_link_target': os.readlink(CURRENT_LINK)},
        'protected_scope_sha256': manifest['protected_scope_sha256'],
        'import_graph_sha256': manifest['import_graph_sha256'],
        'source_namespace_sha256': namespace,
        'physical_seal_sha256': physical.digest(seal_path),
        'verifier_protocol_sha256': _hash(canonical_json(protocol_hashes)),
        'physical_lock_protocol_version': physical.PROTOCOL_VERSION,
        'python_implementation_and_version': [platform.python_implementation(), list(sys.version_info[:3])],
    }
    # Re-read byte identities after the independent signature verifier's reads.
    for file, raw in ((MANIFEST_RELATIVE, manifest_raw), (SIGNATURE_RELATIVE, signature_raw),
                      (PUBLIC_KEY_RELATIVE, public_raw)):
        if _regular_bytes(target / file) != raw:
            raise TrustFailure('status_certification_changed_during_snapshot')
    if _regular_bytes(REGISTRY) != registry_raw or CURRENT_LINK.resolve() != target:
        raise TrustFailure('status_identity_changed_during_snapshot')
    return identity


def _record_path(identity):
    return physical.STORE / 'seals' / ('status-trust-' + trust_key(identity) + '.json')


def _result(identity, cache):
    return {'ok': True, 'status': 'FOLLOW60_STATUS_TRUST_PASS', 'cache': cache,
            'diagnostic_only': True, 'deployment_authorized': False,
            'candidate_sha': identity['active_release_commit_sha'],
            'manifest_sha256': identity['manifest_sha256'], 'trust_cache_key': trust_key(identity),
            'signature': 'PASS', 'physical_lock': 'PASS', 'registry': 'PASS'}


def verify_status_trust(target):
    """Read-only: a hit reuses immutable proof, never live process fields."""
    try:
        before = _snapshot(target)
        cache_state = 'MISS'
        try:
            record = physical.read_record(_record_path(before))
            if (set(record) != {'schema', 'identity', 'key', 'full_verification'}
                    or record['schema'] != SCHEMA or record['identity'] != before
                    or record['key'] != trust_key(before) or record['full_verification'] != 'PASS'):
                raise TrustFailure('status_cache_record_mismatch')
            cache_state = 'HIT'
        except (OSError, ValueError, KeyError, TypeError, physical.LockFailure):
            # Missing/corrupt data cannot become trust. No cache is written here.
            full = verify_deployment_candidate(Path(target))
            if not full.get('ok'):
                raise TrustFailure('status_full_verification_failed:' + str(full.get('reason')))
        after = _snapshot(target)
        if before != after:
            raise TrustFailure('status_identity_changed_during_verification')
        return _result(after, cache_state)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError,
            physical.LockFailure) as exc:
        return {'ok': False, 'status': 'FOLLOW60_STATUS_TRUST_FAILED',
                'reason': str(exc) if isinstance(exc, TrustFailure) else 'status_trust_input_invalid',
                'error_type': type(exc).__name__, 'diagnostic_only': True,
                'deployment_authorized': False}


def install_status_attestation(target):
    """Root-only, explicit maintenance command. Does not switch or start anything."""
    physical._root_only()
    with physical.storage_guard(Path(target)):
        before = _snapshot(target)
        full = verify_deployment_candidate(Path(target))
        if not full.get('ok'):
            raise TrustFailure('status_attestation_full_verification_failed')
        after = _snapshot(target)
        if before != after:
            raise TrustFailure('status_identity_changed_before_publication')
        record = {'schema': SCHEMA, 'identity': after, 'key': trust_key(after), 'full_verification': 'PASS'}
        target_path = _record_path(after)
        physical.atomic_record(target_path, record)
        return {'ok': True, 'path': str(target_path), 'key': record['key'], 'activation': False}
