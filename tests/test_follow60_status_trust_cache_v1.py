"""Real Git/Ed25519; isolated filesystem; root/immutable metadata explicitly modelled.

No production store, services, devices, credentials or network are accessed.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest import mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from follow60_candidate_identity_v2 import git, commit_sha
from follow60_deployment_gate_v1 import verify_deployment_candidate
from follow60_lock_v3 import canonical_json, git_file_entries, transitive_import_graph
from follow60_write_lock_v3_1 import load_protected_paths, protected_directories, _verify_physical_metadata
import follow60_external_deployment_lock_v2 as physical
import follow60_status_trust_cache_v1 as cache
from tests import test_follow60_deployment_gate_v1 as deployment_fixtures

SOURCE = Path(__file__).resolve().parents[1]


class StatusTrustCacheTests(unittest.TestCase):
    def setUp(self):
        helper = deployment_fixtures.Follow60DeploymentGateV1Tests()
        temporary, root, _, self.key, public, _, _ = helper._repo()
        self.addCleanup(temporary.cleanup)
        self.root = root.resolve()
        self.evidence = self.root.parent
        self.docs = self.root / 'docs/governance'
        self.docs.mkdir(parents=True)
        shutil.copyfile(public, self.docs / cache.PUBLIC_KEY_RELATIVE.name)
        (self.root / '.gitignore').write_text('optional_plugin.py\n')
        (self.root / 'protected.py').write_text('try:\n    import optional_plugin\nexcept ImportError:\n    pass\nVALUE = 1\n')
        for name in cache.PROTOCOL_FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, target)
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'synthetic cache protocol')
        self.sha = commit_sha(self.root, 'HEAD')
        self.payload = helper._manifest(self.root, self.sha)
        entries = self.payload['protected_entries']
        for name, entry in git_file_entries(self.root, cache.PROTOCOL_FILES).items():
            entries[name] = dict(entry, role='protocol', lock_version='3.1.0')
        graph = transitive_import_graph(self.root, entries)
        self.payload.update(protected_scope_sha256=hashlib.sha256(canonical_json(entries)).hexdigest(),
                            import_graph=graph, import_graph_sha256=hashlib.sha256(canonical_json(graph)).hexdigest())
        self.manifest = self.root / cache.MANIFEST_RELATIVE
        self.signature = self.root / cache.SIGNATURE_RELATIVE
        self.manifest.write_text(json.dumps(self.payload, sort_keys=True))
        self.signature.write_bytes(self.key.sign(self.manifest.read_bytes()))
        self.current = self.evidence / 'current'
        self.current.symlink_to(self.root)
        self.registry = self.evidence / 'registry.json'
        self.registry_payload = {'schema': 'PHONE_FARM_CANONICAL_DELTA_REGISTRY_V1', 'components': {
            'worker': {'production': {'sha': self.sha, 'runtime_root': str(self.root), 'verification_state': 'VERIFIED'}}}}
        self.registry.write_text(json.dumps(self.registry_payload))
        self.store = self.evidence / 'store'
        for patcher in (
            mock.patch.object(cache, 'CURRENT_LINK', self.current),
            mock.patch.object(cache, 'RELEASES', self.evidence),
            mock.patch.object(cache, 'REGISTRY', self.registry),
            mock.patch.object(physical, 'STORE', self.store),
            mock.patch.object(physical, '_root_only'),
            mock.patch.object(physical, '_trusted'),
            mock.patch.object(physical.os, 'chflags'),
            mock.patch.object(cache, '_verify_physical_metadata', side_effect=lambda *a, **kw:
                _verify_physical_metadata(*a, require_root_owner=False, require_immutable=False)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        physical.initialize_store(self.root)
        protected = load_protected_paths(self.manifest)
        dirs = protected_directories(protected)
        expected = physical.exact_admission(self.root)
        record = {'schema': 'FOLLOW60_PHYSICAL_WRITE_LOCK_V3_1', 'lock_version': '3.1.0',
                  'lock_state': 'LOCKED', 'protocol_version': physical.PROTOCOL_VERSION,
                  **expected, 'verified_revision': self.sha,
                  'protected_paths': list(protected), 'protected_directories': dirs}
        physical.atomic_record(physical.seal_path(self.root), record)
        for name in protected:
            (self.root / name).chmod(0o444)
        for name in dirs:
            (self.root / name).chmod(0o555)
        self.root.chmod(0o555)
        self.addCleanup(self.restore)
        self.installed = cache.install_status_attestation(self.root)
        self.cached_file = Path(self.installed['path'])

    def restore(self):
        for directory, dirs, files in os.walk(self.root):
            Path(directory).chmod(0o755)
            for name in files:
                candidate = Path(directory) / name
                if not candidate.is_symlink():
                    candidate.chmod(0o644)

    def verify(self):
        return cache.verify_status_trust(self.root)

    def rewrite(self, path, raw):
        mode = path.stat().st_mode & 0o777
        path.chmod(0o644)
        path.write_bytes(raw)
        path.chmod(mode)

    def test_warm_hit_rechecks_signature_but_not_full_gate(self):
        with mock.patch.object(cache, 'verify_deployment_candidate', side_effect=AssertionError('full gate on hit')), \
             mock.patch.object(cache, 'verify_detached_signature', wraps=cache.verify_detached_signature) as signature:
            result = self.verify()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['cache'], 'HIT')
        self.assertEqual(signature.call_count, 2)
        self.assertFalse(result['deployment_authorized'])
        self.assertFalse({'pid', 'processRunning', 'heartbeat', 'scheduler', 'service_state'} & set(result))

    def test_manifest_tamper_after_cache_fails(self):
        self.rewrite(self.manifest, self.manifest.read_bytes() + b' ')
        self.assertFalse(self.verify()['ok'])

    def test_active_sha_changed_fails_even_same_tree(self):
        git(self.root, 'commit', '--allow-empty', '-qm', 'different commit')
        self.assertFalse(self.verify()['ok'])

    def test_registry_sha_changed_fails(self):
        self.registry_payload['components']['worker']['production']['sha'] = 'f' * 40
        self.registry.write_text(json.dumps(self.registry_payload))
        self.assertFalse(self.verify()['ok'])

    def test_registry_bytes_changed_invalidates_hit(self):
        self.registry.write_text(json.dumps(self.registry_payload, indent=2))
        result = self.verify()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['cache'], 'MISS')

    def test_signature_changed_fails(self):
        self.rewrite(self.signature, Ed25519PrivateKey.generate().sign(self.manifest.read_bytes()))
        self.assertFalse(self.verify()['ok'])

    def test_runtime_root_swap_fails(self):
        alternate = self.evidence / 'other'
        alternate.mkdir()
        self.current.unlink()
        self.current.symlink_to(alternate)
        self.assertFalse(self.verify()['ok'])

    def test_old_cache_copied_under_new_key_does_not_hit(self):
        self.registry.write_text(json.dumps(self.registry_payload, indent=2))
        new_path = cache._record_path(cache._snapshot(self.root))
        shutil.copyfile(self.cached_file, new_path)
        result = self.verify()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['cache'], 'MISS')

    def test_corrupt_cache_requires_full_verification(self):
        self.rewrite(self.cached_file, b'{broken')
        with mock.patch.object(cache, 'verify_deployment_candidate', wraps=verify_deployment_candidate) as full:
            result = self.verify()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['cache'], 'MISS')
        self.assertEqual(full.call_count, 1)

    def test_missing_cache_requires_full_verification_without_write(self):
        self.cached_file.unlink()
        result = self.verify()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['cache'], 'MISS')
        self.assertFalse(self.cached_file.exists())

    def test_corrupt_cache_and_failed_full_gate_never_trusts(self):
        self.rewrite(self.cached_file, b'{}')
        with mock.patch.object(cache, 'verify_deployment_candidate', return_value={'ok': False, 'reason': 'failed'}):
            self.assertFalse(self.verify()['ok'])

    def test_same_size_source_tamper_with_restored_mtime_fails(self):
        source = self.root / 'protected.py'
        info = source.stat()
        self.rewrite(source, source.read_bytes().replace(b'VALUE = 1', b'VALUE = 2'))
        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertFalse(self.verify()['ok'])

    def test_ignored_new_import_resolution_invalidates_and_fails(self):
        self.root.chmod(0o755)
        (self.root / 'optional_plugin.py').write_text('VALUE=9\n')
        self.root.chmod(0o555)
        result = self.verify()
        self.assertFalse(result['ok'], result)
        self.assertIn('full_verification_failed', result['reason'])

    def test_physical_write_bit_fails(self):
        (self.root / 'protected.py').chmod(0o644)
        self.assertFalse(self.verify()['ok'])

    def test_root_owner_flags_not_bypassed_by_cache(self):
        with mock.patch.object(cache, '_verify_physical_metadata', wraps=_verify_physical_metadata):
            self.assertFalse(self.verify()['ok'])

    def test_cache_metadata_rejection_falls_back_not_hit(self):
        original = physical.read_record
        def read(path):
            if path == self.cached_file:
                raise physical.LockFailure('untrusted_cache_metadata')
            return original(path)
        with mock.patch.object(physical, 'read_record', side_effect=read):
            result = self.verify()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['cache'], 'MISS')

    def test_protocol_version_change_invalidates(self):
        with mock.patch.object(cache, 'SCHEMA', cache.SCHEMA + '_next'):
            result = self.verify()
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['cache'], 'MISS')

    def test_snapshot_race_fails(self):
        original = cache._snapshot
        count = 0
        def snapshot(root):
            nonlocal count
            value = original(root)
            count += 1
            if count == 2:
                value['registry_bytes_sha256'] = '0' * 64
            return value
        with mock.patch.object(cache, '_snapshot', side_effect=snapshot):
            self.assertFalse(self.verify()['ok'])

    def test_full_deployment_gate_does_not_use_status_cache(self):
        with mock.patch.object(cache, 'verify_status_trust', side_effect=AssertionError('status used for activation')):
            self.assertTrue(verify_deployment_candidate(self.root)['ok'])

    def test_installer_is_exclusive_and_no_liveness_stored(self):
        with self.assertRaises(FileExistsError):
            cache.install_status_attestation(self.root)
        payload = json.loads(self.cached_file.read_bytes())
        self.assertEqual(set(payload), {'schema', 'key', 'identity', 'full_verification'})

    def test_executing_protocol_mismatch_fails(self):
        with mock.patch.object(cache, 'PROTOCOL_FILES', cache.PROTOCOL_FILES + ('not_signed.py',)):
            self.assertFalse(self.verify()['ok'])

    def test_wrapper_only_status_uses_cache(self):
        script = (SOURCE / 'scripts/run_control_dispatcher_service.sh').read_text()
        block = script[script.index('if [[ "${1:-start}" == "status" ]]'):]
        block = block.split('\nfi', 1)[0]
        self.assertIn('verify-follow60-status-trust-v1.py', block.split('else')[0])
        self.assertIn('verify-follow60-deployment-candidate-v1.py', block.split('else')[1])


if __name__ == '__main__':
    unittest.main()
