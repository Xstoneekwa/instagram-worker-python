import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from instagram_version_lock_state_v1 import LockStateError, VersionLockStore


class VersionLockStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.release = self.root / "sealed"
        self.release.mkdir()
        self.store = self.make_store()

    def make_store(self, serial="device", package="com.instagram.fixture"):
        return VersionLockStore(run_dir=self.root / "runtime" / "run", release_root=self.release,
                                host="host", serial=serial, package=package)

    def baseline(self):
        return {**self.store.read(), "device_serial": "device", "package": "com.instagram.fixture",
                "authorized_version_name": "123", "authorized_version_code": 123, "lock_applied": True}

    def test_new_writes_external_and_isolated_per_device_clone(self):
        self.store.write(self.baseline())
        self.assertEqual(self.store.read()["authorized_version_code"], 123)
        self.assertEqual(self.make_store(serial="other").read()["_storage_generation"], 0)
        self.assertEqual(self.make_store(package="other.clone").read()["_storage_generation"], 0)
        self.assertEqual(list(self.release.iterdir()), [])

    def test_legacy_read_only_then_atomic_migration(self):
        raw = json.dumps(self.baseline()).encode()
        self.store.legacy.write_bytes(raw)
        self.store.legacy.chmod(0o400)
        self.store.write({**self.store.read(), "locked_at_iso": "new"})
        self.assertEqual(self.store.legacy.read_bytes(), raw)
        self.assertEqual(self.store.read()["_storage_generation"], 1)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_stale_writer_cannot_overwrite(self):
        first = self.baseline()
        self.store.write(first)
        with self.assertRaisesRegex(LockStateError, "generation_changed"):
            self.store.write(first)

    def test_conflicting_dual_state_is_closed(self):
        self.store.legacy.write_text(json.dumps(self.baseline()))
        self.store.write(self.store.read())
        self.store.legacy.write_text(json.dumps({**self.baseline(), "authorized_version_code": 999}))
        with self.assertRaisesRegex(LockStateError, "ambiguous_dual_state"):
            self.store.read()

    def test_corrupt_external_is_not_missing(self):
        self.store.directory.mkdir(parents=True)
        self.store.path.write_text("truncated{")
        self.store.path.chmod(0o600)
        with self.assertRaisesRegex(LockStateError, "json_invalid"):
            self.store.read()

    def test_missing_legacy_scope_is_not_bootstrap_permission(self):
        self.store.legacy.write_text('{"authorized_version_code": 123}')
        with self.assertRaisesRegex(LockStateError, "scope_unproved"):
            self.store.read()

    def test_symlink_and_release_root_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.store.root.parent.mkdir(parents=True)
        self.store.root.symlink_to(outside)
        with self.assertRaisesRegex(LockStateError, "symlink"):
            self.store.write(self.baseline())
        self.assertEqual(list(outside.iterdir()), [])
        with self.assertRaisesRegex(LockStateError, "inside_release"):
            VersionLockStore(run_dir=self.release, release_root=self.release,
                             serial="x", package="p")

    def test_fsync_failure_does_not_publish_partial_state(self):
        baseline = self.baseline()
        with mock.patch("instagram_version_lock_state_v1.os.fsync", side_effect=OSError("fixture fsync failure")):
            with self.assertRaises(OSError):
                self.store.write(baseline)
        self.assertFalse(self.store.path.exists())
        self.assertEqual(list(self.release.iterdir()), [])

    def test_relocation_survives_new_release_without_new_legacy(self):
        self.store.write(self.baseline())
        other_release = self.root / "next-release"
        other_release.mkdir()
        other = VersionLockStore(run_dir=self.root / "runtime" / "run", release_root=other_release,
                                 host="host", serial="device", package="com.instagram.fixture")
        self.assertEqual(other.read(), self.store.read())

    def test_current_schema_binding_cannot_be_forged(self):
        self.store.write(self.baseline())
        payload = json.loads(self.store.path.read_text())
        payload["state"]["package"] = "other.clone"
        self.store.path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(LockStateError, "binding_mismatch"):
            self.store.read()

    def test_writer_contention_fails_without_mutating_authority(self):
        self.store.write(self.baseline())
        before = self.store.path.read_bytes()
        update = self.store.read()
        with self.store._writer():
            with self.assertRaisesRegex(LockStateError, "writer_busy"):
                self.store.write(update)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_pre_mutation_journal_is_durable_and_not_authority(self):
        self.store.write(self.baseline())
        entries = [json.loads(line) for line in (self.store.directory / "journal.jsonl").read_text().splitlines()]
        self.assertEqual(entries[0]["previous_generation"], 0)
        self.assertEqual(entries[0]["next_generation"], 1)
        before = self.store.path.read_bytes()
        with mock.patch("instagram_version_lock_state_v1.os.replace", side_effect=OSError("fixture crash beforerename")):
            with self.assertRaises(OSError):
                self.store.write(self.store.read())
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.store.read()["_storage_generation"], 1)
        self.assertFalse(list(self.store.directory.glob(".lock-state-*")))

    def test_external_read_rejects_shared_permissions_and_hardlink(self):
        self.store.write(self.baseline())
        self.store.path.chmod(0o644)
        with self.assertRaisesRegex(LockStateError, "file_invalid"):
            self.store.read()
        self.store.path.chmod(0o600)
        os.link(self.store.path, self.root / "external-link")
        with self.assertRaisesRegex(LockStateError, "file_invalid"):
            self.store.read()

    def test_failed_store_read_is_not_bootstrap_permission_in_real_check(self):
        import device
        from types import SimpleNamespace
        with mock.patch.object(device, "_lock_state_store", side_effect=LockStateError("fixture corrupt")), \
             mock.patch.object(device, "_fetch_package_version", return_value=("123", 123)), \
             mock.patch.object(device, "_package_disabled_for_user0", return_value=True), \
             mock.patch.object(device, "_package_suspended", return_value=False), \
             mock.patch.object(device, "_lock_state_write") as write:
            result = device.check_instagram_version_lock(SimpleNamespace(serial="device"))
        self.assertTrue(result["unsafe_for_automation"])
        self.assertTrue(result["lock_state_error"])
        write.assert_not_called()

    def test_failed_bootstrap_write_blocks_admission(self):
        import device
        from types import SimpleNamespace
        with mock.patch.object(device, "_lock_state_store", return_value=self.store), \
             mock.patch.object(device, "_fetch_package_version", return_value=("123", 123)), \
             mock.patch.object(device, "_package_disabled_for_user0", return_value=True), \
             mock.patch.object(device, "_package_suspended", return_value=False), \
             mock.patch.object(device, "_lock_state_write", side_effect=OSError("fixture disk failure")):
            result = device.check_instagram_version_lock(SimpleNamespace(serial="device"))
        self.assertTrue(result["unsafe_for_automation"])
        self.assertTrue(result["lock_state_error"])
