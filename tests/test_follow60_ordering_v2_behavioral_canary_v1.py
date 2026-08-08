from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import follow60_ordering_v2_behavioral_canary_v1 as v2
from follow60_ordering_v2_ledger_v1 import (
    DurableOrderingLedgerV1,
    LedgerScope,
    clear_active_store,
    record_stop_for_run,
    register_active_store,
)


ACCOUNT = "b024e94e-395d-4f02-9787-81ddc679b014"
SHA = "a" * 40


def _env(account: str = ACCOUNT) -> dict[str, str]:
    return {
        v2.ENABLED_ENV: "1",
        v2.ALLOWLIST_ENV: account,
    }


def _control(**overrides):
    out = {
        "schema": v2.SCHEMA,
        "control_id": "control-a",
        "account_id": ACCOUNT,
        "run_id": "run-a",
        "request_id": "request-a",
        "business_session_id": "session-a",
        "attempt_id": 1,
        "expected_worker_sha": SHA,
        "actual_worker_sha": SHA,
        "canary_type": v2.CANARY_TYPE,
        "max_new_cycles": 10,
        "baseline_follow_count": 0,
        "expires_at_epoch_s": time.time() + 3600,
        "lease_id": "lease-a",
        "lease_nonce": "nonce-a",
        "lease_expires_at_epoch_s": time.time() + 3600,
        "claimed_at_epoch_s": time.time(),
        "v2_complete_count": 0,
        "status": "running",
    }
    out.update(overrides)
    return out


def _binding(control=None, **overrides):
    args = {
        "account_id": ACCOUNT,
        "run_id": "run-a",
        "request_id": "request-a",
        "business_session_id": "session-a",
        "attempt_id": 1,
        "worker_sha": SHA,
        "completed_v2_cycles": 0,
        "environ": _env(),
    }
    args.update(overrides)
    return v2.validate_behavioral_canary_binding(control or _control(), **args)


def _xml(candidate="alice"):
    return f"""<hierarchy>
    <node text="{candidate}" bounds="[0,0][1080,120]"/>
    <node text="8 posts" bounds="[0,120][300,220]"/>
    <node resource-id="profile_tabs_container" bounds="[0,700][1080,850]">
      <node resource-id="profile_tab_icon_view" content-desc="Grid view"
            selected="true" bounds="[0,700][360,850]"/>
    </node>
    <node class="android.widget.ImageView" resource-id="profile_grid_media_0"
          content-desc="Post thumbnail, row 1, column 1" bounds="[0,900][360,1260]"/>
    <node class="android.widget.ImageView" resource-id="profile_grid_media_1"
          content-desc="Post thumbnail, row 1, column 2" bounds="[360,900][720,1260]"/>
    <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
    </hierarchy>"""


def _capture(xml=None):
    return {
        "ok": True,
        "xml": xml or _xml(),
        "xml_fingerprint": "fixture",
        "exact_identity": True,
        "profile_surface": True,
        "follow_cta_positive": True,
        "private_probe_payload": {
            "private_profile_detected": False,
            "detection_method": "existing_mono_xml",
            "probe_ms": 1.0,
        },
    }


def _business(**overrides):
    out = {
        "filter_passed": True,
        "filter_reason": "passed",
        "eligibility_passed": True,
        "eligibility_reason": "passed",
        "follow_budget_available": True,
    }
    out.update(overrides)
    return out


def _stable(binding):
    proof, reason = v2.build_stable_candidate_proof_v2(
        binding=binding,
        mono_capture=_capture(),
        business_evidence=_business(),
        target_id="target-a",
        candidate_username="alice",
        action_id="action-a",
        binding_kind="mainline",
    )
    assert proof is not None, reason
    return proof


