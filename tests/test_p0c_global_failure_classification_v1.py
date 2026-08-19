from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import auto_restart_runtime
import follow_persistence_intent


class P0CGlobalFailureClassificationV1Test(unittest.TestCase):
    def test_storage_preflight_failure_is_typed_global(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("x", encoding="utf-8")
            with patch.dict(
                follow_persistence_intent.os.environ,
                {"FOLLOW_PERSISTENCE_INTENT_ROOT": str(blocker / "child")},
            ):
                with self.assertRaises(
                    follow_persistence_intent.FollowPersistenceRuntimeUnavailable
                ):
                    follow_persistence_intent.preflight_runtime_storage()

    def test_same_release_restart_is_denied_for_systemic_persistence_failure(self) -> None:
        metadata = {
            "auto_restart": True,
            "source": auto_restart_runtime.AUTO_RESTART_TICK_SOURCE,
            "failure_category": "systemic_persistence_failure",
            "root_failure_code": "follow_persistence_runtime_unavailable",
        }
        ok, reason, policy = auto_restart_runtime.validate_auto_restart_request_at_claim(
            metadata=metadata,
            account_id="00000000-0000-4000-8000-000000000001",
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "same_release_systemic_persistence_failure")
        self.assertIsNone(policy)


if __name__ == "__main__":
    unittest.main()
