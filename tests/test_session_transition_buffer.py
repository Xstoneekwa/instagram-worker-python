import unittest
from datetime import datetime, timezone

from session_transition_buffer import (
    SESSION_TRANSITION_BUFFER_ACTIVE_REASON,
    business_actions_allowed_now,
    derive_session_transition_timestamps,
)


class SessionTransitionBufferTest(unittest.TestCase):
    def test_derive_timestamps_t10(self) -> None:
        derived = derive_session_transition_timestamps(
            "2026-07-07T10:00:00Z",
            "2026-07-07T16:00:00Z",
        )
        self.assertIsNotNone(derived)
        assert derived is not None
        self.assertEqual(derived["business_action_deadline"], "2026-07-07T15:50:00Z")
        self.assertEqual(derived["preflight_start"], "2026-07-07T09:50:00Z")

    def test_business_actions_blocked_after_deadline(self) -> None:
        allowed = business_actions_allowed_now(
            now=datetime(2026, 7, 7, 15, 50, tzinfo=timezone.utc),
            business_action_deadline="2026-07-07T15:50:00Z",
        )
        self.assertFalse(allowed)

    def test_reason_constant(self) -> None:
        self.assertEqual(SESSION_TRANSITION_BUFFER_ACTIVE_REASON, "session_transition_buffer_active")


if __name__ == "__main__":
    unittest.main()
