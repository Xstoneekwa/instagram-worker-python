from datetime import datetime, timezone
import unittest
from unittest.mock import patch

import supabase_client


class SastBusinessDayCounterTests(unittest.TestCase):
    def test_sast_business_day_rolls_at_2200_utc(self) -> None:
        before = supabase_client.sast_business_day_window(
            datetime(2026, 7, 27, 21, 59, 59, 999999, tzinfo=timezone.utc)
        )
        after = supabase_client.sast_business_day_window(
            datetime(2026, 7, 27, 22, 0, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(before[0], "2026-07-27")
        self.assertEqual(before[1].isoformat(), "2026-07-26T22:00:00+00:00")
        self.assertEqual(before[2].isoformat(), "2026-07-27T22:00:00+00:00")
        self.assertEqual(after[0], "2026-07-28")
        self.assertEqual(after[1].isoformat(), "2026-07-27T22:00:00+00:00")
        self.assertEqual(after[2].isoformat(), "2026-07-28T22:00:00+00:00")

    def test_unfollow_counter_uses_half_open_sast_window(self) -> None:
        rows = [
            {"id": "before", "unfollowed_at": "2026-07-27T21:59:59.999Z"},
            {"id": "start", "unfollowed_at": "2026-07-27T22:00:00.000Z"},
            {"id": "inside", "unfollowed_at": "2026-07-28T10:00:00.000Z"},
            {"id": "end", "unfollowed_at": "2026-07-28T22:00:00.000Z"},
        ]
        window = (
            "2026-07-28",
            datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 28, 22, 0, tzinfo=timezone.utc),
        )
        with (
            patch.object(supabase_client, "sast_business_day_window", return_value=window),
            patch.object(supabase_client, "_request_json", return_value=rows) as request_json,
        ):
            self.assertEqual(supabase_client.count_successful_unfollows_today("account-a"), 2)
        self.assertEqual(
            request_json.call_args.kwargs["query"]["unfollowed_at"],
            "gte.2026-07-27T22:00:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
