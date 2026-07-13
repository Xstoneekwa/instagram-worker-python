import unittest
from unittest import mock

import supabase_client


class VerifiedProgressEventTests(unittest.TestCase):
    def test_duplicate_progress_uses_same_deterministic_id(self) -> None:
        bodies = []

        def capture(*_args, **kwargs):
            bodies.append(kwargs["body"])
            self.assertEqual(kwargs["prefer_resolution"], "resolution=ignore-duplicates")

        with mock.patch.object(supabase_client, "_request_json_tolerate_unknown_columns", side_effect=capture):
            for _ in range(2):
                result = supabase_client.record_verified_progress_event(
                    "account-1",
                    "target_name",
                    "source_name",
                    run_id="11111111-1111-1111-1111-111111111111",
                    action_type="follow_verified",
                    payload={"verified_count": 1},
                )
                self.assertTrue(result["ok"])

        self.assertEqual(bodies[0]["id"], bodies[1]["id"])
        self.assertEqual(
            bodies[0]["payload"]["progress_key"],
            "11111111-1111-1111-1111-111111111111:follow_verified:target_name",
        )

    def test_attempt_has_no_verified_progress_contract(self) -> None:
        self.assertNotEqual(
            supabase_client.verified_progress_key("run-1", "follow_verified", "target"),
            supabase_client.verified_progress_key("run-1", "follow_tap_sent", "target"),
        )


if __name__ == "__main__":
    unittest.main()
