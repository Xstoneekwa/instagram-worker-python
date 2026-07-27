import os
import tempfile
import unittest
from unittest.mock import patch

import deferred_projection_outbox as outbox


class DeferredProjectionOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {"WORKER_DEFERRED_PROJECTION_OUTBOX_PATH": os.path.join(self.tmp.name, "outbox.sqlite3")},
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_supabase_step_is_durable_and_deleted_after_replay(self) -> None:
        self.assertEqual(
            outbox.enqueue([{"kind": "supabase_step", "payload": {"fn_name": "probe", "args": ["a"], "kwargs": {"b": 2}}}]),
            1,
        )
        with patch.object(outbox.supabase_client, "probe", return_value={"ok": True}, create=True) as replay:
            first = outbox.drain()
            second = outbox.drain()
        replay.assert_called_once_with("a", b=2)
        self.assertEqual(first["succeeded_count"], 1)
        self.assertEqual(second["claimed_count"], 0)

    def test_failed_replay_remains_eligible_for_next_dispatcher_loop(self) -> None:
        outbox.enqueue([{"kind": "supabase_step", "payload": {"fn_name": "probe", "args": [], "kwargs": {}}}])
        with patch.object(outbox.supabase_client, "probe", side_effect=RuntimeError("offline"), create=True):
            failed = outbox.drain()
        with patch.object(outbox.supabase_client, "probe", return_value={"ok": True}, create=True):
            recovered = outbox.drain()
        self.assertEqual(failed["failed_count"], 1)
        self.assertEqual(recovered["succeeded_count"], 1)

    def test_action_log_replay_is_bounded_to_exact_payload(self) -> None:
        payload = {
            "run_id": "run",
            "account_id": "acct",
            "target_username": "target",
            "action_type": "follow_tap_sent",
            "status": "info",
            "message": "follow_tap_sent",
            "payload": {"safe": True},
        }
        outbox.enqueue([{"kind": "action_log", "payload": payload}])
        with patch.object(outbox.supabase_client, "insert_action_log", return_value={"ok": True}) as replay:
            result = outbox.drain()
        replay.assert_called_once_with(**payload)
        self.assertEqual(result["succeeded_count"], 1)


if __name__ == "__main__":
    unittest.main()
