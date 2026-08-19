from __future__ import annotations

import unittest
from unittest.mock import patch

import account_run_control


REQUEST = "00000000-0000-4000-8000-000000000001"
RUN = "00000000-0000-4000-8000-000000000002"


class AccountRunRequestLeaseRenewalV1Test(unittest.TestCase):
    @patch.object(account_run_control.supabase_client, "renew_account_run_request_lease_v1")
    def test_exact_lineage_is_forwarded(self, rpc) -> None:
        rpc.return_value = [{"id": REQUEST, "run_id": RUN}]
        row = account_run_control.renew_account_run_request_lease(
            REQUEST, "worker", RUN, lease_seconds=300
        )
        self.assertEqual(row["id"], REQUEST)
        rpc.assert_called_once_with(
            request_id=REQUEST,
            worker_id="worker",
            run_id=RUN,
            lease_seconds=300,
        )

    @patch.object(account_run_control.supabase_client, "renew_account_run_request_lease_v1")
    def test_missing_run_id_never_calls_rpc(self, rpc) -> None:
        self.assertIsNone(
            account_run_control.renew_account_run_request_lease(
                REQUEST, "worker", "", lease_seconds=300
            )
        )
        rpc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
