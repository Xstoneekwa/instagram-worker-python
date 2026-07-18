from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import welcome_scan_producer as scan


class WelcomeScanAttemptCapTest(unittest.TestCase):
    @staticmethod
    def _follower_row(username: str, row_index: int) -> dict:
        return {
            "username": username,
            "row_index": row_index,
            "screen_index": 0,
            "bounds": {
                "left": 1,
                "top": row_index * 2 + 1,
                "right": 2,
                "bottom": row_index * 2 + 2,
            },
            "row_cta_xml_class": "message",
        }

    def _run_scan(
        self,
        *,
        known_map: dict[str, dict],
        rows: list[dict],
        attempt_cap: int = 2,
        welcome_send_max_jobs_env: int | None = None,
        db_welcome_per_session_limit: int = 1,
    ) -> tuple[int, dict, MagicMock]:
        enqueue_mock = MagicMock(
            side_effect=lambda _aid, username, **_kwargs: {
                "id": f"job-{username}",
                "status": "pending",
            }
        )
        device = MagicMock()
        env_patch = {}
        if welcome_send_max_jobs_env is not None:
            env_patch["WELCOME_SESSION_SEND_MAX_JOBS"] = str(welcome_send_max_jobs_env)
        with (
            patch.dict(os.environ, env_patch, clear=True),
            patch.object(scan.config, "WELCOME_SCAN_CANDIDATE_ATTEMPT_CAP", attempt_cap, create=True),
            patch.object(
                scan.config,
                "WELCOME_SESSION_SEND_MAX_JOBS",
                welcome_send_max_jobs_env if welcome_send_max_jobs_env is not None else 3,
                create=True,
            ),
            patch.object(scan.supabase_client, "ensure_account_dm_settings", return_value={
                "welcome_enabled": True,
                "welcome_baseline_completed_at": "2026-06-08T08:48:09Z",
                "welcome_template_id": "template-1",
                "welcome_per_session_limit": db_welcome_per_session_limit,
                "welcome_per_day_limit": 10,
                "total_dm_per_day_limit": 40,
            }),
            patch.object(scan.supabase_client, "get_account_dm_counter_today", return_value={}),
            patch.object(scan, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(scan, "verify_own_profile", return_value=(True, {})),
            patch.object(scan.config, "WELCOME_SCAN_FOLLOWERS_OPEN_WAIT_S", 0.6, create=True),
            patch.object(scan, "open_own_followers_list_from_own_profile", return_value=(True, {"det": {"is_followers_list": True}})) as open_followers_mock,
            patch.object(scan, "harvest_visible_followers_rows", return_value=(rows, {"visible_rows_count": len(rows), "extraction_methods": ["test"]})),
            patch.object(scan.supabase_client, "mark_followbacks_from_seen_followers", return_value={}),
            patch.object(scan.supabase_client, "fetch_followers_by_usernames", return_value=known_map),
            patch.object(scan.supabase_client, "fetch_pending_welcome_jobs_by_usernames", return_value={}),
            patch.object(scan.supabase_client, "enqueue_welcome_dm_job_if_eligible", enqueue_mock),
            patch.object(scan, "scroll_followers_list_forward", return_value=False),
        ):
            code = scan.run_welcome_scan_producer(
                device,
                account_id="acct-1",
                account_username="j_automatise_pour_toi",
                run_id="run-1",
            )
        self.assertEqual(open_followers_mock.call_args.kwargs["followers_open_wait_s"], 0.6)
        return code, scan.get_last_welcome_scan_summary(), enqueue_mock

    def test_scan_enqueues_two_new_followers_before_baseline_anchor_with_sent_cap_one(self) -> None:
        rows = [
            self._follower_row("pmwzstella", 0),
            self._follower_row("espehair", 1),
            self._follower_row("aquarellepeinture68", 2),
        ]
        known_map = {
            "aquarellepeinture68": {
                "follower_username": "aquarellepeinture68",
                "baseline_existing": True,
                "welcome_dm_status": "not_eligible_baseline",
            }
        }

        code, summary, enqueue_mock = self._run_scan(known_map=known_map, rows=rows)

        self.assertEqual(code, 0)
        self.assertEqual(summary["effective_welcome_sent_cap"], 1)
        self.assertEqual(summary["candidate_attempt_cap"], 2)
        self.assertFalse(summary["welcome_send_hard_cap_present"])
        self.assertEqual(summary["jobs_enqueued_count"], 2)
        self.assertEqual(
            summary["new_follower_usernames_enqueued"],
            ["pmwzstella", "espehair"],
        )
        self.assertEqual(enqueue_mock.call_count, 2)

    def test_explicit_welcome_send_hard_cap_one_limits_scan_enqueue_to_one(self) -> None:
        rows = [
            self._follower_row("pmwzstella", 0),
            self._follower_row("espehair", 1),
            self._follower_row("mini_durable", 2),
        ]

        code, summary, enqueue_mock = self._run_scan(
            known_map={},
            rows=rows,
            attempt_cap=10,
            welcome_send_max_jobs_env=1,
            db_welcome_per_session_limit=10,
        )

        self.assertEqual(code, 0)
        self.assertTrue(summary["welcome_send_hard_cap_present"])
        self.assertEqual(summary["welcome_send_hard_cap"], 1)
        self.assertEqual(summary["effective_welcome_sent_cap"], 1)
        self.assertEqual(summary["candidate_attempt_cap"], 1)
        self.assertEqual(summary["jobs_enqueued_count"], 1)
        self.assertEqual(summary["new_follower_usernames_enqueued"], ["pmwzstella"])
        self.assertEqual(summary["stop_reason"], "candidate_attempt_cap_reached")
        self.assertEqual(enqueue_mock.call_count, 1)

    def test_explicit_welcome_send_hard_cap_two_limits_scan_enqueue_to_two(self) -> None:
        rows = [
            self._follower_row("pmwzstella", 0),
            self._follower_row("espehair", 1),
            self._follower_row("mini_durable", 2),
        ]

        code, summary, enqueue_mock = self._run_scan(
            known_map={},
            rows=rows,
            attempt_cap=10,
            welcome_send_max_jobs_env=2,
            db_welcome_per_session_limit=10,
        )

        self.assertEqual(code, 0)
        self.assertTrue(summary["welcome_send_hard_cap_present"])
        self.assertEqual(summary["welcome_send_hard_cap"], 2)
        self.assertEqual(summary["effective_welcome_sent_cap"], 2)
        self.assertEqual(summary["candidate_attempt_cap"], 2)
        self.assertEqual(summary["jobs_enqueued_count"], 2)
        self.assertEqual(summary["new_follower_usernames_enqueued"], ["pmwzstella", "espehair"])
        self.assertEqual(enqueue_mock.call_count, 2)

    def test_scan_does_not_treat_skipped_nonbaseline_as_anchor(self) -> None:
        rows = [
            self._follower_row("pmwzstella", 0),
            self._follower_row("espehair", 1),
            self._follower_row("aquarellepeinture68", 2),
        ]
        known_map = {
            "pmwzstella": {
                "follower_username": "pmwzstella",
                "baseline_existing": False,
                "welcome_dm_status": "skipped",
                "skip_reason": "dm_not_available",
            },
            "aquarellepeinture68": {
                "follower_username": "aquarellepeinture68",
                "baseline_existing": True,
                "welcome_dm_status": "not_eligible_baseline",
            },
        }

        code, summary, enqueue_mock = self._run_scan(known_map=known_map, rows=rows)

        self.assertEqual(code, 0)
        self.assertEqual(summary["jobs_enqueued_count"], 1)
        self.assertEqual(summary["new_follower_usernames_enqueued"], ["espehair"])
        self.assertEqual(summary["first_anchor_username"], "aquarellepeinture68")
        enqueue_mock.assert_called_once()

    def test_scan_reuses_existing_pending_nonbaseline_job_before_anchor(self) -> None:
        rows = [
            self._follower_row("corpus_architecture_urbanisme", 0),
            self._follower_row("aquarellepeinture68", 1),
        ]
        known_map = {
            "corpus_architecture_urbanisme": {
                "follower_username": "corpus_architecture_urbanisme",
                "baseline_existing": False,
                "welcome_dm_status": "pending",
            },
            "aquarellepeinture68": {
                "follower_username": "aquarellepeinture68",
                "baseline_existing": True,
                "welcome_dm_status": "not_eligible_baseline",
            },
        }
        pending_jobs = {
            "corpus_architecture_urbanisme": {
                "id": "job-corpus",
                "recipient_username": "corpus_architecture_urbanisme",
                "status": "pending",
            }
        }
        enqueue_mock = MagicMock()
        device = MagicMock()
        with (
            patch.object(scan.config, "WELCOME_SCAN_CANDIDATE_ATTEMPT_CAP", 2, create=True),
            patch.object(scan.supabase_client, "ensure_account_dm_settings", return_value={
                "welcome_enabled": True,
                "welcome_baseline_completed_at": "2026-06-08T08:48:09Z",
                "welcome_template_id": "template-1",
                "welcome_per_session_limit": 1,
                "welcome_per_day_limit": 10,
                "total_dm_per_day_limit": 40,
            }),
            patch.object(scan.supabase_client, "get_account_dm_counter_today", return_value={}),
            patch.object(scan, "open_own_profile_from_bottom_nav", return_value=True),
            patch.object(scan, "verify_own_profile", return_value=(True, {})),
            patch.object(scan, "open_own_followers_list_from_own_profile", return_value=(True, {"det": {"is_followers_list": True}})),
            patch.object(scan, "harvest_visible_followers_rows", return_value=(rows, {"visible_rows_count": len(rows), "extraction_methods": ["test"]})),
            patch.object(scan.supabase_client, "mark_followbacks_from_seen_followers", return_value={}),
            patch.object(scan.supabase_client, "fetch_followers_by_usernames", return_value=known_map),
            patch.object(scan.supabase_client, "fetch_pending_welcome_jobs_by_usernames", return_value=pending_jobs),
            patch.object(scan.supabase_client, "enqueue_welcome_dm_job_if_eligible", enqueue_mock),
            patch.object(scan, "scroll_followers_list_forward", return_value=False),
        ):
            code = scan.run_welcome_scan_producer(
                device,
                account_id="acct-1",
                account_username="j_automatise_pour_toi",
                run_id="run-1",
            )

        summary = scan.get_last_welcome_scan_summary()
        self.assertEqual(code, 0)
        self.assertEqual(summary["jobs_enqueued_count"], 1)
        self.assertEqual(summary["new_follower_usernames_enqueued"], ["corpus_architecture_urbanisme"])
        self.assertEqual(summary["new_follower_job_ids_enqueued"][0]["job_id"], "job-corpus")
        enqueue_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
