from __future__ import annotations

import hashlib
import time
import unittest
from unittest import mock

import follow_60s_canary as canary
import instagram_navigation as nav
from tests.follow60_generic_fixtures import (
    TEST_CANARY_ACCOUNT_ID,
    TEST_CANARY_USERNAME,
    configure_canary,
)


PKG = "com.instagram.android"
ACT = "com.instagram.mainactivity.InstagramMainActivity"
FIELD_CANDIDATES = (
    "usegabo",
    "aloma.moura",
    "dr.pc_ardn",
    "annadanse28",
    "table.facile",
    "bamboo.onigiris",
)


class GrowthLikeFreshAuthorityResetV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            configure_canary(
                canary,
                account_id=TEST_CANARY_ACCOUNT_ID,
                account_username=TEST_CANARY_USERNAME,
                run_id="growth-like-authority-reset",
                package=PKG,
                resume_policy=None,
            )
        )
        self.device = mock.MagicMock()
        self.device.window_size.return_value = (1080, 2340)

    def tearDown(self) -> None:
        configure_canary(
            canary,
            account_id="other",
            account_username="other",
            run_id="reset",
            package=PKG,
            resume_policy=None,
        )

    def _binding(self, candidate: str) -> dict[str, object]:
        return {
            "account_id": TEST_CANARY_ACCOUNT_ID,
            "run_id": "growth-like-authority-reset",
            "request_id": "request-growth-like",
            "action_id": f"action-{candidate}",
            "attempt_id": 2,
            "business_session_id": "business-growth-like",
            "control_id": "control-growth-like",
            "worker_sha": "worker-growth-like",
            "candidate_username": candidate,
            "source_target_id": "target-growth-like",
        }

    def _xml(
        self,
        candidate: str,
        *,
        state: str = "Like",
        include_posts: bool = True,
        include_like: bool = True,
        bounds: str = "[40,1700][120,1800]",
        dynamic_marker: str = "fresh",
    ) -> str:
        posts = '<node text="Posts"/>' if include_posts else ""
        like = (
            f'<node resource-id="{PKG}:id/row_feed_button_like" '
            f'content-desc="{state}" clickable="true" bounds="{bounds}"/>'
            if include_like
            else ""
        )
        return (
            f'<hierarchy><node text="{dynamic_marker}"/>{posts}'
            f'<node text="{candidate}"/>{like}</hierarchy>'
        )

    def _post_open(
        self,
        candidate: str,
        *,
        age_seconds: float = 25.0,
        stage_overrides: dict[str, object] | None = None,
    ) -> dict[str, object]:
        old_xml = self._xml(candidate, dynamic_marker="stale-clock-10:00")
        exact = nav._hierarchy_collect_like_semantic_nodes(old_xml)[0]
        binding = self._binding(candidate)
        stage: dict[str, object] = {
            "version": "PostOpenContextV1",
            **binding,
            "candidate_profile_id": "profile-growth-like",
            "package": PKG,
            "activity": ACT,
            "viewer_type": "Posts",
            "open_method": "direct_cell_under_suggested",
            "source_cell_fingerprint": "source-cell-growth-like",
            "v5_positive": True,
            "post_identity_confirmed": True,
            "story_or_highlight_detected": False,
            "exact_like_proof": exact,
            "exact_like_source": "transported-a2-v5",
            "exact_like_bounds": exact["matched_node_bounds"],
            "exact_like_bounds_hash": "stale-bounds-hash",
            "xml_hash": hashlib.sha256(old_xml.encode()).hexdigest(),
            "exact_like_xml_fingerprint": hashlib.sha256(old_xml.encode()).hexdigest()[:20],
            "exact_like_ui_generation": canary.runtime_context()["ui_generation"],
            "post_open_ui_generation": canary.runtime_context()["ui_generation"],
            "navigation_generation": "growth-nav-generation",
            "stage_nonce": "growth-stage-nonce",
            "created_at_monotonic": time.monotonic() - age_seconds,
        }
        stage.update(stage_overrides or {})
        stage["proof_hash"] = nav._post_open_context_v1_proof_hash(stage)
        return {
            "post_open_surface_audit": {
                "snapshot_xml": old_xml,
                "snapshot_captured_at_monotonic": (
                    1.0
                    if age_seconds >= 20.0
                    else time.perf_counter() - age_seconds
                ),
            },
            "post_open_context_v1": stage,
        }

    def _create(
        self,
        candidate: str,
        *,
        post_open: dict[str, object] | None = None,
        fresh_xml: str | None = None,
        current_package: str = PKG,
        current_activity: str = ACT,
        force_fresh_dump: bool = True,
    ):
        self.device.dump_hierarchy.return_value = fresh_xml or self._xml(
            candidate, dynamic_marker="fresh-clock-10:01"
        )
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={
                "current_package": current_package,
                "current_activity": current_activity,
            },
        ):
            return nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username=candidate,
                expected_stage_binding=self._binding(candidate),
                post_open_context=post_open or self._post_open(candidate),
                allow_fresh_dump=force_fresh_dump,
                force_fresh_dump=force_fresh_dump,
            )

    def test_six_growth_replays_discard_stale_ui_and_rebuild_from_fresh_xml(self) -> None:
        for index, candidate in enumerate(FIELD_CANDIDATES):
            with self.subTest(candidate=candidate):
                self.device.reset_mock()
                post_open = self._post_open(candidate)
                old_fingerprint = post_open["post_open_context_v1"][
                    "exact_like_xml_fingerprint"
                ]
                with mock.patch.object(nav.time, "perf_counter", return_value=30.0):
                    first_ctx, first_reason = self._create(
                        candidate,
                        post_open=post_open,
                        force_fresh_dump=False,
                    )
                self.assertIsNone(first_ctx)
                self.assertEqual(first_reason, "liketapcontext_snapshot_ttl_expired")
                self.device.dump_hierarchy.assert_not_called()

                fresh_xml = self._xml(
                    candidate,
                    bounds=f"[40,{1450 + index * 35}][120,{1530 + index * 35}]",
                    dynamic_marker=f"fresh-clock-{index}",
                )
                ctx, reason = self._create(
                    candidate,
                    post_open=post_open,
                    fresh_xml=fresh_xml,
                )
                self.assertEqual(reason, "")
                self.assertIsNotNone(ctx)
                self.device.dump_hierarchy.assert_called_once()
                self.assertTrue(ctx["fresh_ui_authority"])
                self.assertTrue(ctx["stale_ui_authority_discarded"])
                self.assertFalse(ctx["exact_like_transport_used"])
                self.assertNotEqual(ctx["xml_fingerprint"], old_fingerprint)
                self.assertEqual(ctx["account_id"], TEST_CANARY_ACCOUNT_ID)
                self.assertEqual(ctx["request_id"], "request-growth-like")
                self.assertEqual(ctx["run_id"], "growth-like-authority-reset")
                self.assertEqual(ctx["business_session_id"], "business-growth-like")
                self.assertEqual(ctx["action_id"], f"action-{candidate}")
                self.assertEqual(ctx["candidate_username"], candidate)
                self.assertEqual(ctx["package"], PKG)
                self.assertEqual(ctx["activity"], ACT)
                self.assertEqual(
                    ctx["source_navigation_generation"],
                    "growth-nav-generation",
                )
                self.assertEqual(
                    ctx["source_post_open_ui_generation"],
                    canary.runtime_context()["ui_generation"],
                )
                with mock.patch.object(
                    nav,
                    "_followers_current_pkg_activity",
                    return_value={"current_package": PKG, "current_activity": ACT},
                ):
                    valid, validation_reason, _ = nav._validate_like_tap_context_v2(
                        ctx,
                        expected_stage_binding=self._binding(candidate),
                        expected_follower_username=candidate,
                        d=self.device,
                        expected_package=PKG,
                    )
                self.assertTrue(valid, validation_reason)

    def test_fresh_semantics_replace_all_stale_ui_claims(self) -> None:
        candidate = "usegabo"
        post_open = self._post_open(
            candidate,
            stage_overrides={
                "viewer_type": "Story",
                "v5_positive": False,
                "post_identity_confirmed": False,
                "story_or_highlight_detected": True,
            },
        )
        ctx, reason = self._create(candidate, post_open=post_open)
        self.assertEqual(reason, "")
        self.assertTrue(ctx["fresh_ui_authority"])
        self.assertEqual(ctx["viewer_type"], "Posts")
        self.assertFalse(ctx["story_or_highlight_detected"])

    def test_non_expired_healthy_control_uses_immutable_path_without_dump(self) -> None:
        candidate = "kazhki35"
        post_open = self._post_open(candidate, age_seconds=0.0)
        ctx, reason = self._create(
            candidate,
            post_open=post_open,
            force_fresh_dump=False,
        )
        self.assertEqual(reason, "")
        self.device.dump_hierarchy.assert_not_called()
        self.assertTrue(ctx["exact_like_transport_used"])
        self.assertFalse(ctx["fresh_ui_authority"])
        self.assertFalse(ctx["stale_ui_authority_discarded"])

    def test_fresh_reacquisition_safety_matrix(self) -> None:
        candidate = "candidate"
        cases = (
            (
                "wrong_identity",
                {"fresh_xml": self._xml("other")},
                "liketapcontext_fresh_candidate_identity_missing",
            ),
            (
                "wrong_package",
                {"current_package": "com.example.foreign"},
                "liketapcontext_post_open_live_package_mismatch",
            ),
            (
                "wrong_activity",
                {"current_activity": "com.instagram.other.OtherActivity"},
                "liketapcontext_post_open_live_activity_mismatch",
            ),
            (
                "posts_absent",
                {"fresh_xml": self._xml(candidate, include_posts=False)},
                "liketapcontext_fresh_posts_viewer_missing",
            ),
            (
                "like_absent",
                {"fresh_xml": self._xml(candidate, include_like=False)},
                "liketapcontext_exact_like_missing",
            ),
            (
                "already_liked",
                {"fresh_xml": self._xml(candidate, state="Unlike")},
                "liketapcontext_already_liked",
            ),
            (
                "invalid_bounds",
                {"fresh_xml": self._xml(candidate, bounds="[40,1700][1400,1800]")},
                "liketapcontext_exact_like_missing",
            ),
        )
        for name, kwargs, expected_reason in cases:
            with self.subTest(case=name):
                self.device.reset_mock()
                ctx, reason = self._create(candidate, **kwargs)
                self.assertIsNone(ctx)
                self.assertEqual(reason, expected_reason)
                self.device.dump_hierarchy.assert_called_once()
                self.device.click.assert_not_called()

    def test_fresh_reacquisition_preserves_durable_binding_integrity(self) -> None:
        candidate = "candidate"
        post_open = self._post_open(candidate)
        post_open["post_open_context_v1"]["request_id"] = "tampered"
        ctx, reason = self._create(candidate, post_open=post_open)
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_hash_mismatch")
        self.device.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
