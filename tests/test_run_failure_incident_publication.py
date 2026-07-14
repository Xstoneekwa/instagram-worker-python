from __future__ import annotations

import unittest
from unittest.mock import patch

import account_run_request_consumer as consumer

ACCOUNT_ID = "e9c7462b-fc0e-46c9-8d40-1e07e0f6a41b"
RUN_ID = "9e46c4a5-72c5-4b16-9f0f-96f6f2ff11aa"
REQUEST_ID = "7d3b4a4d-0000-4e0f-8a53-51e529a0a001"


def _identity_run_row() -> dict:
    return {
        "id": RUN_ID,
        "account_id": ACCOUNT_ID,
        "status": "failed",
        "performance_summary": {
            "reason": "active_instagram_account_mismatch",
            "account_identity_failure_reason": "actual_logged_in_username_not_detected",
            "run_type": "account_session",
        },
    }


class PublishRunFailureIncidentTest(unittest.TestCase):
    def test_reviewable_failure_upserts_canonical_operator_review_action(self) -> None:
        run_row = {
            "id": RUN_ID,
            "status": "failed",
            "performance_summary": {
                "reason": "recovered_snapshot_rejected",
                "run_type": "account_session",
                "welcome_scan_jobs_enqueued_count": 4,
            },
        }
        with (
            patch.object(consumer.supabase_client, "load_run_row", return_value=run_row),
            patch.object(consumer.supabase_client, "get_account_username", return_value="i_m_your_traker"),
            patch.object(
                consumer.runtime_incidents,
                "publish_account_incident",
                return_value={"published": True, "incident_id": "inc-welcome", "occurrence_count": 1},
            ) as publish,
            patch.object(
                consumer.supabase_client,
                "call_rpc",
                return_value={"id": "action-welcome"},
            ) as call_rpc,
            patch.object(
                consumer.incident_notifications,
                "dispatch_operator_review_action_notification",
            ) as notify,
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._publish_run_failure_incident(
                request_id=REQUEST_ID,
                account_id=ACCOUNT_ID,
                run_id=RUN_ID,
                run_type="account_session",
                exit_code=1,
                timed_out=False,
                canceled=False,
            )

        self.assertEqual(publish.call_args.kwargs["incident_type"], "welcome_surface_unstable")
        self.assertEqual(publish.call_args.kwargs["reason"], "recovered_snapshot_rejected")
        rpc_name, params = call_rpc.call_args.args
        self.assertEqual(rpc_name, "upsert_account_dashboard_action")
        self.assertEqual(params["p_action_type"], "operator_review_required")
        self.assertEqual(params["p_incident_id"], "inc-welcome")
        self.assertTrue(params["p_blocking_campaign"])
        notify.assert_called_once()
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs["payload"]["dashboard_action_id"], "action-welcome")
        self.assertEqual(notify.call_args.kwargs["action_id"], "action-welcome")
        self.assertEqual(notify.call_args.kwargs["incident_id"], "inc-welcome")

    def test_unknown_worker_failure_uses_the_same_operator_review_workflow(self) -> None:
        run_row = {"id": RUN_ID, "status": "failed", "performance_summary": {}}
        with (
            patch.object(consumer.supabase_client, "load_run_row", return_value=run_row),
            patch.object(consumer.supabase_client, "get_account_username", return_value="mythyl_fitness"),
            patch.object(
                consumer.runtime_incidents,
                "publish_account_incident",
                return_value={"published": True, "incident_id": "inc-worker", "occurrence_count": 1},
            ),
            patch.object(consumer.supabase_client, "call_rpc", return_value={"id": "action-worker"}) as call_rpc,
            patch.object(consumer.incident_notifications, "dispatch_operator_review_action_notification") as notify,
            patch.object(consumer, "_audit") as audit,
        ):
            consumer._publish_run_failure_incident(
                request_id=REQUEST_ID,
                account_id=ACCOUNT_ID,
                run_id=RUN_ID,
                run_type="account_session",
                exit_code=1,
                timed_out=False,
                canceled=False,
            )

        params = call_rpc.call_args.args[1]
        self.assertEqual(params["p_incident_id"], "inc-worker")
        self.assertEqual(params["p_action_type"], "operator_review_required")
        self.assertEqual(params["p_metadata"]["incident_type"], "run_worker_failure")
        self.assertEqual(params["p_metadata"]["reason"], "worker_exit_nonzero")
        notify.assert_called_once()
        audit.assert_called_once()

    def test_identity_failure_publishes_true_reason_incident(self) -> None:
        with (
            patch.object(
                consumer.supabase_client, "load_run_row", return_value=_identity_run_row()
            ),
            patch.object(
                consumer.supabase_client, "get_account_username", return_value="mythyl_fitness"
            ),
            patch.object(
                consumer.runtime_incidents,
                "publish_account_incident",
                return_value={"published": True, "incident_id": "inc-1", "occurrence_count": 1},
            ) as publish,
            patch.object(
                consumer.supabase_client,
                "call_rpc",
                return_value={"id": "action-identity"},
            ),
            patch.object(
                consumer.incident_notifications,
                "dispatch_operator_review_action_notification",
            ),
            patch.object(consumer, "_audit"),
        ):
            consumer._publish_run_failure_incident(
                request_id=REQUEST_ID,
                account_id=ACCOUNT_ID,
                run_id=RUN_ID,
                run_type="account_session",
                exit_code=75,
                timed_out=False,
                canceled=False,
            )
        publish.assert_called_once()
        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs["incident_type"], "run_identity_verification_failed")
        self.assertEqual(kwargs["reason"], "actual_logged_in_username_not_detected")
        self.assertEqual(kwargs["failure_reason"], "actual_logged_in_username_not_detected")
        self.assertEqual(kwargs["severity"], "critical")
        self.assertEqual(kwargs["account_id"], ACCOUNT_ID)
        self.assertEqual(kwargs["account_username"], "mythyl_fitness")
        self.assertEqual(kwargs["run_id"], RUN_ID)
        self.assertEqual(
            kwargs["dedupe_key"],
            f"account:{ACCOUNT_ID}:run:{RUN_ID}:run_identity_verification_failed",
        )
        self.assertIn("active Instagram account could not be confirmed", kwargs["action_required"])
        self.assertEqual(kwargs["metadata"]["run_request_id"], REQUEST_ID)

    def test_canceled_run_publishes_nothing(self) -> None:
        with (
            patch.object(
                consumer.supabase_client, "load_run_row", return_value=_identity_run_row()
            ),
            patch.object(
                consumer.runtime_incidents, "publish_account_incident"
            ) as publish,
        ):
            consumer._publish_run_failure_incident(
                request_id=REQUEST_ID,
                account_id=ACCOUNT_ID,
                run_id=RUN_ID,
                run_type="account_session",
                exit_code=143,
                timed_out=False,
                canceled=True,
            )
        publish.assert_not_called()

    def test_scheduler_gate_reason_publishes_nothing(self) -> None:
        run_row = {
            "id": RUN_ID,
            "status": "failed",
            "performance_summary": {"reason": "scheduler_disabled"},
        }
        with (
            patch.object(consumer.supabase_client, "load_run_row", return_value=run_row),
            patch.object(
                consumer.runtime_incidents, "publish_account_incident"
            ) as publish,
        ):
            consumer._publish_run_failure_incident(
                request_id=REQUEST_ID,
                account_id=ACCOUNT_ID,
                run_id=RUN_ID,
                run_type="account_session",
                exit_code=1,
                timed_out=False,
                canceled=False,
            )
        publish.assert_not_called()

    def test_exit_nonzero_without_run_row_falls_back(self) -> None:
        with (
            patch.object(consumer.supabase_client, "load_run_row", return_value=None),
            patch.object(
                consumer.supabase_client, "get_account_username", return_value=None
            ),
            patch.object(
                consumer.runtime_incidents,
                "publish_account_incident",
                return_value={"published": True},
            ) as publish,
        ):
            consumer._publish_run_failure_incident(
                request_id=REQUEST_ID,
                account_id=ACCOUNT_ID,
                run_id=None,
                run_type="account_session",
                exit_code=1,
                timed_out=False,
                canceled=False,
            )
        kwargs = publish.call_args.kwargs
        self.assertEqual(kwargs["incident_type"], "run_worker_failure")
        self.assertEqual(kwargs["reason"], "worker_exit_nonzero")
        # Without a run id the dedupe key falls back to the request id.
        self.assertEqual(
            kwargs["dedupe_key"],
            f"account:{ACCOUNT_ID}:run:{REQUEST_ID}:run_worker_failure",
        )

    def test_publish_errors_never_escape(self) -> None:
        with (
            patch.object(
                consumer.supabase_client,
                "load_run_row",
                side_effect=RuntimeError("network down"),
            ),
            patch.object(
                consumer.supabase_client, "get_account_username", return_value=None
            ),
            patch.object(
                consumer.runtime_incidents,
                "publish_account_incident",
                side_effect=RuntimeError("rpc down"),
            ),
        ):
            # Must never raise: incident publication is strictly best-effort.
            consumer._publish_run_failure_incident(
                request_id=REQUEST_ID,
                account_id=ACCOUNT_ID,
                run_id=RUN_ID,
                run_type="account_session",
                exit_code=75,
                timed_out=False,
                canceled=False,
            )


if __name__ == "__main__":
    unittest.main()
