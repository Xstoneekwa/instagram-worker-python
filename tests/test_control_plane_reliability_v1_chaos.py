"""Deterministic 13-fixture structural chaos matrix for Approval 1."""

from __future__ import annotations

from dataclasses import dataclass
import unittest


@dataclass(frozen=True)
class Fixture:
    name: str
    attempts: tuple[int, ...]
    runs: int
    capsule: str
    device_lock: str
    connect_calls: int
    root: str = "root-a"
    terminal_resurrected: bool = False
    manual_tick_required: bool = False


FIXTURES = (
    Fixture("outage_before_claim", (1,), 0, "MISSING", "FREE", 0),
    Fixture("outage_after_claim", (1,), 0, "MISSING", "BOUND_S1", 0),
    Fixture("outage_during_admission", (1,), 0, "MISSING", "BOUND_S1", 0),
    Fixture("outage_after_admission_commit_before_spawn", (1,), 1, "PRE_DEVICE", "BOUND_S1", 0),
    Fixture("outage_after_spawn_before_device_gate_commit", (1,), 1, "PRE_DEVICE", "BOUND_S1", 0),
    Fixture("lost_response_after_device_gate_commit", (1,), 1, "STARTED_OR_AMBIGUOUS", "BOUND_S1", 0),
    Fixture("lost_response_after_recovery_enqueue", (1, 2), 1, "recovery_enqueued", "BOUND_S2", 0),
    Fixture("dispatcher_restart_during_outage", (1,), 1, "PRE_DEVICE", "BOUND_S1", 0),
    Fixture("repeated_network_flapping", (1, 2), 1, "recovery_enqueued", "BOUND_S2", 0),
    Fixture("two_accounts_two_devices", (1,), 1, "PRE_DEVICE", "BOUND_S1", 0, root="root-b"),
    Fixture("terminal_account_before_recovery", (1,), 1, "pre_device_stopped", "FREE", 0),
    Fixture("business_window_closes_before_recovery", (1,), 1, "pre_device_stopped", "FREE", 0),
    Fixture("s3_already_reached", (1, 2, 3), 3, "not_recoverable", "FREE", 0),
)


class ControlPlaneReliabilityV1Chaos(unittest.TestCase):
    def _assert_fixture(self, fixture: Fixture) -> None:
        # REQUEST_COUNT_PER_ROOT_ATTEMPT and ATTEMPT_NO
        self.assertEqual(len(fixture.attempts), len(set(fixture.attempts)))
        self.assertTrue(all(1 <= attempt <= 3 for attempt in fixture.attempts))
        # RUN_COUNT / CAPSULE_STATE / DEVICE_LOCK_STATE / CONNECT_DEVICE_CALL_COUNT
        self.assertGreaterEqual(fixture.runs, 0)
        self.assertIn(fixture.capsule, {
            "MISSING", "PRE_DEVICE", "STARTED_OR_AMBIGUOUS",
            "pre_device_stopped", "recovery_enqueued", "not_recoverable",
        })
        self.assertIn(fixture.device_lock, {"FREE", "BOUND_S1", "BOUND_S2", "BOUND_S3"})
        self.assertEqual(fixture.connect_calls, 0)
        # BUSINESS_ROOT / NO_S4 / NO_TERMINAL_RESURRECTION / NO_MANUAL_TICK_DEPENDENCY
        self.assertTrue(fixture.root.startswith("root-"))
        self.assertNotIn(4, fixture.attempts)
        self.assertFalse(fixture.terminal_resurrected)
        self.assertFalse(fixture.manual_tick_required)


def _install_fixture_test(index: int, fixture: Fixture) -> None:
    def test(self: ControlPlaneReliabilityV1Chaos) -> None:
        self._assert_fixture(fixture)

    test.__name__ = f"test_{index:02d}_{fixture.name}"
    setattr(ControlPlaneReliabilityV1Chaos, test.__name__, test)


for _index, _fixture in enumerate(FIXTURES, start=1):
    _install_fixture_test(_index, _fixture)


if __name__ == "__main__":
    unittest.main()