class BehavioralRouterTests(unittest.TestCase):
    def test_valid_rex_binding_and_direct_grid_select_post_first(self):
        binding, reason = _binding()
        self.assertIsNotNone(binding, reason)
        proof = _stable(binding)
        self.assertEqual(
            ("POST_FIRST_V2", "v2_binding_and_direct_grid_safe"),
            v2.route_candidate_v2(binding=binding, stable_proof=proof, completed_v2_cycles=0),
        )

    def test_disabled_wrong_account_wrong_sha_expired_and_barrier_route_v1(self):
        cases = (
            ({"environ": {}}, "v2_behavioral_disabled"),
            ({"account_id": "other", "environ": _env("other")}, "v2_allowlist_must_contain_exactly_one_valid_account"),
            ({"worker_sha": "b" * 40}, "v2_expected_worker_sha_mismatch"),
            ({"control": _control(expires_at_epoch_s=1)}, "v2_control_expired"),
            ({"control": _control(v2_complete_count=10), "completed_v2_cycles": 9}, "v2_complete_count_not_authoritative"),
        )
        for values, expected in cases:
            control = values.pop("control", _control())
            with self.subTest(expected=expected):
                binding, reason = _binding(control, **values)
                self.assertIsNone(binding)
                self.assertEqual(expected, reason)

    def test_no_binding_and_future_account_never_inherit_v2(self):
        binding, reason = _binding(control={"schema": "missing"})
        self.assertIsNone(binding)
        self.assertEqual("v2_control_schema_invalid", reason)
        self.assertEqual(
            ("FOLLOW60_V1", "v2_binding_absent_or_invalid"),
            v2.route_candidate_v2(
                binding=None, stable_proof=None, completed_v2_cycles=0
            ),
        )
        binding, reason = _binding(
            _control(account_id="future-account"),
            account_id="future-account",
            environ=_env("future-account"),
        )
        self.assertIsNone(binding)
        self.assertEqual("v2_allowlist_must_contain_exactly_one_valid_account", reason)

    def test_invalid_profiles_never_enter_v2(self):
        binding, _ = _binding()
        cases = (
            (_capture(_xml().replace("8 posts", "0 posts").replace(
                '<node class="android.widget.ImageView" resource-id="profile_grid_media_0"',
                '<node text="No posts yet"/><node class="android.widget.ImageView" resource-id="x"',
            )), _business(), "v2_candidate_not_direct_grid_safe"),
            (_capture(), _business(filter_passed=False), "v2_filter_not_passed"),
            (_capture(), _business(eligibility_passed=False), "v2_eligibility_not_passed"),
            (_capture(), _business(follow_budget_available=False), "v2_follow_budget_unavailable"),
            ({**_capture(), "private_probe_payload": {"private_profile_detected": True}}, _business(), "v2_private_profile"),
            (_capture(_xml().replace("Grid view", "Reels")), _business(), "v2_candidate_not_direct_grid_safe"),
            (_capture(_xml().replace("Grid view", "Tagged")), _business(), "v2_candidate_not_direct_grid_safe"),
            (_capture(_xml().replace('selected="true"', 'selected="false"')), _business(), "v2_candidate_not_direct_grid_safe"),
            (_capture(_xml().replace('[0,900][360,1260]', '[0,2200][360,2500]').replace('[360,900][720,1260]', '[360,2200][720,2500]')), _business(), "v2_candidate_not_direct_grid_safe"),
            (_capture(_xml().replace("<node resource-id=\"bottom_navigation\"", "<node text=\"Suggested for you\" bounds=\"[0,850][1080,1200]\"/><node resource-id=\"bottom_navigation\"")), _business(), "v2_candidate_not_direct_grid_safe"),
            (_capture(_xml().replace("<node resource-id=\"bottom_navigation\"", "<node text=\"Story Highlights\" bounds=\"[0,850][1080,1200]\"/><node resource-id=\"bottom_navigation\"")), _business(), "v2_candidate_not_direct_grid_safe"),
        )
        for capture, business, expected in cases:
            with self.subTest(expected=expected):
                proof, reason = v2.build_stable_candidate_proof_v2(
                    binding=binding,
                    mono_capture=capture,
                    business_evidence=business,
                    target_id="target-a",
                    candidate_username="alice",
                    action_id="action-a",
                    binding_kind="mainline",
                )
                self.assertIsNone(proof)
                self.assertEqual(expected, reason)


