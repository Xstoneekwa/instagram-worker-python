"""Offline production entry/consumer paths; no device, RPC, DB or run admission."""
import ast
from pathlib import Path
import unittest
from unittest import mock

import ui_continuity_v1 as continuity
import followers_surface_proof as surfaces
import instagram_navigation as nav
import runner
import account_identity_guard as identity
import device_action_latch as latch
from tests.test_ui_continuity_v1 import Clock
from tests import test_post_open_intent_v2 as post_intent_fixtures


class CallGraphTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.fence = continuity.ContinuityFence(self.clock.sample, "fixture")
        patcher = mock.patch.object(continuity, "_fence", self.fence)
        patcher.start()
        self.addCleanup(patcher.stop)
        surfaces.reset_for_tests()
        self.addCleanup(surfaces.reset_for_tests)

    def suspend(self):
        self.clock.advance(230, monotonic=0)

    def test_post_open_intent_same_binding_cannot_survive_suspension(self):
        helper = post_intent_fixtures.PostOpenIntentV2Tests()
        helper.setUp()
        intent = helper._intent()
        before = intent.payload()
        self.suspend()
        with self.assertRaises(continuity.UiContinuityLost):
            helper._consume(intent)
        self.assertEqual(intent.payload(), before)  # No durable binding reset.

    def test_followers_cached_identity_and_row_are_not_authority_after_sleep(self):
        scope = surfaces.FollowersSurfaceScope("phone", "clone", "account", "ct", "followers")
        proof = surfaces.capture(scope, "<hierarchy/>")
        self.suspend()
        with self.assertRaises(continuity.UiContinuityLost):
            surfaces.validate(proof, scope, mode="same_surface_read")

    def test_fresh_retimestamp_cannot_launder_old_like_context(self):
        self.suspend()
        with self.assertRaises(continuity.UiContinuityLost):
            nav._create_like_tap_context_v2(
                None, expected_package="com.instagram.fixture", expected_follower_username="candidate",
                expected_stage_binding={"account_id": "account", "action_id": "durable-action"},
                post_open_context={},
            )

    def test_runner_records_cause_without_device_cleanup_or_receipt_fabrication(self):
        self.suspend()
        with mock.patch.object(runner, "_main_impl", side_effect=lambda: continuity.check("LIKE")), \
             mock.patch.object(runner, "_CURRENT_RUN_ID", "fixture-run"), \
             mock.patch.object(runner, "_RUNTIME_FOLLOW_COUNT", 15), \
             mock.patch.object(runner, "_update_run_status_safe") as update, \
             mock.patch.object(runner, "_return_with_cleanup", side_effect=AssertionError("unsafe cleanup gesture")):
            self.assertEqual(runner.main(), 1)
        args = update.call_args.kwargs
        self.assertEqual(args["status"], "failed")
        self.assertEqual(args["totals"]["success"], 15)
        self.assertEqual(args["performance_summary"]["first_failure_reason"], continuity.REASON)

    def test_business_runner_arms_fence_before_device_wrappers(self):
        source = (Path(__file__).resolve().parents[1] / "runner.py").read_text()
        arm = source.index("ui_continuity_v1.begin_attempt(")
        wrap = source.index("d = device_action_latch.install_device_guard(d)")
        self.assertLess(arm, wrap)

    def test_every_ui_subsystem_has_a_consumer_fence(self):
        required = {
            "follow_action_engine.py": "follow_action_surface_wait_and_select_element",
            "account_identity_guard.py": "verify_active_instagram_account_matches_expected",
            "navigation_engine.py": "observe_instagram_state",
            "own_following_navigation.py": "open_own_following_list_from_own_profile",
            "follow_60s_canary.py": "consume",
            "instagram_navigation.py": "run_mute_engine_v2",
        }
        root = Path(__file__).resolve().parents[1]
        for file, name in required.items():
            with self.subTest(file=file):
                tree = ast.parse((root / file).read_text())
                fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
                body = list(fn.body)
                if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    body.pop(0)
                expected = "ui_continuity_v1.observe" if file == "account_identity_guard.py" else "ui_continuity_v1.check"
                self.assertIn(expected, ast.unparse(body[0]))


