from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

import startup_tick_skip_token as protocol


class StartupTickSkipTokenOwnershipV1Test(unittest.TestCase):
    def _layout(self, raw: str) -> tuple[Path, Path]:
        runtime_home = Path(raw) / "runtime"
        token_parent = runtime_home / "run" / "run-control-dispatcher"
        token_parent.mkdir(parents=True)
        token_parent.chmod(0o755)
        return runtime_home, token_parent / protocol.TOKEN_FILENAME

    def test_same_service_identity_can_prepare_consume_and_prove_exact_token(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime_home, token = self._layout(raw)
            prepared = protocol.prepare_startup_skip_token(token, runtime_home)
            self.assertEqual(stat.S_IMODE(token.stat().st_mode), 0o600)
            self.assertEqual(
                (token.stat().st_uid, token.stat().st_gid),
                (runtime_home.stat().st_uid, runtime_home.stat().st_gid),
            )
            consumed = protocol.consume_startup_skip_token(token, runtime_home)
            proof = protocol.verify_startup_skip_consumed(
                token, runtime_home, prepared["token"]
            )
        self.assertTrue(consumed["consumed"])
        self.assertTrue(proof["ok"])
        self.assertTrue(proof["token_consumed"])
        self.assertTrue(proof["token_cleanup"])

    def test_root_creator_fixture_assigns_canonical_service_owner_and_group(self):
        root_created = mock.Mock(st_uid=0, st_gid=0)
        identity = protocol.RuntimeIdentity(uid=501, gid=20)
        with mock.patch.object(protocol.os, "fstat", return_value=root_created), \
             mock.patch.object(protocol.os, "fchown") as fchown, \
             mock.patch.object(protocol.os, "fchmod") as fchmod:
            protocol._apply_private_identity(17, identity)
        fchown.assert_called_once_with(17, 501, 20)
        fchmod.assert_called_once_with(17, 0o600)

    def test_unrelated_identity_has_no_permission_bits(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime_home, token = self._layout(raw)
            protocol.prepare_startup_skip_token(token, runtime_home)
            mode = stat.S_IMODE(token.stat().st_mode)
        self.assertEqual(mode & 0o077, 0)

    def test_existing_wrong_mode_or_owner_group_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime_home, token = self._layout(raw)
            protocol.prepare_startup_skip_token(token, runtime_home)
            token.chmod(0o640)
            with self.assertRaisesRegex(protocol.StartupSkipTokenError, "mode_mismatch"):
                protocol.prepare_startup_skip_token(token, runtime_home)

            token.chmod(0o600)
            actual = protocol.canonical_runtime_identity(runtime_home, token.parent)
            wrong = protocol.RuntimeIdentity(actual.uid + 1, actual.gid)
            with mock.patch.object(protocol, "canonical_runtime_identity", return_value=wrong):
                with self.assertRaisesRegex(protocol.StartupSkipTokenError, "owner_group_mismatch"):
                    protocol.prepare_startup_skip_token(token, runtime_home)

    def test_consumed_token_is_not_reused_and_stale_valid_token_consumes_once(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime_home, token = self._layout(raw)
            protocol.prepare_startup_skip_token(token, runtime_home)
            os.utime(token, (1, 1))
            first = protocol.consume_startup_skip_token(token, runtime_home)
            second = protocol.consume_startup_skip_token(token, runtime_home)
        self.assertTrue(first["consumed"])
        self.assertEqual(second, {"consumed": False, "reason": "guard_absent"})

    def test_parallel_prepare_publishes_one_complete_token(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime_home, token = self._layout(raw)
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda _: protocol.prepare_startup_skip_token(token, runtime_home),
                    range(2),
                ))
            self.assertEqual(token.read_bytes(), protocol.TOKEN_MARKER)
        self.assertEqual(sum(bool(item["token"]["created"]) for item in results), 1)
        self.assertEqual(len({item["token"]["inode"] for item in results}), 1)

    def test_atomic_target_is_invisible_until_private_complete_temp_is_ready(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime_home, token = self._layout(raw)
            original_replace = protocol.os.replace
            observed = []

            def inspect_then_replace(source, destination):
                if Path(destination) == token:
                    observed.append((token.exists(), stat.S_IMODE(Path(source).stat().st_mode)))
                return original_replace(source, destination)

            with mock.patch.object(protocol.os, "replace", side_effect=inspect_then_replace):
                protocol.prepare_startup_skip_token(token, runtime_home)
        self.assertEqual(observed, [(False, 0o600)])

    def test_permission_setup_failure_never_publishes_target(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime_home, token = self._layout(raw)
            with mock.patch.object(protocol, "_apply_private_identity", side_effect=protocol.StartupSkipTokenError("setup_failed")):
                with self.assertRaisesRegex(protocol.StartupSkipTokenError, "setup_failed"):
                    protocol.prepare_startup_skip_token(token, runtime_home)
            leftovers = list(token.parent.glob(f".{token.name}.prepare.*"))
        self.assertFalse(token.exists())
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
