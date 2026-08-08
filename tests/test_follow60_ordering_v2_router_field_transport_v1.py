from __future__ import annotations

import os
import time
import unittest
from unittest import mock

import account_session_orchestrator as session
import follow60_ordering_v2_behavioral_canary_v1 as contract
from follow60_business_session_binding_v1 import (
    create_mainline_business_session_binding,
)


ACCOUNT_ID = "b024e94e-395d-4f02-9787-81ddc679b014"
RUN_ID = "a5ba8065-105e-4df5-8e8f-3c9e5ddc7268"
REQUEST_ID = "50f86120-9666-41f4-a6d9-b8594302f14b"
BUSINESS_SESSION_ID = "d193e082-a820-42f2-ac06-f466e29b2726"
WORKER_SHA = "0ba51ea1b6530f79c28deb9ed16e9efce47d2ee5"


def _mainline_binding() -> dict:
    return create_mainline_business_session_binding(
        business_session_id=BUSINESS_SESSION_ID,
        account_id=ACCOUNT_ID,
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        attempt_id=1,
        worker_sha=WORKER_SHA,
    ).to_dict()


def _v2_control() -> dict:
    return {
        "schema": contract.SCHEMA,
        "control_id": "e6281702-3b6e-4707-a022-5b8a8360e31f",
        "account_id": ACCOUNT_ID,
        "run_id": RUN_ID,
        "request_id": REQUEST_ID,
        "business_session_id": BUSINESS_SESSION_ID,
        "attempt_id": 1,
        "expected_worker_sha": WORKER_SHA,
        "actual_worker_sha": WORKER_SHA,
        "canary_type": contract.CANARY_TYPE,
        "max_new_cycles": 10,
        "baseline_follow_count": 0,
        "expires_at_epoch_s": time.time() + 3600,
        "lease_id": "c30c7c14-96cf-47f7-b8ae-684cab2a6ac8",
        "lease_nonce": "runtime-fixture-nonce",
        "lease_expires_at_epoch_s": time.time() + 3600,
        "claimed_at_epoch_s": time.time(),
        "candidate_seen_count": 0,
        "v2_selected_count": 0,
        "v2_complete_count": 0,
        "v2_partial_count": 0,
        "v1_fallback_count": 0,
        "status": "running",
    }


def _carrier() -> dict:
    return {
        "legacy_canary_field": "must-not-cross-mainline",
        "ordering_v2_behavioral_canary_v1": _v2_control(),
    }


def _runtime_grid_xml(candidate: str, posts_count: int, cell_count: int) -> str:
    cells = "".join(
        (
            '<node class="android.widget.ImageView" '
            f'resource-id="profile_grid_media_{index}" '
            f'content-desc="Post thumbnail, row {(index // 3) + 1}, '
            f'column {(index % 3) + 1}" '
            f'bounds="[{(index % 3) * 360},{900 + (index // 3) * 360}]'
            f'[{((index % 3) + 1) * 360},{1260 + (index // 3) * 360}]"/>'
        )
        for index in range(cell_count)
    )
    return f"""<hierarchy>
    <node text="{candidate}" bounds="[0,0][1080,120]"/>
    <node text="{posts_count} posts" bounds="[0,120][300,220]"/>
    <node resource-id="profile_tabs_container" bounds="[0,700][1080,850]">
      <node resource-id="profile_tab_icon_view" content-desc="Grid view"
            selected="true" bounds="[0,700][360,850]"/>
    </node>
    {cells}
    <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
    </hierarchy>"""


def _validated_v2_binding() -> contract.BehavioralCanaryBindingV1:
    binding, reason = contract.validate_behavioral_canary_binding(
        _v2_control(),
        account_id=ACCOUNT_ID,
        run_id=RUN_ID,
        request_id=REQUEST_ID,
        business_session_id=BUSINESS_SESSION_ID,
        attempt_id=1,
        worker_sha=WORKER_SHA,
        completed_v2_cycles=0,
        environ={
            contract.ENABLED_ENV: "true",
            contract.ALLOWLIST_ENV: ACCOUNT_ID,
        },
    )
    if binding is None:
        raise AssertionError(reason)
    return binding


def _route_runtime_fixture(candidate: str, posts_count: int, cell_count: int):
    binding = _validated_v2_binding()
    proof, proof_reason = contract.build_stable_candidate_proof_v2(
        binding=binding,
        mono_capture={
            "ok": True,
            "xml": _runtime_grid_xml(candidate, posts_count, cell_count),
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
        },
        business_evidence={
            "filter_passed": True,
            "filter_reason": "passed",
            "eligibility_passed": True,
            "eligibility_reason": "passed",
            "follow_budget_available": True,
        },
        target_id="target-runtime-fixture",
        candidate_username=candidate,
        action_id=f"action-{candidate}",
        binding_kind="mainline",
    )
    route = contract.route_candidate_v2(
        binding=binding,
        stable_proof=proof,
        completed_v2_cycles=0,
    )
    return proof, proof_reason, route


