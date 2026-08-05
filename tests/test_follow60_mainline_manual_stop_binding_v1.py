import unittest
from unittest.mock import patch

import runner


class Follow60MainlineManualStopBindingV1Tests(unittest.TestCase):
    def test_mainline_binding_is_forwarded_to_post_follow_stop_drain(self) -> None:
        captured = {}

        def flush_pending_bounded(*, active_binding, budget_s):
            captured.update(active_binding)
            return {"ok": True, "pending": 3, "flushed": 3, "budget_s": budget_s}

        with patch.object(
            runner.follow_persistence_receipt_replay,
            "replay_verified_receipts",
            return_value={"ok": True, "replayed": 0},
        ), patch(
            "post_follow_stage_outbox.flush_pending_bounded",
            side_effect=flush_pending_bounded,
        ):
            result = runner._drain_candidate_receipts_for_manual_stop(
                account_id="account-one",
                run_id="run-one",
                request_id="request-one",
                control_id="mainline-control-one",
                worker_sha="worker-one",
                binding_kind="mainline",
                budget_s=1.0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("mainline", captured["binding_kind"])
        self.assertEqual("account-one", captured["account_id"])
        self.assertEqual("run-one", captured["run_id"])
        self.assertEqual("request-one", captured["request_id"])
        self.assertEqual("mainline-control-one", captured["control_id"])
        self.assertEqual("worker-one", captured["worker_sha"])
        self.assertEqual(3, result["post_follow_replay"]["flushed"])

    def test_mainline_runtime_never_arms_canary_evaluation_hold(self) -> None:
        with patch.object(
            runner, "_follow60_canary_enabled_for_account", return_value=True
        ):
            self.assertFalse(
                runner._follow60_canary_evaluation_hold_allowed(
                    account_id="account-one",
                    runtime_context={"binding_kind": "mainline"},
                )
            )

    def test_canary_runtime_still_allows_canary_evaluation_hold(self) -> None:
        with patch.object(
            runner, "_follow60_canary_enabled_for_account", return_value=True
        ):
            self.assertTrue(
                runner._follow60_canary_evaluation_hold_allowed(
                    account_id="account-one",
                    runtime_context={"binding_kind": "canary"},
                )
            )

    def test_missing_binding_kind_fails_closed_without_outbox_flush(self) -> None:
        with patch.object(
            runner.follow_persistence_receipt_replay,
            "replay_verified_receipts",
            return_value={"ok": True, "replayed": 0},
        ), patch("post_follow_stage_outbox.flush_pending_bounded") as flush:
            result = runner._drain_candidate_receipts_for_manual_stop(
                account_id="account-one",
                run_id="run-one",
                request_id="request-one",
                control_id="control-one",
                worker_sha="worker-one",
                budget_s=1.0,
            )

        flush.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(
            "post_follow_replay_not_applicable_without_active_binding",
            result["post_follow_replay"]["reason"],
        )


if __name__ == "__main__":
    unittest.main()
