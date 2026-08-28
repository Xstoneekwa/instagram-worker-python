import unittest
from unittest import mock

import ui_continuity_v1 as continuity


class Clock:
    def __init__(self):
        self.c = self.m = self.w = 1_000_000_000_000

    def sample(self):
        return continuity.Sample(self.c, self.m, self.w)

    def advance(self, seconds, *, monotonic=None, wall=None):
        self.c += int(seconds * 1e9)
        self.m += int((seconds if monotonic is None else monotonic) * 1e9)
        self.w += int((seconds if wall is None else wall) * 1e9)


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.fence = continuity.ContinuityFence(self.clock.sample, "fixture")

    def test_awake_work_has_no_false_suspension(self):
        for _ in range(1000):
            self.clock.advance(.01)
            self.fence.check("normal")
        self.assertIsNone(self.fence.failure)

    def test_nab_230s_sleep_with_6_3s_monotonic_is_not_fresh(self):
        self.clock.advance(236.3, monotonic=6.3)
        with self.assertRaises(continuity.UiContinuityLost) as ctx:
            self.fence.check("LIKE")
        self.assertEqual(ctx.exception.evidence["cause"], "suspend_inclusive_clock_discontinuity")

    def test_long_awake_call_is_not_suspension(self):
        self.clock.advance(30)
        self.fence.check("OPEN_POST")
        self.assertIsNone(self.fence.failure)

    def test_exact_230s_wall_6s_monotonic_sleep(self):
        self.clock.advance(230, monotonic=6)
        with self.assertRaises(continuity.UiContinuityLost):
            self.fence.check("FOLLOW")
        self.assertEqual(self.fence.epoch, 1)
        self.assertEqual(self.fence.failure["continuous_delta_ns"], 230_000_000_000)
        self.assertEqual(self.fence.failure["monotonic_delta_ns"], 6_000_000_000)

    def test_failed_verification_never_clears_fence(self):
        self.clock.advance(230, monotonic=6)
        self.fence.observe("read")
        with self.assertRaises(ValueError):
            with self.fence.revalidation():
                with self.assertRaises(continuity.UiContinuityLost):
                    self.fence.check("MUTE")
                raise ValueError("exact identity unproved")
        with self.assertRaises(continuity.UiContinuityLost):
            self.fence.check("LIKE")

    def test_second_sleep_during_verification_cannot_clear_fence(self):
        self.clock.advance(230, monotonic=6)
        self.fence.observe("read")
        with self.assertRaises(continuity.UiContinuityLost):
            with self.fence.revalidation():
                self.clock.advance(230, monotonic=6)
        self.assertEqual(self.fence.epoch, 2)
        with self.assertRaises(continuity.UiContinuityLost):
            self.fence.check("RETURN_CT")

    def test_clock_rollback_or_invalid_timestamp_cannot_resurrect_legacy_proof(self):
        old_timestamp = self.clock.m / 1e9
        self.clock.advance(.001, monotonic=-2)
        with mock.patch.object(continuity, "_fence", self.fence):
            continuity.observe("rollback")
            with continuity.revalidation():
                pass  # Pure fence fixture; real identity call graph covered separately.
            for timestamp in (old_timestamp, float("nan"), float("inf"), 0):
                with self.subTest(timestamp=timestamp), self.assertRaises(continuity.UiContinuityLost):
                    continuity.require_capture(timestamp, "legacy")

    def test_revalidation_expiry_is_not_misclassified_as_suspension(self):
        self.clock.advance(230, monotonic=6)
        self.fence.observe("sleep")
        with self.assertRaises(continuity.UiContinuityLost) as exc:
            with self.fence.revalidation():
                self.clock.advance(30)
        self.assertEqual(exc.exception.evidence["cause"], "revalidation_proof_expired")
        self.assertEqual(self.fence.epoch, 1)
        self.assertIsNotNone(self.fence.failure)

    def test_wall_clock_jumps_both_directions(self):
        for wall in (500, -500):
            with self.subTest(wall=wall):
                clock = Clock()
                fence = continuity.ContinuityFence(clock.sample, "fixture")
                clock.advance(.001, wall=wall)
                with self.assertRaises(continuity.UiContinuityLost):
                    fence.check("foreground")

    def test_fence_cannot_be_cleared_by_retimestamp_or_new_xml(self):
        self.clock.advance(200, monotonic=0)
        for action in ("FOLLOW", "MUTE", "LIKE", "RETURN_CT", "OPEN_POST", "recovery", "new_xml"):
            with self.subTest(action=action), self.assertRaises(continuity.UiContinuityLost):
                self.fence.check(action)
            self.clock.advance(.001)

    def test_local_retry_cannot_swallow_loss(self):
        self.clock.advance(200, monotonic=0)
        with self.assertRaises(continuity.UiContinuityLost):
            try:
                self.fence.check("critical")
            except Exception:
                self.fail("candidate retry swallowed global safety boundary")

    def test_same_attempt_rearm_does_not_clear_failure(self):
        with mock.patch.object(continuity, "_fence", self.fence), mock.patch.object(continuity, "_attempt", "run"):
            self.clock.advance(230, monotonic=0)
            with self.assertRaises(continuity.UiContinuityLost):
                continuity.begin_attempt("run")
            with self.assertRaises(RuntimeError):
                continuity.begin_attempt("other-run")

    def test_disabled_context_is_read_only_and_unchanged(self):
        with mock.patch.object(continuity, "_fence", None):
            self.assertFalse(continuity.armed())
            continuity.check("not-a-business-run")

    def test_real_local_continuous_clock_is_available(self):
        clock, source = continuity._continuous_clock()
        self.assertGreater(clock(), 0)
        self.assertTrue(source)
