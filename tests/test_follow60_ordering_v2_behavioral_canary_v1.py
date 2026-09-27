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
        "package": "com.instagram.android",
        "activity": "com.instagram.mainactivity.InstagramMainActivity",
        "package_exact": True,
        "navigation_counter": 4,
        "scroll_counter": 2,
        "ui_generation": 6,
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
            )), _business(), "v2_posts_count_not_positive"),
            (_capture(), _business(filter_passed=False), "v2_filter_not_passed"),
            (_capture(), _business(eligibility_passed=False), "v2_eligibility_not_passed"),
            (_capture(), _business(follow_budget_available=False), "v2_follow_budget_unavailable"),
            ({**_capture(), "private_probe_payload": {"private_profile_detected": True}}, _business(), "v2_private_profile"),
            (_capture(_xml().replace("Grid view", "Reels")), _business(), "v2_reels_tab_selected"),
            (_capture(_xml().replace("Grid view", "Tagged")), _business(), "v2_tagged_tab_selected"),
            (_capture(_xml().replace('selected="true"', 'selected="false"')), _business(), "v2_posts_tab_identity_not_exact"),
            (_capture(_xml().replace('[0,900][360,1260]', '[0,2200][360,2500]').replace('[360,900][720,1260]', '[360,2200][720,2500]')), _business(), "v2_absolute_top_left_not_fully_visible"),
            (_capture(_xml().replace("<node resource-id=\"bottom_navigation\"", "<node text=\"Suggested for you\" bounds=\"[0,850][1080,1200]\"/><node resource-id=\"bottom_navigation\"")), _business(), "v2_suggested_region_overlaps_post_grid"),
            (_capture(_xml().replace("<node resource-id=\"bottom_navigation\"", "<node text=\"Story Highlights\" bounds=\"[0,850][1080,1200]\"/><node resource-id=\"bottom_navigation\"")), _business(), "v2_highlights_region_overlaps_post_grid"),
            ({**_capture(), "package_exact": False}, _business(), "v2_profile_surface_package_activity_not_exact"),
            ({key: value for key, value in _capture().items() if key != "ui_generation"}, _business(), "v2_profile_surface_generation_missing"),
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

    def test_highlights_or_suggested_presence_before_posts_tab_does_not_block_v2(self):
        binding, _ = _binding()
        for marker in (
            '<node text="Story Highlights" bounds="[0,300][1080,600]"/>',
            '<node text="Suggested for you" bounds="[0,300][1080,600]"/>',
        ):
            with self.subTest(marker=marker):
                proof, reason = v2.build_stable_candidate_proof_v2(
                    binding=binding,
                    mono_capture=_capture(_xml().replace(
                        '<node resource-id="profile_tabs_container"',
                        marker + '<node resource-id="profile_tabs_container"',
                    )),
                    business_evidence=_business(),
                    target_id="target-a",
                    candidate_username="alice",
                    action_id="action-a",
                    binding_kind="mainline",
                )
                self.assertIsNotNone(proof, reason)
                self.assertTrue(proof.direct_grid_safe)

    def test_partial_grid_still_uses_v2_when_absolute_top_left_is_fully_visible(self):
        binding, _ = _binding()
        partial_lower_row = (
            '<node class="android.widget.ImageView" '
            'resource-id="profile_grid_media_2" '
            'content-desc="Post thumbnail, row 2, column 1" '
            'bounds="[0,2090][360,2380]"/>'
        )
        proof, reason = v2.build_stable_candidate_proof_v2(
            binding=binding,
            mono_capture=_capture(
                _xml().replace(
                    '<node resource-id="bottom_navigation"',
                    partial_lower_row + '<node resource-id="bottom_navigation"',
                )
            ),
            business_evidence=_business(),
            target_id="target-a",
            candidate_username="alice",
            action_id="action-a",
            binding_kind="mainline",
        )
        self.assertIsNotNone(proof, reason)
        self.assertTrue(proof.direct_grid_safe)
        top_left = [
            cell
            for cell in proof.post_grid_evidence["physical_cells"]
            if cell.get("absolute_row_index") == 1
            and cell.get("absolute_column_index") == 1
        ]
        self.assertEqual(1, len(top_left))

    def test_zero_is_a_valid_current_generation_when_transport_is_explicit(self):
        binding, _ = _binding()
        capture = {
            **_capture(),
            "navigation_counter": 0,
            "scroll_counter": 0,
            "ui_generation": 0,
        }
        proof, reason = v2.build_stable_candidate_proof_v2(
            binding=binding,
            mono_capture=capture,
            business_evidence=_business(),
            target_id="target-a",
            candidate_username="alice",
            action_id="action-a",
            binding_kind="mainline",
        )
        self.assertIsNotNone(proof, reason)
        self.assertEqual(0, proof.post_grid_evidence["proof_ui_generation"])

    def test_stable_proof_transports_existing_surface_without_new_acquisition(self):
        binding, _ = _binding()
        proof = _stable(binding)
        evidence = proof.post_grid_evidence
        self.assertEqual(evidence["final_proof_package"], "com.instagram.android")
        self.assertEqual(
            evidence["final_proof_activity"],
            "com.instagram.mainactivity.InstagramMainActivity",
        )
        self.assertTrue(evidence.get("coordinate_frame"))
        self.assertEqual(evidence["proof_navigation_counter"], 4)
        self.assertEqual(evidence["proof_scroll_counter"], 2)
        self.assertEqual(evidence["proof_ui_generation"], 6)


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




