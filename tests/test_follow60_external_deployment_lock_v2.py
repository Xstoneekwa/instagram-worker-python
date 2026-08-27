"""A–L use real Git/signatures; only root/immutable OS metadata is modelled.

No runtime service, network, device or production lock is accessed. Separate
root-only installer smoke verifies actual macOS ownership/flags in a temp tree.
"""
import json
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace
import unittest
from unittest import mock

from follow60_candidate_identity_v2 import git, commit_sha
from follow60_deployment_gate_v1 import verify_deployment_candidate
from follow60_write_lock_v3_1 import verify_physical_write_lock, load_protected_paths, protected_directories
import follow60_external_deployment_lock_v2 as lock
from tests import test_follow60_deployment_gate_v1 as fixtures


class ExternalPhysicalLockV2Tests(unittest.TestCase):
    def setUp(self):
        helper = fixtures.Follow60DeploymentGateV1Tests()
        temporary, self.root, _, self.key, public, _, _ = helper._repo()
        self.temp = temporary
        self.root = self.root.resolve()
        self.addCleanup(temporary.cleanup)
        self.store = self.root.parent / 'external-store'
        self.docs = self.root / 'docs/governance'
        self.docs.mkdir(parents=True)
        shutil.copyfile(public, self.docs / 'FOLLOW60_MAINLINE_LOCK_V3_1_PUBLIC_KEY.pem')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'synthetic trusted key')
        self.sha = commit_sha(self.root, 'HEAD')
        self.manifest = self.docs / 'FOLLOW60_MAINLINE_LOCK_V3.json'
        self.manifest.write_text(json.dumps(helper._manifest(self.root, self.sha)))
        (self.docs / 'FOLLOW60_MAINLINE_LOCK_V3.sig').write_bytes(self.key.sign(self.manifest.read_bytes()))
        self.expected = lock.exact_admission(self.root)
        self.owner = {'pid': 1234, 'process_start_and_uid': 'synthetic-start 0', 'boot_session': 'synthetic-boot'}
        for patcher in (
            mock.patch.object(lock, 'STORE', self.store),
            mock.patch.object(lock, '_root_only'),
            mock.patch.object(lock, '_trusted'),
            mock.patch.object(lock.os, 'chflags'),
            mock.patch.object(lock, 'owner_identity', return_value=self.owner),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        lock.initialize_store(self.root)
        self.protected = load_protected_paths(self.manifest)
        self.dirs = protected_directories(self.protected)
        for name in self.protected:
            (self.root / name).chmod(0o444)
        for name in self.dirs:
            (self.root / name).chmod(0o555)
        self.root.chmod(0o555)
        self.seal = lock.seal_path(self.root)
        lock.atomic_record(self.seal, lock.seal_payload(self.root, self.expected, self.protected, self.dirs))
        def verify(root):
            result = verify_physical_write_lock(root, self.manifest, require_root_owner=False, require_immutable=False)
            if not result['ok']:
                raise lock.LockFailure('physical_seal:' + str(result))
            return result
        patcher = mock.patch.object(lock, 'verify_seal', side_effect=verify)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.restore)

    def restore(self):
        self.root.chmod(0o755)
        for name in self.dirs:
            (self.root / name).chmod(0o755)
        for name in self.protected:
            (self.root / name).chmod(0o644)

    def rewrite(self, path, value):
        path.chmod(0o644)
        path.write_text(json.dumps(value))
        path.chmod(0o444)

    def test_A_clean_candidate_valid_external_lock_passes(self):
        with lock.DeploymentTransaction(self.root) as transaction:
            self.assertTrue(transaction.recheck()['ok'])
            self.assertEqual(transaction.expected, self.expected)

    def test_B_missing_lock_fails(self):
        with self.assertRaisesRegex(lock.LockFailure, 'not_acquired'):
            lock.DeploymentTransaction(self.root).recheck()
        self.seal.unlink()
        with self.assertRaises(Exception):
            with lock.DeploymentTransaction(self.root):
                self.fail('missing physical seal admitted')

    def test_C_wrong_candidate_lock_fails(self):
        value = json.loads(self.seal.read_text())
        value['candidate_commit_sha'] = 'f' * 40
        self.rewrite(self.seal, value)
        with self.assertRaisesRegex(lock.LockFailure, 'candidate_or_manifest_mismatch'):
            with lock.DeploymentTransaction(self.root):
                self.fail('wrong candidate admitted')

    def test_D_tampered_lock_fails(self):
        with lock.DeploymentTransaction(self.root) as transaction:
            value = dict(transaction.payload, manifest_sha256='f' * 64)
            self.rewrite(self.store / 'transactions/active.json', value)
            with self.assertRaisesRegex(lock.LockFailure, 'tampered'):
                transaction.recheck()

    def test_E_dirty_source_worktree_fails(self):
        path = self.root / 'protected.py'
        path.chmod(0o644)
        path.write_text('VALUE = 2\n')
        with self.assertRaisesRegex(lock.LockFailure, 'dirty'):
            with lock.DeploymentTransaction(self.root):
                self.fail('dirty candidate admitted')

    def test_F_untracked_protected_source_fails(self):
        self.root.chmod(0o755)
        (self.root / 'injected_runtime.py').write_text('pass\n')
        with self.assertRaisesRegex(lock.LockFailure, 'dirty'):
            with lock.DeploymentTransaction(self.root):
                self.fail('untracked source admitted')

    def test_G_candidate_changed_after_lock_fails(self):
        with lock.DeploymentTransaction(self.root) as transaction:
            path = self.root / 'protected.py'
            path.chmod(0o644)
            path.write_text('VALUE = 9\n')
            with self.assertRaisesRegex(lock.LockFailure, 'dirty'):
                transaction.recheck()

    def test_H_stale_lock_requires_explicit_owner_proof_and_preservation(self):
        with lock.DeploymentTransaction(self.root):
            pass  # A failed/interrupted promotion intentionally leaves evidence.
        active = self.store / 'transactions/active.json'
        before = active.read_bytes()
        sha = lock.digest(active)
        with self.assertRaisesRegex(lock.LockFailure, 'requires_explicit_recovery'):
            with lock.DeploymentTransaction(self.root):
                pass
        with self.assertRaisesRegex(lock.LockFailure, 'still_alive'):
            lock.recover_stale(self.root, sha)
        with mock.patch.object(lock, 'owner_identity', return_value=None):
            archived = lock.recover_stale(self.root, sha)
        self.assertEqual(archived.read_bytes(), before)
        self.assertFalse(active.exists())
        with lock.DeploymentTransaction(self.root) as transaction:
            self.assertTrue(transaction.recheck()['ok'])

    def test_I_lock_creation_does_not_dirty_candidate(self):
        before = git(self.root, 'status', '--porcelain')
        with lock.DeploymentTransaction(self.root):
            self.assertEqual(git(self.root, 'status', '--porcelain'), before)
            self.assertFalse((self.root / '.follow60-write-lock-v3.1.json').exists())
            self.assertNotIn(self.root, self.seal.parents)

    def test_J_arbitrary_json_and_dotfile_still_fail(self):
        self.root.chmod(0o755)
        for name in ('extra.json', '.other', '.follow60-write-lock-v3.1.json'):
            with self.subTest(name=name):
                path = self.root / name
                path.write_text('{}')
                self.assertEqual(verify_deployment_candidate(self.root)['reason'], 'candidate_worktree_or_index_dirty')
                path.unlink()

    def test_K_old_protocol_failure_is_reproduced(self):
        self.assertTrue(verify_deployment_candidate(self.root)['ok'])
        self.root.chmod(0o755)
        (self.root / '.follow60-write-lock-v3.1.json').write_text(self.seal.read_text())
        self.assertEqual(verify_deployment_candidate(self.root)['reason'], 'candidate_worktree_or_index_dirty')

    def test_L_exact_activation_preflight_passes(self):
        with lock.DeploymentTransaction(self.root) as transaction:
            result = transaction.recheck()
            self.assertEqual(result['status'], 'EXTERNAL_DEPLOYMENT_PREFLIGHT_PASS')
            self.assertEqual(result['physical_write_lock'], 'PASS')
            self.assertTrue(result['toctou_recheck_after_lock'])
            self.assertEqual(result['manifest_sha256'], lock.digest(self.manifest))

    def test_missing_active_record_after_acquisition_fails(self):
        with lock.DeploymentTransaction(self.root) as transaction:
            (self.store / 'transactions/active.json').unlink()
            with self.assertRaises(Exception):
                transaction.recheck()

    def test_wrong_manifest_seal_fails(self):
        value = json.loads(self.seal.read_text())
        value['manifest_sha256'] = '0' * 64
        self.rewrite(self.seal, value)
        with self.assertRaisesRegex(lock.LockFailure, 'candidate_or_manifest_mismatch'):
            lock.verify_seal(self.root)

    def test_signature_changed_after_acquisition_fails(self):
        with lock.DeploymentTransaction(self.root) as transaction:
            (self.docs / 'FOLLOW60_MAINLINE_LOCK_V3.sig').write_bytes(b'X' * 64)
            with self.assertRaises(lock.LockFailure):
                transaction.recheck()

    def test_owner_identity_change_after_acquisition_fails(self):
        with lock.DeploymentTransaction(self.root) as transaction:
            with mock.patch.object(lock, 'owner_identity', return_value=dict(self.owner, boot_session='other')):
                with self.assertRaisesRegex(lock.LockFailure, 'owner_mismatch'):
                    transaction.recheck()

    def test_unknown_owner_liveness_never_recovers(self):
        with lock.DeploymentTransaction(self.root):
            pass
        sha = lock.digest(self.store / 'transactions/active.json')
        with mock.patch.object(lock, 'owner_identity', side_effect=lock.LockFailure('owner_identity_unknown')):
            with self.assertRaisesRegex(lock.LockFailure, 'unknown'):
                lock.recover_stale(self.root, sha)
        self.assertTrue((self.store / 'transactions/active.json').exists())

    def test_source_mode_writability_is_not_accepted_as_lock(self):
        (self.root / 'protected.py').chmod(0o644)
        with self.assertRaisesRegex(lock.LockFailure, 'write_bit_present'):
            with lock.DeploymentTransaction(self.root):
                self.fail('writable protected source admitted')

    def test_native_switch_without_seal_cannot_reach_zero_or_mutation(self):
        import phonefarm_runtime_control as ctl
        self.seal.unlink()
        valid = ctl.RuntimeRoot(True, 'valid', str(self.root), str(self.root), self.sha)
        with mock.patch.object(ctl, 'resolve_runtime_root', return_value=valid), \
             mock.patch.object(ctl, '_switch_release_locked') as mutate, \
             mock.patch.object(ctl, 'deployment_zero_gate') as zero:
            result = ctl.switch_release(str(self.root))
        self.assertFalse(result['ok'])
        mutate.assert_not_called()
        zero.assert_not_called()

    def test_same_tree_new_commit_after_lock_is_rejected(self):
        with lock.DeploymentTransaction(self.root) as transaction:
            git(self.root, 'commit', '--allow-empty', '-qm', 'new identity, same source tree')
            with self.assertRaisesRegex(lock.LockFailure, 'manifest_certified_sha_mismatch'):
                transaction.recheck()

    def test_concurrent_guard_is_exclusive(self):
        with lock.storage_guard(self.root):
            with self.assertRaisesRegex(lock.LockFailure, 'already_owned'):
                with lock.storage_guard(self.root):
                    self.fail('concurrent guard acquired')

    def test_atomic_record_cannot_overwrite_existing_lock(self):
        with lock.DeploymentTransaction(self.root):
            active = self.store / 'transactions/active.json'
            before = active.read_bytes()
            with self.assertRaises(FileExistsError):
                lock.atomic_record(active, {'forged': True})
            self.assertEqual(active.read_bytes(), before)


