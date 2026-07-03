import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from account_commercial_policy import (
    commercial_policy_boundary_blocks_phase,
    evaluate_queued_run_commercial_policy,
    load_account_effective_package_policy,
    policy_revision_changed,
    stamp_commercial_policy_metadata,
)


class AccountCommercialPolicyTests(unittest.TestCase):
  def test_queued_run_blocks_when_revision_changed(self):
    meta = {"commercial_policy_revision": "rev-old"}
    with patch(
      "account_commercial_policy.load_account_commercial_policy_revision",
      return_value={"revision_token": "rev-new", "package_code": "growth"},
    ):
      ok, reason, ctx = evaluate_queued_run_commercial_policy("account-a", meta)
    self.assertFalse(ok)
    self.assertEqual(reason, "commercial_policy_revision_changed")
    self.assertEqual(ctx["current_revision"], "rev-new")

  def test_queued_run_allows_when_revision_matches(self):
    meta = {"commercial_policy_revision": "rev-same"}
    with patch(
      "account_commercial_policy.load_account_commercial_policy_revision",
      return_value={"revision_token": "rev-same", "package_code": "pro"},
    ):
      ok, reason, ctx = evaluate_queued_run_commercial_policy("account-a", meta)
    self.assertTrue(ok)
    self.assertIsNone(reason)
    self.assertEqual(ctx["commercial_policy_revision"], "rev-same")

  def test_queued_run_fail_closed_when_package_started_after_queue(self):
    with patch(
      "account_commercial_policy.load_account_commercial_policy_revision",
      return_value={
        "revision_token": "rev-new",
        "package_code": "growth",
        "package_starts_at": "2026-07-03T12:00:00+00:00",
      },
    ):
      ok, reason, _ctx = evaluate_queued_run_commercial_policy(
        "account-a",
        {},
        request_created_at="2026-07-03T11:00:00+00:00",
      )
    self.assertFalse(ok)
    self.assertEqual(reason, "commercial_policy_revision_changed")

  def test_stamp_metadata_adds_current_revision(self):
    with patch(
      "account_commercial_policy.load_account_commercial_policy_revision",
      return_value={"revision_token": "rev-1", "package_code": "pro"},
    ):
      meta = stamp_commercial_policy_metadata("account-a", {"requested_from": "scheduler"})
    self.assertEqual(meta["commercial_policy_revision"], "rev-1")
    self.assertEqual(meta["commercial_package_code"], "pro")
    self.assertEqual(meta["requested_from"], "scheduler")

  def test_no_queued_revision_never_blocks_without_package_drift(self):
    with patch(
      "account_commercial_policy.load_account_commercial_policy_revision",
      return_value={"revision_token": "rev-new", "package_starts_at": "2026-07-03T10:00:00+00:00"},
    ):
      ok, reason, _ctx = evaluate_queued_run_commercial_policy(
        "account-a",
        {},
        request_created_at="2026-07-03T11:00:00+00:00",
      )
    self.assertTrue(ok)
    self.assertIsNone(reason)
    self.assertFalse(policy_revision_changed("account-a", None))

  def test_active_boundary_detects_change(self):
    with patch(
      "account_commercial_policy.load_account_commercial_policy_revision",
      return_value={"revision_token": "rev-new", "package_code": "growth"},
    ):
      blocked = commercial_policy_boundary_blocks_phase(
        "account-a",
        bound_revision="rev-old",
        run_id="run-1",
        boundary="before_follow_phase",
      )
    self.assertTrue(blocked)

  def test_post_follow_likes_boundary_detects_change(self):
    with patch(
      "account_commercial_policy.load_account_commercial_policy_revision",
      return_value={"revision_token": "rev-new", "package_code": "growth"},
    ):
      blocked = commercial_policy_boundary_blocks_phase(
        "account-a",
        bound_revision="rev-old",
        run_id="run-1",
        boundary="before_post_follow_likes_phase",
      )
    self.assertTrue(blocked)

  def test_effective_package_policy_reads_summary(self):
    with patch(
      "account_commercial_policy.supabase_client.get_account_package_summary",
      return_value={
        "commercial_package_code": "growth",
        "package_caps": {"follow_day_cap": 40},
        "effective_caps_preview": {"follow_day_cap": 40},
      },
    ), patch(
      "account_commercial_policy.load_account_commercial_policy_revision",
      return_value={"revision_token": "rev-growth"},
    ):
      policy = load_account_effective_package_policy("account-a")
    self.assertEqual(policy["commercial_package_code"], "growth")
    self.assertEqual(policy["package_caps"]["follow_day_cap"], 40)


if __name__ == "__main__":
  unittest.main()