class Follow60OrderingV2RouterFieldTransportTests(unittest.TestCase):
    def test_carrier_extracts_only_ordering_v2_and_copies_nested_control(self) -> None:
        source = _carrier()
        carried = session._follow60_ordering_v2_runtime_control_carrier(source)
        self.assertEqual(
            carried,
            {
                "ordering_v2_behavioral_canary_v1":
                    source["ordering_v2_behavioral_canary_v1"]
            },
        )
        self.assertNotIn("legacy_canary_field", carried)
        self.assertIsNot(
            carried["ordering_v2_behavioral_canary_v1"],
            source["ordering_v2_behavioral_canary_v1"],
        )

    def test_empty_or_invalid_nested_control_preserves_mainline_v1_shape(self) -> None:
        for value in (None, {}, {"ordering_v2_behavioral_canary_v1": {}}, {
            "ordering_v2_behavioral_canary_v1": "invalid"
        }):
            with self.subTest(value=value):
                self.assertIsNone(
                    session._follow60_ordering_v2_runtime_control_carrier(value)
                )

    def test_rotation_transports_v2_control_without_enabling_legacy_canary(self) -> None:
        calls: list[dict] = []

        def engine(_device, **kwargs):
            calls.append(dict(kwargs))
            engine.last_session_summary = {
                "follows_completed_count": 1,
                "follow_session_outcome": "global_follow_cap_reached",
                "follow_stop_reason": "global_follow_cap_reached",
            }
            return 0

        engine.last_session_summary = {}
        source_carrier = _carrier()
        with mock.patch.dict(os.environ, {"WORKER_GIT_SHA": WORKER_SHA}):
            result = session._run_follow_target_rotation(
                object(),
                account_id=ACCOUNT_ID,
                account_username="rex_gen_boost_ai",
                run_id=RUN_ID,
                follow_targets=[{"target_id": "target-1", "source_profile": "ct"}],
                run_followers_list_engine_session=engine,
                supabase_mode=False,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=1,
                run_request_id=REQUEST_ID,
                follow60_mainline_active=True,
                follow60_business_session_binding=_mainline_binding(),
                follow60_canary_active=False,
                follow60_canary_control=source_carrier,
                follow60_attempt_id=1,
                business_session_id=BUSINESS_SESSION_ID,
            )

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0]["follow60_mainline_active"], True)
        self.assertNotIn("follow60_canary_active", calls[0])
        self.assertEqual(
            calls[0]["follow60_canary_control"],
            {
                "ordering_v2_behavioral_canary_v1":
                    source_carrier["ordering_v2_behavioral_canary_v1"]
            },
        )

    def test_account_session_transports_v2_control_to_rotation_on_mainline(self) -> None:
        captured: dict = {}
        source_carrier = _carrier()

        def stop_after_capture(_device, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop_after_v2_transport_capture")

        with (
            mock.patch.object(session, "_abort_if_operator_stop_requested", return_value=None),
            mock.patch.object(session, "load_account_commercial_policy_revision", return_value={}),
            mock.patch.object(
                session,
                "_resolve_target_availability_tenant_once",
                return_value=(None, None),
            ),
            mock.patch.object(
                session.supabase_client,
                "get_account_dm_settings",
                return_value={"welcome_enabled": False},
            ),
            mock.patch.object(
                session,
                "resolve_welcome_dm_real_send_enabled",
                return_value=(False, "test"),
            ),
            mock.patch.object(session, "_transition_buffer_blocks_business_actions", return_value=False),
            mock.patch.object(session, "_operator_stop_cancel_requested", return_value=False),
            mock.patch.object(session, "commercial_policy_boundary_blocks_phase", return_value=False),
            mock.patch.object(session, "_follow_to_unfollow_real_enabled", return_value=False),
            mock.patch.object(
                session,
                "_follow_to_unfollow_real_max_actions_effective",
                return_value=0,
            ),
            mock.patch.object(
                session,
                "_resolve_follow_source_rotation_settings",
                return_value={
                    "max_follows_per_target_per_run": 1,
                    "max_targets_per_run": 1,
                    "settings_source": "test",
                    "bounds": {},
                },
            ),
            mock.patch.object(
                session,
                "_run_follow_target_rotation",
                side_effect=stop_after_capture,
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "stop_after_v2_transport_capture"
            ):
                session.dispatch_account_session(
                    object(),
                    account_id=ACCOUNT_ID,
                    account_username="rex_gen_boost_ai",
                    run_id=RUN_ID,
                    run_request_id=REQUEST_ID,
                    source_profile_username="ct",
                    target_id="target-1",
                    follow_targets=[
                        {"target_id": "target-1", "source_profile": "ct"}
                    ],
                    run_followers_list_engine_session=mock.Mock(),
                    supabase_mode=False,
                    warm_session_used=False,
                    force_stop_used=False,
                    follow60_canary_active=False,
                    follow60_canary_control=source_carrier,
                    follow60_mainline_active=True,
                    follow60_business_session_binding=_mainline_binding(),
                    follow60_attempt_id=1,
                    business_session_id=BUSINESS_SESSION_ID,
                )

        self.assertIs(captured["follow60_mainline_active"], True)
        self.assertNotIn("follow60_canary_active", captured)
        self.assertEqual(
            captured["follow60_canary_control"],
            {
                "ordering_v2_behavioral_canary_v1":
                    source_carrier["ordering_v2_behavioral_canary_v1"]
            },
        )

    def test_three_runtime_safe_grid_structures_select_v2(self) -> None:
        for candidate, posts_count, cell_count in (
            ("dehrib7", 12, 6),
            ("carlosfontes23", 4, 4),
            ("armandrio_", 5, 5),
        ):
            with self.subTest(candidate=candidate):
                proof, reason, route = _route_runtime_fixture(
                    candidate, posts_count, cell_count
                )
                self.assertIsNotNone(proof, reason)
                self.assertTrue(proof.direct_grid_safe)
                self.assertEqual(
                    route,
                    ("POST_FIRST_V2", "v2_binding_and_direct_grid_safe"),
                )

    def test_no_posts_runtime_structure_falls_back_to_v1(self) -> None:
        proof, reason, route = _route_runtime_fixture("no_posts", 0, 0)
        self.assertIsNone(proof)
        self.assertEqual(reason, "v2_posts_count_not_positive")
        self.assertEqual(route, ("FOLLOW60_V1", "candidate_not_direct_grid_safe"))


if __name__ == "__main__":
    unittest.main()
