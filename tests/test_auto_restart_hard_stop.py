import unittest
from unittest.mock import patch

from auto_restart_hard_stop import execute_auto_restart_hard_stop


class AutoRestartHardStopTests(unittest.TestCase):
    @patch("auto_restart_hard_stop.upsert_hard_stop_dashboard_action", return_value={"published": True})
    @patch("auto_restart_hard_stop.publish_account_incident", return_value={"published": True, "incident_id": "incident-1"})
    def test_incident_uses_legacy_clone_id_not_app_instance_id(self, publish, _dashboard) -> None:
        execute_auto_restart_hard_stop(
            account_id="11111111-1111-4111-8111-111111111111",
            reason="challenge_blocked",
            clone_id="22222222-2222-4222-8222-222222222222",
            app_instance_id="33333333-3333-4333-8333-333333333333",
            cancel_pending=False,
        )
        self.assertEqual(publish.call_args.kwargs["clone_id"], "22222222-2222-4222-8222-222222222222")
        self.assertEqual(
            publish.call_args.kwargs["metadata"]["app_instance_id"],
            "33333333-3333-4333-8333-333333333333",
        )


if __name__ == "__main__":
    unittest.main()
