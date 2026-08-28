import unittest
from unittest import mock

import device_action_latch as latch
import ui_continuity_v1 as continuity
from tests.test_ui_continuity_v1 import Clock


class Device:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.interrupt = False

    def click(self, *args):
        self.calls.append("click")
        if self.interrupt:
            self.clock.advance(230, monotonic=0)

    def press(self, *args):
        self.calls.append("press")

    def shell(self, *args):
        self.calls.append("shell")

    def jsonrpc_call(self, method, *args):
        self.calls.append(method)
        if self.interrupt:
            self.clock.advance(230, monotonic=0)
        return "fresh-xml"


class SuspensionDeviceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.fence = continuity.ContinuityFence(self.clock.sample, "fixture")
        self.patch = mock.patch.object(continuity, "_fence", self.fence)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        latch.configure(enabled=False, account_id="account", run_id="run")
        self.device = latch.install_device_guard(Device(self.clock))

    def test_guard_installed_even_when_canary_disabled(self):
        self.clock.advance(230, monotonic=0)
        for fn, args in ((self.device.click, (1, 2)), (self.device.press, ("back",)),
                         (self.device.shell, ("input tap 1 2",)),
                         (self.device.jsonrpc_call, ("click",))):
            with self.assertRaises(continuity.UiContinuityLost):
                fn(*args)
        self.assertEqual(self.device.calls, [])

    def test_inflight_capture_is_not_returned_as_fresh(self):
        self.device.interrupt = True
        with self.assertRaises(continuity.UiContinuityLost):
            self.device.jsonrpc_call("dumpWindowHierarchy")
        self.assertEqual(self.device.calls, ["dumpWindowHierarchy"])

    def test_inflight_tap_never_retried(self):
        self.device.interrupt = True
        with self.assertRaises(continuity.UiContinuityLost):
            self.device.click(1, 2)
        with self.assertRaises(continuity.UiContinuityLost):
            self.device.click(1, 2)
        self.assertEqual(self.device.calls, ["click"])

    def test_healthy_path_has_exact_same_device_calls(self):
        self.device.click(1, 2)
        self.device.jsonrpc_call("dumpWindowHierarchy")
        self.assertEqual(self.device.calls, ["click", "dumpWindowHierarchy"])


class RequiredContinuityChallengeTests(unittest.TestCase):
    """Release-blocking requirements, intentionally not xfailed or weakened.

    These expose gaps in the WIP implementation; a passing unit suite alone
    must not be mistaken for authorization to freeze or certify this candidate.
    """

    def test_long_awake_capture_must_not_become_global_attempt_stop(self):
        clock = Clock()
        fence = continuity.ContinuityFence(clock.sample, "fixture")

        class SlowRead(Device):
            def jsonrpc_call(self, method, *args):
                self.calls.append(method)
                clock.advance(30)  # Host awake throughout, no clock discontinuity.
                return "bounded-read-result"

        with mock.patch.object(continuity, "_fence", fence):
            latch.configure(enabled=False, account_id="account", run_id="run")
            device = latch.install_device_guard(SlowRead(clock))
            try:
                device.jsonrpc_call("dumpWindowHierarchy")
            except continuity.UiContinuityLost:
                self.fail("NO_GO: ordinary bounded 30s read globally terminates the attempt")

    def test_suspension_must_allow_fresh_verification_not_only_sticky_abort(self):
        clock = Clock()
        fence = continuity.ContinuityFence(clock.sample, "fixture")
        with mock.patch.object(continuity, "_fence", fence):
            latch.configure(enabled=False, account_id="account", run_id="run")
            device = latch.install_device_guard(Device(clock))
            clock.advance(230, monotonic=0)
            with self.assertRaises(continuity.UiContinuityLost):
                device.click(1, 2)
            # A fresh bounded read is only a prerequisite to re-proof, never
            # permission to clear the fence, replay a tap or trust an old token.
            try:
                device.jsonrpc_call("dumpWindowHierarchy")
            except continuity.UiContinuityLost:
                self.fail("NO_GO: read-only post-suspension re-verification is unreachable")
            self.assertNotIn("click", device.calls)
