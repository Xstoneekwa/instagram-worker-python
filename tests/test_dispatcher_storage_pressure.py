import unittest
from unittest import mock
from pathlib import Path

import account_run_request_consumer as dispatcher
import storage_health


class DispatcherStoragePressureTests(unittest.TestCase):
    def setUp(self):
        dispatcher._STORAGE_BLOCK_ACTIVE = False
        dispatcher._LAST_STORAGE_INCIDENT_AT = 0.0
        dispatcher._LAST_STORAGE_WARNING_AT = 0.0

    def snapshot(self, status):
        return storage_health.StorageSnapshot(
            status=status, filesystem_path="/", total_bytes=100, free_bytes=0,
            free_percent=0.0, warning_free_bytes=10, critical_free_bytes=2,
            warning_free_percent=5.0, critical_free_percent=2.0,
            checked_at_monotonic=1.0,
            reason="host_storage_critical" if status == "critical" else "",
        )

    def test_critical_storage_blocks_all_accounts_and_publishes_host_incident(self):
        cfg = mock.Mock(worker_id="dispatcher:test")
        with mock.patch(
            "account_run_request_consumer.storage_health.inspect_storage_health",
            return_value=self.snapshot("critical"),
        ), mock.patch(
            "account_run_request_consumer.runtime_incidents.publish_account_incident"
        ) as publish, mock.patch("account_run_request_consumer._heartbeat"):
            allowed, payload = dispatcher._dispatcher_storage_gate(cfg, boundary="claim")
        self.assertFalse(allowed)
        self.assertFalse(payload["new_run_allowed"])
        publish.assert_called_once()

    def test_restored_storage_recovers_without_manual_patch(self):
        cfg = mock.Mock(worker_id="dispatcher:test")
        dispatcher._STORAGE_BLOCK_ACTIVE = True
        with mock.patch(
            "account_run_request_consumer.storage_health.inspect_storage_health",
            return_value=self.snapshot("healthy"),
        ):
            allowed, _payload = dispatcher._dispatcher_storage_gate(cfg, boundary="loop")
        self.assertTrue(allowed)
        self.assertFalse(dispatcher._STORAGE_BLOCK_ACTIVE)

    def test_startup_storage_block_waits_in_process_instead_of_exiting(self):
        source = Path(dispatcher.__file__).read_text(encoding="utf-8")
        start = source.index("def run_forever(")
        recovery = source.index("orphan_run_reconciliation.reconcile_orphaned_active_runs", start)
        startup_gate = source[start:recovery]
        self.assertIn("while True:", startup_gate)
        self.assertIn('boundary="dispatcher_startup"', startup_gate)
        self.assertIn("process_exit=False", startup_gate)
        self.assertNotIn("return 6", startup_gate)


if __name__ == "__main__":
    unittest.main()
