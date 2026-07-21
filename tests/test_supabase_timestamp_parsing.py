from __future__ import annotations

from datetime import timezone
import unittest

import supabase_client


class SupabaseTimestampParsingTests(unittest.TestCase):
    def test_accepts_postgres_variable_fractional_precision(self) -> None:
        parsed = supabase_client.parse_utc_iso_timestamp(
            "2026-07-17T16:05:33.83451+00:00"
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.microsecond, 834510)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_preserves_six_digit_fraction_and_z_suffix(self) -> None:
        parsed = supabase_client.parse_utc_iso_timestamp(
            "2026-07-17T16:05:33.123456Z"
        )

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.microsecond, 123456)
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_returns_none_for_invalid_timestamp(self) -> None:
        self.assertIsNone(supabase_client.parse_utc_iso_timestamp("not-a-timestamp"))


if __name__ == "__main__":
    unittest.main()
