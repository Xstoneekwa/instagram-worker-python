from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch

import account_protection_lists as protection
import instagram_navigation
import supabase_client


def snapshot_env() -> dict[str, str]:
    return {
        protection.REQUIRED_ENV: "1",
        protection.SNAPSHOT_ENV: json.dumps(
            {
                "ok": True,
                "account_id": "11111111-1111-4111-8111-111111111111",
                "source": "account_protection_list_entries",
                "loaded_at": "2026-07-26T03:00:00+00:00",
                "lists": {
                    "interaction_blacklist": ["blocked.user"],
                    "unfollow_whitelist": ["protected.user"],
                },
                "versions": {"interaction_blacklist": 1, "unfollow_whitelist": 2},
            }
        ),
    }


class AccountProtectionActionGuardsTest(unittest.TestCase):
    def test_follow_blacklist_returns_before_any_device_access(self) -> None:
        device = Mock()
        with patch.dict(os.environ, snapshot_env(), clear=False):
            result = instagram_navigation.perform_follow_safe(device, "@blocked.user")
        self.assertEqual(result["failure_reason"], "interaction_blacklist")
        self.assertFalse(result["tapped"])
        self.assertFalse(device.mock_calls)

    def test_like_blacklist_returns_before_any_device_access(self) -> None:
        device = Mock()
        with patch.dict(os.environ, snapshot_env(), clear=False):
            result = instagram_navigation.run_post_follow_post_likes_phase(
                device,
                pkg="com.instagram.androif",
                source_profile_username="source",
                follower_username="blocked.user",
                visual_candidate_id="candidate-1",
                follow_success_verified=True,
                follow_state_after="following",
                skipped_tap=False,
            )
        self.assertEqual(result["failure_reason"], "interaction_blacklist")
        self.assertFalse(device.mock_calls)

    def test_welcome_enqueue_blacklist_never_renders_or_calls_rpc(self) -> None:
        with (
            patch.dict(os.environ, snapshot_env(), clear=False),
            patch.object(supabase_client, "_resolve_and_render_dm_message_for_enqueue") as render,
            patch.object(supabase_client, "call_rpc") as rpc,
        ):
            result = supabase_client.enqueue_welcome_dm_job_if_eligible(
                "11111111-1111-4111-8111-111111111111",
                "blocked.user",
            )
        self.assertIsNone(result)
        render.assert_not_called()
        rpc.assert_not_called()

    def test_outreach_enqueue_blacklist_never_renders_or_calls_rpc(self) -> None:
        with (
            patch.dict(os.environ, snapshot_env(), clear=False),
            patch.object(supabase_client, "_render_dm_message_body_for_enqueue") as render,
            patch.object(supabase_client, "call_rpc") as rpc,
        ):
            result = supabase_client.enqueue_outreach_dm_job(
                "11111111-1111-4111-8111-111111111111",
                "blocked.user",
            )
        self.assertIsNone(result)
        render.assert_not_called()
        rpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