class Exact446ActivityRoutingTests(unittest.TestCase):
    BASE = "eefa841ff02a28838de92f8fd6fe7fb3f929138f"

    @classmethod
    def setUpClass(cls):
        import ast
        import subprocess
        cls.root = Path(v2.__file__).parent
        cls.old_source = subprocess.check_output(
            ["git", "show", cls.BASE + ":follow60_ordering_v2_behavioral_canary_v1.py"],
            cwd=cls.root, text=True)
        tree = ast.parse(cls.old_source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "build_stable_candidate_proof_v2")
        namespace = dict(vars(v2))
        exec(compile(ast.Module(body=[node], type_ignores=[]), "signed-baseline", "exec"), namespace)
        cls.old_builder = staticmethod(namespace[node.name])

    def device(self, version=("446.0.0.49.77", 385211304), package="com.instagram.android"):
        from instagram_ui_446_compat import retain_context
        class NoIO:
            serial = "RFGL62N646V"
            def __getattr__(self, name):
                raise AssertionError("unexpected device I/O: " + name)
        d = NoIO()
        d._instagram_search_observed_version = (id(d), d.serial, package, *version)
        retain_context(d, serial=d.serial, package=package,
                       version_name=version[0], version_code=version[1], unsafe=False)
        return d

    def args(self, activity=".activity.MainTabActivity", **changes):
        binding, _ = _binding()
        return dict(binding=binding, mono_capture={**_capture(), "activity": activity, **changes},
                    business_evidence=_business(), target_id="target-a", candidate_username="alice",
                    action_id="action-a", binding_kind="mainline")

    def outcome(self, builder, args, **kw):
        proof, reason = builder(**args, **kw)
        route = v2.route_candidate_v2(binding=args["binding"], stable_proof=proof, completed_v2_cycles=0)
        return proof is not None, reason, route

    def test_exact_version_package_activity_matrix(self):
        main = "com.instagram.mainactivity.InstagramMainActivity"
        full = "com.instagram.android.activity.MainTabActivity"
        for version, package, activity, expected in (
            (("372.0.0.48.60",377611059),"com.instagram.androif",main,True),
            (("446.0.0.49.77",385211304),"com.instagram.android",main,True),
            (("446.0.0.49.77",385211304),"com.instagram.android",full,True),
            (("446.0.0.49.77",385211304),"com.instagram.android",".activity.MainTabActivity",True),
            (("372.0.0.48.60",377611059),"com.instagram.android",full,False),
            (("446.0.0.49.77",385211304),"com.instagram.androif",full,False),
            (("446.0.0.49.77",385211304),"foreign.package",full,False),
            (("446.0.0.49.77",385211304),"com.instagram.android",full+"Overlay",False),
        ):
            with self.subTest(version=version,package=package,activity=activity):
                out=self.outcome(v2.build_stable_candidate_proof_v2,self.args(activity,package=package),
                                 device_context=self.device(version,package))
                self.assertEqual(out[0],expected)
                self.assertEqual(out[2][0],"POST_FIRST_V2" if expected else "FOLLOW60_V1")

    def test_missing_wrong_stale_or_forged_context_rejected(self):
        def stale_owner(d):
            other=type(d)()
            vars(other).update(vars(d))
            return other
        mutations = [lambda d: None, lambda d: object(),
            lambda d: (vars(d).pop("_instagram_search_observed_version"),d)[1],
            lambda d: (vars(d).__setitem__("_instagram_446_observed_context",None),d)[1],
            lambda d: (vars(d).__setitem__("serial","other"),d)[1],
            lambda d: (vars(d).__setitem__("_instagram_search_observed_version",(id(d),d.serial,"com.instagram.android","446.0.0.49.77",True)),d)[1],
            stale_owner]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                out=self.outcome(v2.build_stable_candidate_proof_v2,self.args(),device_context=mutate(self.device()))
                self.assertFalse(out[0]);self.assertEqual(out[1],"v2_profile_surface_package_activity_not_exact")
        for version in [("445",385211304),("446.0.0.49.77",385211305)]:
            self.assertFalse(self.outcome(v2.build_stable_candidate_proof_v2,self.args(),device_context=self.device(version))[0])
        self.assertFalse(self.outcome(v2.build_stable_candidate_proof_v2,self.args(package_exact=False),device_context=self.device())[0])

    def test_existing_timing_proxy_and_wrong_proxy(self):
        from run_latency_observability_v1 import _TimedProxy
        d=self.device();proxy=_TimedProxy(d,kind="device",emit=None,methods=frozenset())
        self.assertTrue(self.outcome(v2.build_stable_candidate_proof_v2,self.args(),device_context=proxy)[0])
        for bad in [_TimedProxy(d,kind="uiautomator",emit=None,methods=frozenset()),
                    _TimedProxy(proxy,kind="device",emit=None,methods=frozenset())]:
            self.assertFalse(self.outcome(v2.build_stable_candidate_proof_v2,self.args(),device_context=bad)[0])

    def test_372_and_historical_activity_differential(self):
        for activity in ["com.instagram.mainactivity.InstagramMainActivity","instagram.MainActivity",
                         ".activity.MainTabActivity","com.instagram.android.activity.MainTabActivity",
                         "", "foreign.Activity", "challenge"]:
            for package in ["com.instagram.android","com.instagram.androif",""]:
                args=self.args(activity,package=package)
                self.assertEqual(self.outcome(self.old_builder,args),self.outcome(
                    v2.build_stable_candidate_proof_v2,args,device_context=self.device(("372.0.0.48.60",377611059),package)))

    def test_legitimate_fallbacks_identical_for_both_activities(self):
        mutations=[lambda a:a["mono_capture"].update(exact_identity=False),
                   lambda a:a["mono_capture"].update(private_probe_payload={"private_profile_detected":True}),
                   lambda a:a["business_evidence"].update(filter_passed=False),
                   lambda a:a["business_evidence"].update(eligibility_passed=False),
                   lambda a:a["business_evidence"].update(follow_budget_available=False),
                   lambda a:a["mono_capture"].pop("ui_generation"),
                   lambda a:a["mono_capture"].update(xml=_xml().replace("Grid view","Reels")),
                   lambda a:a["mono_capture"].update(xml=_xml().replace("8 posts","0 posts")),
                   lambda a:a["mono_capture"].update(xml=_xml().replace('[0,900][360,1260]','[0,2200][360,2500]'))]
        for mutate in mutations:
            old=self.args("com.instagram.mainactivity.InstagramMainActivity");new=self.args();mutate(old);mutate(new)
            expected=self.outcome(self.old_builder,old)
            self.assertFalse(expected[0]);self.assertEqual(expected[2][0],"FOLLOW60_V1")
            self.assertEqual(expected,self.outcome(v2.build_stable_candidate_proof_v2,new,device_context=self.device()))

    def test_real_runner_call_passes_context_to_real_builder(self):
        import ast
        tree=ast.parse((self.root/"runner.py").read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_follow60_ordering_v2_prepare_candidate")
        calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
               and n.func.id=="build_stable_candidate_proof_v2"]
        self.assertEqual(len(calls),1)
        args=self.args();d=self.device();env={**args,"d":d,"_mainline_mode":True,
            "build_stable_candidate_proof_v2":v2.build_stable_candidate_proof_v2}
        proof,reason=eval(compile(ast.Expression(calls[0]),"real-runner-call","eval"),env)
        self.assertIsNotNone(proof,reason)
        self.assertEqual(v2.route_candidate_v2(binding=args["binding"],stable_proof=proof,completed_v2_cycles=0)[0],"POST_FIRST_V2")

    def test_no_added_io_sleep_or_network(self):
        from unittest.mock import patch
        import instagram_navigation as nav
        d=self.device();args=self.args()
        # The grid parser and actual activity authority run; no permissive helper mock.
        with patch.object(nav.time,"sleep",side_effect=AssertionError("sleep")), \
             patch("subprocess.run",side_effect=AssertionError("subprocess")), \
             patch("socket.socket",side_effect=AssertionError("network")):
            self.assertTrue(self.outcome(v2.build_stable_candidate_proof_v2,args,device_context=d)[0])

    def test_routing_orchestrator_and_other_functions_byte_identical(self):
        import ast
        old=ast.parse(self.old_source);current=ast.parse(Path(v2.__file__).read_text())
        def remaining(source,tree):
            return {n.name:ast.get_source_segment(source,n) for n in tree.body
                    if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name!="build_stable_candidate_proof_v2"}
        self.assertEqual(remaining(self.old_source,old),remaining(Path(v2.__file__).read_text(),current))


if __name__ == "__main__":
    unittest.main()