class ExternalLockMetadataTests(unittest.TestCase):
    def test_root_required_without_any_override(self):
        with mock.patch.object(lock.os, 'geteuid', return_value=501):
            with self.assertRaisesRegex(lock.LockFailure, 'root_required'):
                lock._root_only()

    def test_trusted_record_checks_owner_mode_type_links_and_flags(self):
        good = {'st_mode': stat.S_IFREG | 0o444, 'st_uid': 0, 'st_nlink': 1, 'st_flags': lock.IMMUTABLE}
        path = mock.Mock(name='record');path.name='record.json'
        path.lstat.return_value=SimpleNamespace(**good)
        lock._trusted(path)
        for delta in ({'st_uid':501}, {'st_mode':stat.S_IFREG|0o644},
                      {'st_mode':stat.S_IFLNK|0o444}, {'st_nlink':2}, {'st_flags':0}):
            with self.subTest(delta=delta):
                path.lstat.return_value=SimpleNamespace(**dict(good,**delta))
                with self.assertRaises(lock.LockFailure): lock._trusted(path)

    def test_external_storage_must_not_overlap_candidate(self):
        with mock.patch.object(lock, 'STORE', Path('/private/tmp/candidate/lock')):
            with self.assertRaisesRegex(lock.LockFailure, 'overlaps'):
                lock._outside(Path('/private/tmp/candidate'))

    def test_owner_probe_unknown_permission_is_not_dead(self):
        with mock.patch.object(lock.os, 'kill', side_effect=PermissionError):
            with self.assertRaisesRegex(lock.LockFailure, 'unknown'):
                lock.owner_identity(1234)

    def test_owner_probe_esrch_is_proved_dead(self):
        with mock.patch.object(lock.os, 'kill', side_effect=ProcessLookupError):
            self.assertIsNone(lock.owner_identity(1234))

    def test_partial_or_forged_lease_schema_is_rejected(self):
        with self.assertRaisesRegex(lock.LockFailure, 'schema_invalid'):
            lock.validate_lease({'owner':{'pid':1234}})


if __name__ == '__main__':
    unittest.main()
