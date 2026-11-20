from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import follow60_ordering_v2_behavioral_canary_v1 as contract
import follow60_ordering_v2_ledger_v1 as ledger_module
import runner


ACCOUNT = "b024e94e-395d-4f02-9787-81ddc679b014"
SHA = "a" * 40
REAL_LEDGER_CLASS = ledger_module.DurableOrderingLedgerV1


def _control() -> dict:
    return {
        "schema": contract.SCHEMA,
        "control_id": "control-a",
        "account_id": ACCOUNT,
        "run_id": "run-a",
        "request_id": "request-a",
        "business_session_id": "session-a",
        "attempt_id": 1,
        "expected_worker_sha": SHA,
        "actual_worker_sha": SHA,
        "canary_type": contract.CANARY_TYPE,
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


def _xml() -> str:
    return """<hierarchy>
    <node text="alice" bounds="[0,0][1080,120]"/>
    <node text="8 posts" bounds="[0,120][300,220]"/>
    <node resource-id="profile_tabs_container" bounds="[0,700][1080,850]">
      <node resource-id="profile_tab_icon_view" content-desc="Grid view" selected="true" bounds="[0,700][360,850]"/>
    </node>
    <node class="android.widget.ImageView" resource-id="profile_grid_media_0" content-desc="Post thumbnail, row 1, column 1" bounds="[0,900][360,1260]"/>
    <node class="android.widget.ImageView" resource-id="profile_grid_media_1" content-desc="Post thumbnail, row 1, column 2" bounds="[360,900][720,1260]"/>
    <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
    </hierarchy>"""


def _mono_capture() -> dict:
    return {
        "ok": True,
        "exact_identity": True,
        "xml": _xml(),
        "package": "com.instagram.android",
        "activity": "com.instagram.mainactivity.InstagramMainActivity",
        "package_exact": True,
        "navigation_counter": 4,
        "scroll_counter": 2,
        "ui_generation": 6,
        "private_probe_payload": {
            "private_profile_detected": False,
            "probe_ms": 1.0,
        },
    }


class Follow60OrderingV2RunnerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _factory(self, scope):
        return REAL_LEDGER_CLASS(
            scope, path=Path(self.directory.name) / "ledger.sqlite3"
        )

    @patch.dict(os.environ, {contract.ENABLED_ENV: "1", contract.ALLOWLIST_ENV: ACCOUNT,
                             "WORKER_GIT_SHA": SHA, "PHONEFARM_APP_INSTANCE_ID": ""})
    def test_assigned_packages_reach_real_v2_reentry_on_consecutive_candidates(self):
        import account_session_orchestrator as session
        import follow_action_engine as actions
        import instagram_navigation as nav
        import instagram_ui_profile_v1 as ui
        from tests.test_follow60_ordering_v2_reentry_surface import _Device, _Selector

        class CandidateDevice(_Device):
            def __call__(self, **selector):
                self.selector_calls.append(dict(selector))
                return _Selector(exists="resourceIdMatches" in selector, text=self.candidate)

        for index, package in enumerate(("com.instagram.android", "com.instagram.androig",
                                         "com.instagram.other_device", "org.fixturefleet.clone7")):
            with self.subTest(package=package):
                d = CandidateDevice()
                d.package, d.serial = package, f"offline-device-{index}"
                d.activity = ".activity.MainTabActivity"
                metadata = ui.AppMetadata(package, "446.0.0.49.77", 385211304)
                binding = ui.RunUIBinding(ACCOUNT, "request-a", "run-a", "session-a", 1,
                                          f"device-{index}", d.serial, f"instance-{index}",
                                          package, metadata.version_name, metadata.version_code,
                                          "UI_PROFILE_446", "lease-a")
                runtime = dict(account_id=ACCOUNT, run_id="run-a", account_username="emitter", package=package,
                               attempt_id=1, ui_generation=7, navigation_counter=11)
                completed = []
                def simulate_target(device, *, target_id, invocation_summary, **options):
                    if target_id == "target-absent":
                        reason = "search_results_still_blank_after_recovery"
                        invocation_summary.update(follows_completed_count=0, follow_stop_reason=reason,
                            follow_session_outcome="target_unavailable_retryable", target_local_failure=True,
                            target_safe_to_skip=True, target_retryable=True, target_completed=False,
                            target_outcome="target_unavailable_retryable", first_causal_reason=reason)
                        return 7
                    for count, candidate in enumerate(("alice", "bob")):
                        device.candidate = candidate
                        ok, profile = nav._expected_profile_identity_boundary(
                            device, candidate, package, observed_username=candidate,
                        )
                        self.assertTrue(ok, profile)
                        self.assertFalse(nav._expected_profile_identity_boundary(
                            device, candidate, package, observed_username="wrong-candidate",
                        )[0])
                        observed = {"package": package, "activity": device.activity}
                        self.assertEqual(nav._post_open_intent_surface_from_final_proof(
                            observed, expected_package=package, device_context=device,
                        ), (package, device.activity))
                        self.assertEqual(nav._post_open_intent_surface_from_final_proof(
                            dict(observed, activity="foreign.activity.MainTabActivity"),
                            expected_package=package, device_context=device,
                        ), ("", ""))
                        capture = dict(_mono_capture(), package=package, activity=device.activity,
                                       xml=_xml().replace('text="alice"', f'text="{candidate}"'))
                        stages = runner._follow60_ordering_v2_prepare_candidate(
                            device, control=_control(), account_id=ACCOUNT, run_id="run-a",
                            request_id="request-a", business_session_id="session-a", attempt_id=1,
                            completed_v2_cycles=count, target_id=target_id, candidate_username=candidate,
                            action_id=f"action-{index}-{count}", mono_capture=capture, pkg=package,
                            source_profile_username="ct", pick={"username": candidate},
                            dont_follow_private_accounts=True, private_probe_payload={},
                            session_likes_used=count, commercial_policy_revision="policy-a",
                            event_recorder=lambda **kw: {"ok": True, "v2_complete_count": count},
                        )
                        self.assertTrue(stages["selected"], stages["reason"])
                        self.assertFalse(stages["abort_candidate"], stages["reason"])
                        self.assertIn("ordering_v2_reentry", stages["pre_follow_context"])
                        ledger = stages["ledger"]
                        self.assertIn("profile_reentry_verified", ledger.load().stages)
                        self.assertFalse(ledger.load().cycle_complete)
                        # Simulated transport receipts exercise the production ledger in order.
                        ledger.apply_receipt("follow_pending", {
                            "candidate_username": candidate,
                            "deferred_intent_nonce": stages["plan"].deferred_follow.intent_nonce,
                            "fresh_reentry_proof": True,
                        })
                        ledger.apply_receipt("follow_verified", {
                            "candidate_username": candidate, "follow_state_after": "following",
                            "tap_sent": True,
                        })
                        for stage in ("mute_posts_verified", "mute_stories_verified"):
                            ledger.apply_receipt(stage, {"verified": True, "engine": "mute_engine_v2"})
                        ledger.apply_receipt("return_ct_exact", {"verified": True, "method": "fixture"})
                        receipt = ledger.apply_receipt("cycle_complete", {
                            "critical_persistence_acknowledged": True, "candidate_username": candidate,
                        })
                        self.assertTrue(ledger.load().cycle_complete)
                        completed.append(receipt["idempotency_key"])
                    invocation_summary.update(follows_completed_count=len(completed), follow_processed_count=2,
                        follow_session_outcome="global_follow_cap_reached", follow_stop_reason="global_follow_cap_reached")
                    return 0

                with patch.object(ui, "_SESSION", None), patch.object(nav.config, "INSTAGRAM_PACKAGE", package), \
                        patch("follow_60s_canary.runtime_context", return_value=runtime), \
                        patch("follow_60s_canary.invalidate"), \
                        patch.object(runner, "_run_followers_list_engine_session", side_effect=simulate_target), \
                        patch.object(runner, "run_post_follow_post_likes_phase", return_value={
                            "ok": True, "post_opened": True, "phase_outcome": "success",
                            "liked_count": 1, "attempted_count": 1,
                        }) as like, \
                        patch.object(actions, "try_select_exact_profile_header_follow_fast", return_value=(
                            object(), {"bounds": {"left": 700, "top": 300, "right": 1010, "bottom": 420},
                                       "resource_id": f"{package}:id/profile_header_follow_button", "text": "Follow"},
                        )), patch.object(ledger_module, "DurableOrderingLedgerV1", side_effect=self._factory):
                    ui.arm(binding, lambda: metadata)
                    ui.attach(d)
                    targets = [{"target_id": "target-absent", "source_profile": "ct_missing"},
                               {"target_id": "target-a", "source_profile": "ct"}]
                    result = session._run_follow_target_rotation(
                        d, account_id=ACCOUNT, account_username="emitter", run_id="run-a",
                        run_request_id="request-a", follow_targets=targets,
                        run_followers_list_engine_session=runner._bind_verified_follow_runner("emitter"),
                        supabase_mode=False, warm_session_used=False, force_stop_used=False,
                        max_targets_per_run=2, authorized_follow_quota=2,
                        follow60_canary_active=True, follow60_canary_control=_control(),
                        business_session_id="session-a",
                    )
                    self.assertEqual(result["global_follows_completed"], 2)
                    self.assertEqual(len(set(completed)), 2)
                    self.assertEqual(like.call_count, 2)
                    self.assertIsNone(nav._follow_446_activity_authority(d, package, d.activity))

    @patch.dict(
        os.environ,
        {
            contract.ENABLED_ENV: "1",
            contract.ALLOWLIST_ENV: ACCOUNT,
            "WORKER_GIT_SHA": SHA,
        },
        clear=False,
    )
    @patch("runner.run_post_follow_post_likes_phase")
    def test_authoritative_barrier_blocks_before_post_action(self, post_like) -> None:
        events: list[str] = []

        def recorder(**kwargs):
            events.append(kwargs["event_kind"])
            return {
                "ok": False,
                "reason": "v2_cycle_barrier_reached",
                "barrier_reached": True,
                "v2_complete_count": 10,
            }

        result = runner._follow60_ordering_v2_prepare_candidate(
            object(), control=_control(), account_id=ACCOUNT,
            run_id="run-a", request_id="request-a",
            business_session_id="session-a", attempt_id=1,
            completed_v2_cycles=0, target_id="target-a",
            candidate_username="alice", action_id="action-a",
            mono_capture=_mono_capture(),
            pkg="com.instagram.android", source_profile_username="ct",
            pick={"username": "alice"}, dont_follow_private_accounts=True,
            private_probe_payload={"private_profile_detected": False, "probe_ms": 1.0},
            session_likes_used=0, commercial_policy_revision="policy-a",
            event_recorder=recorder,
        )
        self.assertTrue(result["barrier_reached"])
        self.assertEqual(["candidate_seen"], events)
        post_like.assert_not_called()

    @patch.dict(
        os.environ,
        {
            contract.ENABLED_ENV: "1",
            contract.ALLOWLIST_ENV: ACCOUNT,
            "WORKER_GIT_SHA": SHA,
        },
        clear=False,
    )
    def test_v1_fallback_is_counted_separately_from_v2_complete(self) -> None:
        events: list[str] = []

        def recorder(**kwargs):
            events.append(kwargs["event_kind"])
            return {"ok": True, "v2_complete_count": 0, "v1_fallback_count": 1}

        result = runner._follow60_ordering_v2_prepare_candidate(
            object(), control=_control(), account_id=ACCOUNT,
            run_id="run-a", request_id="request-a",
            business_session_id="session-a", attempt_id=1,
            completed_v2_cycles=0, target_id="target-a",
            candidate_username="alice", action_id="action-a",
            mono_capture={"ok": True, "exact_identity": True, "xml": "<hierarchy/>"},
            pkg="com.instagram.android", source_profile_username="ct",
            pick={"username": "alice"}, dont_follow_private_accounts=True,
            private_probe_payload={"private_profile_detected": False, "probe_ms": 1.0},
            session_likes_used=0, commercial_policy_revision="policy-a",
            event_recorder=recorder,
        )
        self.assertFalse(result["selected"])
        self.assertEqual(["candidate_seen", "v1_fallback"], events)

    @patch.dict(
        os.environ,
        {
            contract.ENABLED_ENV: "1",
            contract.ALLOWLIST_ENV: ACCOUNT,
            "WORKER_GIT_SHA": SHA,
        },
        clear=False,
    )
    @patch("follow60_ordering_v2_ledger_v1.DurableOrderingLedgerV1")
    @patch("follow_action_engine.capture_ordering_v2_profile_reentry_follow_surface")
    @patch("runner.run_post_follow_post_likes_phase")
    def test_direct_grid_safe_runs_post_first_once_and_returns_fresh_follow_context(
        self, post_like, capture_reentry, durable_class
    ) -> None:
        durable_class.side_effect = self._factory
        post_like.return_value = {
            "ok": True,
            "post_opened": True,
            "phase_outcome": "success",
            "liked_count": 1,
            "attempted_count": 1,
        }
        capture_reentry.return_value = {
            "ok": True,
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
            "surface": "candidate_profile",
            "candidate_username": "alice",
            "action_bar_title": "alice",
            "cta_state": "follow",
            "cta_bounds": {"left": 700, "top": 300, "right": 1010, "bottom": 420},
            "resource_id": "com.instagram.android:id/profile_header_follow_button",
            "text": "Follow",
            "overlay_or_challenge": False,
            "navigation_generation": "ui:7",
            "ui_generation": 7,
            "captured_at_monotonic": time.monotonic() + 1,
        }
        result = runner._follow60_ordering_v2_prepare_candidate(
            object(), control=_control(), account_id=ACCOUNT,
            run_id="run-a", request_id="request-a",
            business_session_id="session-a", attempt_id=1,
            completed_v2_cycles=0, target_id="target-a",
            candidate_username="alice", action_id="action-a",
            mono_capture=_mono_capture(),
            pkg="com.instagram.android", source_profile_username="ct",
            pick={"username": "alice"}, dont_follow_private_accounts=True,
            private_probe_payload={"private_profile_detected": False, "probe_ms": 1.0},
            session_likes_used=0, commercial_policy_revision="policy-a",
            event_recorder=lambda **kwargs: {"ok": True, "reason": "recorded", "v2_complete_count": 0},
        )
        self.assertTrue(result["selected"])
        self.assertFalse(result["abort_candidate"])
        self.assertEqual(1, post_like.call_count)
        self.assertFalse(post_like.call_args.kwargs["follow_success_verified"])
        self.assertIn("ordering_v2_reentry", result["pre_follow_context"])
        self.assertIn("like_verified", result["ledger"].load().stages)

    @patch.dict(
        os.environ,
        {
            contract.ENABLED_ENV: "1",
            contract.ALLOWLIST_ENV: ACCOUNT,
            "WORKER_GIT_SHA": SHA,
        },
        clear=False,
    )
    @patch("follow60_ordering_v2_ledger_v1.DurableOrderingLedgerV1")
    @patch("runner.run_post_follow_post_likes_phase")
    def test_failure_before_post_falls_back_but_after_post_fails_closed(
        self, post_like, durable_class
    ) -> None:
        durable_class.side_effect = self._factory
        base = dict(
            d=object(), control=_control(), account_id=ACCOUNT,
            run_id="run-a", request_id="request-a",
            business_session_id="session-a", attempt_id=1,
            completed_v2_cycles=0, target_id="target-a",
            candidate_username="alice", action_id="action-a",
            mono_capture=_mono_capture(),
            pkg="com.instagram.android", source_profile_username="ct",
            pick={"username": "alice"}, dont_follow_private_accounts=True,
            private_probe_payload={"private_profile_detected": False, "probe_ms": 1.0},
            session_likes_used=0, commercial_policy_revision="policy-a",
            event_recorder=lambda **kwargs: {"ok": True, "reason": "recorded", "v2_complete_count": 0},
        )
        post_like.return_value = {
            "ok": False, "post_opened": False, "attempted_count": 0,
            "reason": "intent_rejected_before_tap",
        }
        before = runner._follow60_ordering_v2_prepare_candidate(**base)
        self.assertFalse(before["selected"])
        self.assertFalse(before["abort_candidate"])

        base["action_id"] = "action-b"
        post_like.return_value = {
            "ok": False, "post_opened": True, "attempted_count": 1,
            "reason": "v5_rejected_without_safe_skip",
        }
        after = runner._follow60_ordering_v2_prepare_candidate(**base)
        self.assertFalse(after["selected"])
        self.assertTrue(after["abort_candidate"])


if __name__ == "__main__":
    unittest.main()