class FreshIdentityReentryTests(unittest.TestCase):
    PACKAGE = "com.instagram.fixture"

    def setUp(self):
        self.clock = Clock()
        self.fence = continuity.ContinuityFence(self.clock.sample, "fixture")
        patcher = mock.patch.object(continuity, "_fence", self.fence)
        patcher.start()
        self.addCleanup(patcher.stop)
        latch.configure(enabled=False, account_id="account", run_id="run")
        self.device = self.make_device()

    def make_device(self):
        owner = self
        class Device:
            username = "expected"
            package = owner.PACKAGE
            activity = "com.instagram.mainactivity.InstagramMainActivity"
            own = True
            restricted = False
            interrupt = False
            mutate_during_read = False
            calls = None

            def __init__(self):
                self.calls = []

            def app_current(self):
                self.calls.append("foreground")
                return {"package": self.package, "activity": self.activity}

            def dump_hierarchy(self, **kwargs):
                self.calls.append("xml")
                if self.mutate_during_read:
                    with owner.assertRaises(continuity.UiContinuityLost):
                        self.click(1, 2)
                if self.interrupt:
                    owner.clock.advance(230, monotonic=6)
                modal = '<node text="We added a restriction to your account"/><node text="You cannot send messages"/>' if self.restricted else ""
                return ('<hierarchy>'
                    f'<node package="{self.package}" resource-id="{self.package}:id/action_bar_title" text="{self.username}"/>'
                    f'<node package="{self.package}" resource-id="{self.package}:id/profile_tab" selected="{str(self.own).lower()}"/>'
                    f'<node package="{self.package}" resource-id="{self.package}:id/button_edit_profile"/>'
                    + modal + '</hierarchy>')

            def click(self, *args):
                self.calls.append("click")

            def press(self, *args):
                self.calls.append("press")
        return latch.install_device_guard(Device())

    def verify(self):
        return identity.verify_active_instagram_account_matches_expected(
            self.device, expected_account_username="expected", expected_package_name=self.PACKAGE,
            account_id="account", run_id="run", request_id="request")

    def suspend(self):
        self.clock.advance(230, monotonic=6)

    def test_fresh_canonical_identity_can_reopen_admission_without_replaying_action(self):
        helper = post_intent_fixtures.PostOpenIntentV2Tests()
        helper.setUp()
        old = helper._intent()
        self.suspend()
        for action in ("FOLLOW", "MUTE", "LIKE", "RETURN_CT"):
            with self.assertRaises(continuity.UiContinuityLost):
                latch.assert_device_action_allowed(action)
        self.device.mutate_during_read = True
        result = self.verify()
        self.assertTrue(result.ok, result.to_dict())
        self.assertEqual(self.device.calls, ["foreground", "xml", "foreground"])
        self.assertIsNone(continuity.failed())
        with self.assertRaises(continuity.UiContinuityLost):
            helper._consume(old)
        fresh = helper._intent()
        self.assertEqual(fresh.continuity_epoch, 1)
        self.assertIsNotNone(helper._consume(fresh)[0])
        self.device.click(1, 2)
        self.assertEqual(self.device.calls[-1], "click")

    def test_repeated_epochs_never_resurrect_old_proofs(self):
        helper = post_intent_fixtures.PostOpenIntentV2Tests()
        helper.setUp()
        previous = []
        for expected_epoch in (1, 2, 3):
            previous.append(helper._intent())
            self.suspend()
            self.assertTrue(self.verify().ok)
            self.assertEqual(continuity.epoch(), expected_epoch)
            for proof in previous:
                with self.assertRaises(continuity.UiContinuityLost):
                    helper._consume(proof)

    def test_fresh_identity_does_not_resurrect_followers_or_transported_like_xml(self):
        scope = surfaces.FollowersSurfaceScope("phone", "clone", "account", "ct", "followers")
        proof = surfaces.capture(scope, "<hierarchy/>")
        old = self.clock.m / 1e9
        self.suspend()
        self.assertTrue(self.verify().ok)
        self.assertEqual(surfaces.validate(proof, scope, mode="same_surface_read"), (False, "generation_mismatch"))
        fresh = surfaces.capture(scope, "<hierarchy/>")
        self.assertTrue(surfaces.validate(fresh, scope, mode="same_surface_read")[0])
        with self.assertRaises(continuity.UiContinuityLost):
            nav._create_like_tap_context_v2(
                None, expected_package=self.PACKAGE, expected_follower_username="candidate",
                expected_stage_binding={"account_id": "account", "action_id": "durable-action"},
                post_open_context={"post_open_surface_audit": {
                    "snapshot_xml": "<hierarchy/>", "snapshot_captured_at_monotonic": old}},
            )

    def test_wrong_identity_candidate_surface_package_activity_restriction_fail_closed(self):
        for attr, value in (("username", "wrong"), ("own", False), ("package", "other.package"),
                            ("activity", "ChallengeActivity"), ("restricted", True)):
            with self.subTest(attr=attr):
                self.device = self.make_device()
                setattr(self.device, attr, value)
                self.suspend()
                self.assertFalse(self.verify().ok)
                with self.assertRaises(continuity.UiContinuityLost):
                    self.device.click(1, 2)
                self.assertNotIn("click", self.device.calls)

    def test_sleep_during_fresh_capture_leaves_admission_closed(self):
        self.suspend()
        self.device.interrupt = True
        with self.assertRaises(continuity.UiContinuityLost):
            self.verify()
        with self.assertRaises(continuity.UiContinuityLost):
            self.device.click(1, 2)
