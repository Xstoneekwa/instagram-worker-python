from __future__ import annotations

import threading
import unittest
from unittest import mock

import device_action_latch
import supabase_client


class _FakeDevice:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def click(self, *_args, **_kwargs):
        self.actions.append("click")

    def swipe(self, *_args, **_kwargs):
        self.actions.append("swipe")

    def press(self, *_args, **_kwargs):
        self.actions.append("press")

    def long_click(self, *_args, **_kwargs):
        self.actions.append("long_click")

    def app_stop(self, *_args, **_kwargs):
        self.actions.append("app_stop")

    def shell(self, command, *_args, **_kwargs):
        self.actions.append(f"shell:{command}")


class Follow60sStopLatchTest(unittest.TestCase):
    def tearDown(self) -> None:
        device_action_latch.configure(enabled=False, account_id="", run_id="")

    def test_all_device_gestures_are_blocked_after_latch(self) -> None:
        device_action_latch.configure(enabled=True, account_id="account", run_id="run")
        device = device_action_latch.install_device_guard(_FakeDevice())
        device.click(1, 2)
        device.shell("getprop ro.build.version.release")
        device_action_latch.request_stop(reason="test")
        for action in (
            lambda: device.click(1, 2),
            lambda: device.swipe(1, 2, 3, 4),
            lambda: device.press("back"),
            lambda: device.long_click(1, 2),
            lambda: device.app_stop("com.instagram.android"),
            lambda: device.shell("input tap 1 2"),
        ):
            with self.assertRaises(device_action_latch.DeviceActionBlocked):
                action()
        self.assertEqual(device.actions, ["click", "shell:getprop ro.build.version.release"])

    def test_stop_waits_for_inflight_action_then_closes_the_latch(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class _BlockingDevice(_FakeDevice):
            def click(self, *_args, **_kwargs):
                started.set()
                release.wait(timeout=2.0)
                self.actions.append("click")

        device_action_latch.configure(enabled=True, account_id="account", run_id="run")
        device = device_action_latch.install_device_guard(_BlockingDevice())
        action = threading.Thread(target=device.click, args=(1, 2), daemon=True)
        action.start()
        self.assertTrue(started.wait(timeout=1.0))

        trace_at_stop = device_action_latch.request_stop(reason="race_test")
        self.assertNotIn("device_actions_quiesced_at", trace_at_stop)
        release.set()
        action.join(timeout=1.0)
        self.assertFalse(action.is_alive())
        self.assertIn("device_actions_quiesced_at", device_action_latch.trace())
        with self.assertRaises(device_action_latch.DeviceActionBlocked):
            device.click(1, 2)

    def test_stage_rpc_preserves_exact_binding_and_idempotency_key(self) -> None:
        with mock.patch.object(
            supabase_client,
            "call_rpc",
            return_value={"ok": True, "inserted": True},
        ) as rpc:
            out = supabase_client.persist_follow_60s_stage_v1(
                account_id="account",
                run_id="run",
                request_id="request",
                action_id="action",
                username="@Candidate",
                source_profile="ct",
                stage="like_verified",
                stage_idempotency_key="action:like_verified",
                event_at="2026-07-31T00:00:00+00:00",
                payload={"liked_count": 1},
            )
        self.assertTrue(out["ok"])
        params = rpc.call_args.args[1]
        self.assertEqual(params["p_request_id"], "request")
        self.assertEqual(params["p_stage_idempotency_key"], "action:like_verified")
        self.assertEqual(params["p_username"], "candidate")


if __name__ == "__main__":
    unittest.main()