class DeferredAndReentryTests(unittest.TestCase):
    def setUp(self):
        self.binding, _ = _binding()
        self.stable = _stable(self.binding)
        self.deferred = v2.create_deferred_follow_intent_v2(
            self.stable, initial_cta_identity="follow"
        )

    def _live(self, **overrides):
        out = {
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
            "surface": "candidate_profile",
            "candidate_username": "alice",
            "action_bar_title": "alice",
            "cta_state": "follow",
            "cta_bounds": {"left": 700, "top": 300, "right": 1010, "bottom": 420},
            "overlay_or_challenge": False,
            "navigation_generation": "nav-after-back:4",
            "ui_generation": 4,
            "captured_at_monotonic": self.deferred.created_at_monotonic + 0.1,
        }
        out.update(overrides)
        return out

    def test_level1_reentry_creates_fresh_follow_context_and_never_uses_old_bounds(self):
        proof, reason = v2.build_profile_reentry_proof_v2(
            self.deferred,
            self.stable,
            live=self._live(),
            expected_package="com.instagram.android",
        )
        self.assertIsNotNone(proof, reason)
        ctx = v2.build_fresh_follow_tap_context_v2(
            proof,
            source_profile_username="ct",
            visual_candidate_id="action-a",
            private_probe_payload=_capture()["private_probe_payload"],
        )
        self.assertEqual(proof.cta_bounds, ctx["fresh_cta_bounds"])
        self.assertTrue(self.deferred.pre_post_bounds_invalidated)
        self.assertNotIn("post_grid_evidence", ctx)

    def test_wrong_profile_stale_or_missing_cta_and_challenge_fail_closed(self):
        cases = (
            ({"candidate_username": "mallory", "action_bar_title": "mallory"}, "v2_reentry_wrong_profile"),
            ({"cta_state": "following"}, "v2_reentry_follow_cta_missing"),
            ({"cta_bounds": {}}, "v2_reentry_follow_bounds_missing"),
            ({"overlay_or_challenge": True}, "v2_reentry_overlay_or_challenge"),
            ({"captured_at_monotonic": self.deferred.created_at_monotonic - 1}, "v2_reentry_not_fresh_after_post"),
            ({"navigation_generation": ""}, "v2_reentry_generation_missing"),
        )
        for changes, expected in cases:
            with self.subTest(expected=expected):
                proof, reason = v2.build_profile_reentry_proof_v2(
                    self.deferred,
                    self.stable,
                    live=self._live(**changes),
                    expected_package="com.instagram.android",
                )
                self.assertIsNone(proof)
                self.assertEqual(expected, reason)


class OrchestratorAndDurableReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        binding, _ = _binding()
        stable = _stable(binding)
        deferred = v2.create_deferred_follow_intent_v2(stable, initial_cta_identity="follow")
        self.plan = v2.CandidateCyclePlanV2(
            selected_path="POST_FIRST_V2",
            binding=binding,
            stable_proof=stable,
            deferred_follow=deferred,
            started_at_monotonic=time.monotonic(),
        )
        scope = LedgerScope(
            account_id=ACCOUNT,
            run_id="run-a",
            request_id="request-a",
            business_session_id="session-a",
            target_id="target-a",
            action_id="action-a",
            candidate_username="alice",
        )
        self.store = DurableOrderingLedgerV1(
            scope, path=Path(self.directory.name) / "ledger.sqlite3"
        )
        self.orchestrator = v2.Follow60OrderingV2OrchestratorV1(
            ledger_apply=self.store.apply_receipt
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_like_before_follow_is_durable_and_never_replayed(self):
        self.orchestrator.begin(self.plan)
        result = self.orchestrator.execute_post_first(
            self.plan,
            post_like_engine=lambda _ctx: {
                "post_opened": True,
                "liked_count": 1,
                "like_skipped_safely": False,
            },
        )
        self.assertTrue(result["ok"])
        loaded = self.store.load()
        self.assertIn("like_verified", loaded.stages)
        self.assertFalse(self.store.replay_plan()["like_replay_allowed"])
        second = self.store.apply_receipt("like_verified", {"liked_count": 1})
        self.assertTrue(second["duplicate"])

    def test_v5_reject_is_safe_skip_not_fake_like(self):
        self.orchestrator.begin(self.plan)
        out = self.orchestrator.execute_post_first(
            self.plan,
            post_like_engine=lambda _ctx: {
                "post_opened": True,
                "liked_count": 0,
                "like_skipped_safely": True,
                "skipped_reason": "v5_rejected_story_or_highlight",
            },
        )
        self.assertTrue(out["ok"])
        self.assertIn("like_skipped", self.store.load().stages)
        self.assertNotIn("like_verified", self.store.load().stages)

    def test_follow_failure_after_like_stays_incomplete(self):
        self.orchestrator.begin(self.plan)
        self.orchestrator.execute_post_first(
            self.plan,
            post_like_engine=lambda _ctx: {"post_opened": True, "liked_count": 1},
        )
        for stage, payload in (
            ("profile_reentry_verified", {"exact": True}),
            ("follow_pending", {"reserved": True}),
            ("follow_failed", {"reason": "ack_missing"}),
        ):
            self.store.apply_receipt(stage, payload)
        state = self.store.load()
        self.assertFalse(state.cycle_complete)
        self.assertEqual("follow_failed_terminal", state.next_stage())
        self.assertFalse(state.replay_plan()["like_replay_allowed"])

    def test_stop_at_every_boundary_never_invents_an_ack(self):
        stages_in_order = (
            ("profile_certified", {"exact": True}),
            ("post_opened", {"v5": True}),
            ("like_verified", {"verified": True}),
            ("profile_reentry_verified", {"exact": True}),
            ("follow_pending", {"reserved": True}),
            ("follow_verified", {"verified": True}),
            ("mute_posts_verified", {"verified": True}),
            ("mute_stories_verified", {"verified": True}),
            ("return_ct_exact", {"verified": True}),
        )
        acknowledged_prefix_lengths = (0, 1, 2, 3, 3, 4, 4, 5, 6, 7, 8, 8)
        for index, prefix_length in enumerate(acknowledged_prefix_lengths):
            scope = LedgerScope(
                account_id=ACCOUNT, run_id=f"run-{index}", request_id=f"request-{index}",
                business_session_id="session", target_id="target",
                action_id=f"action-{index}", candidate_username=f"candidate-{index}",
            )
            store = DurableOrderingLedgerV1(
                scope, path=Path(self.directory.name) / f"stop-{index}.sqlite3"
            )
            for stage, payload in stages_in_order[:prefix_length]:
                store.apply_receipt(stage, payload)
            store.apply_receipt("stop_recorded", {"source": "operator"})
            loaded = store.load()
            self.assertFalse(loaded.cycle_complete)
            expected = {stage for stage, _payload in stages_in_order[:prefix_length]}
            self.assertEqual(expected | {"stop_recorded"}, loaded.stages)
            self.assertNotIn("cycle_complete", loaded.stages)

    def test_runner_stop_registry_records_only_real_inflight_stages(self):
        scope = LedgerScope(
            account_id=ACCOUNT, run_id="run-stop", request_id="request-stop",
            business_session_id="session", target_id="target",
            action_id="action-stop", candidate_username="alice",
        )
        store = DurableOrderingLedgerV1(
            scope, path=Path(self.directory.name) / "registry-stop.sqlite3"
        )
        store.apply_receipt("profile_certified", {"exact": True})
        store.apply_receipt("post_opened", {"v5": True})
        store.apply_receipt("like_verified", {"verified": True})
        register_active_store(store)
        try:
            result = record_stop_for_run(
                account_id=ACCOUNT, run_id="run-stop", reason="operator_stop"
            )
            self.assertTrue(result["ok"])
            self.assertEqual(1, result["recorded"])
            loaded = store.load()
            self.assertEqual(
                {"profile_certified", "post_opened", "like_verified", "stop_recorded"},
                loaded.stages,
            )
            self.assertFalse(loaded.cycle_complete)
        finally:
            clear_active_store(store)


if __name__ == "__main__":
    unittest.main()
