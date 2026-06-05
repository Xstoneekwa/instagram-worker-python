from __future__ import annotations

import unittest
from unittest import mock

import supabase_client


class SupabaseClientRunStatusTest(unittest.TestCase):
    def test_update_run_status_persists_session_follow_and_like_counters(self) -> None:
        with mock.patch.object(supabase_client, "_request_json") as request_json:
            supabase_client.update_run_status(
                run_id="run-1",
                status="completed",
                totals={"total": 1, "success": 1, "failed": 0},
                performance_summary={"session_counters": {"follows": 1, "likes": 1}},
            )

        body = request_json.call_args.kwargs["body"]
        self.assertEqual(body["total_follow"], 1)
        self.assertEqual(body["total_like"], 1)
        self.assertEqual(body["total_targets"], 1)
        self.assertIn("completed_at", body)

    def test_update_run_status_leaves_counters_unchanged_when_session_counters_missing(self) -> None:
        with mock.patch.object(supabase_client, "_request_json") as request_json:
            supabase_client.update_run_status(
                run_id="run-1",
                status="completed",
                totals={"total": 1, "success": 1, "failed": 0},
                performance_summary={},
            )

        body = request_json.call_args.kwargs["body"]
        self.assertNotIn("total_follow", body)
        self.assertNotIn("total_like", body)


if __name__ == "__main__":
    unittest.main()
