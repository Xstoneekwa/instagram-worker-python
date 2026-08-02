from __future__ import annotations

import hashlib
import time
import unittest
from unittest import mock

import follow_60s_canary as canary
import instagram_navigation as nav
from tests.follow60_generic_fixtures import configure_canary


PKG = "com.instagram.android"
ACT = "com.instagram.mainactivity.InstagramMainActivity"
ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"


class PostOpenLikeTapBridgeV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        configure_canary(
            canary,
            account_id=ACCOUNT_ID,
            account_username="bridge_account",
            run_id="run-bridge",
            package=PKG,
            resume_policy=None,
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

    def _binding(self) -> dict[str, object]:
        return {
            "account_id": ACCOUNT_ID,
            "run_id": "run-bridge",
            "request_id": "request-bridge",
            "action_id": "action-bridge",
            "attempt_id": 1,
            "business_session_id": "business-bridge",
            "control_id": "control-bridge",
            "worker_sha": "worker-bridge",
        }

    def _xml(self, *, candidate: str = "candidate", state: str = "Like", story: bool = False) -> str:
        story_node = '<node resource-id="story_viewer_root"/>' if story else ""
        return (
            f'<hierarchy>{story_node}<node text="Posts"/><node text="{candidate}"/>'
            f'<node resource-id="com.instagram.android:id/row_feed_button_like" '
            f'content-desc="{state}" clickable="true" bounds="[40,1700][120,1800]"/>'
            '</hierarchy>'
        )

    def _stage(self, **overrides: object) -> dict[str, object]:
        stage = {
            "version": "PostOpenContextV1",
            **self._binding(),
            "candidate_username": "candidate",
            "candidate_profile_id": "profile-bridge",
            "source_target_id": "target-bridge",
            "package": PKG,
            "activity": ACT,
            "viewer_type": "Posts",
            "open_method": "direct_cell_under_suggested",
            "source_cell_fingerprint": "cell-fingerprint",
            "v5_positive": True,
            "post_identity_confirmed": True,
            "story_or_highlight_detected": False,
            "exact_like_proof": None,
            "exact_like_source": "",
            "exact_like_bounds": None,
            "exact_like_bounds_hash": "",
            "xml_hash": "",
            "exact_like_xml_fingerprint": "",
            "exact_like_ui_generation": 0,
            "post_open_ui_generation": canary.runtime_context()["ui_generation"],
            "created_at_monotonic": time.monotonic(),
            "navigation_generation": "nav-bridge",
            "stage_nonce": "nonce-bridge",
        }
        stage.update(overrides)
        stage["proof_hash"] = nav._post_open_context_v1_proof_hash(stage)
        return stage

    def _create(self, *, stage: dict[str, object] | None = None, xml: str | None = None, allow_dump: bool = False):
        post_open: dict[str, object] = {"post_open_context_v1": stage or self._stage()}
        if xml is not None:
            post_open["post_open_surface_audit"] = {
                "snapshot_xml": xml,
                "snapshot_captured_at_monotonic": time.perf_counter(),
            }
        with mock.patch.object(
            nav,
            "_followers_current_pkg_activity",
            return_value={"current_package": PKG, "current_activity": ACT},
        ):
            return nav._create_like_tap_context_v2(
                self.device,
                expected_package=PKG,
                expected_follower_username="candidate",
                expected_stage_binding=self._binding(),
                post_open_context=post_open,
                allow_fresh_dump=allow_dump,
                force_fresh_dump=allow_dump and xml is None,
            )

    def test_v5_bridge_uses_existing_single_xml_without_dump(self) -> None:
        ctx, reason = self._create(xml=self._xml())
        self.assertEqual(reason, "")
        self.assertEqual(ctx["like_context_transport"], "PostOpenContextV1_single_xml_bridge")
        self.device.dump_hierarchy.assert_not_called()

    def test_v5_bridge_reacquires_exactly_one_xml_when_snapshot_missing(self) -> None:
        self.device.dump_hierarchy.return_value = self._xml()
        ctx, reason = self._create(allow_dump=True)
        self.assertEqual(reason, "")
        self.assertEqual(ctx["snapshot_source"], "single_reacquisition")
        self.device.dump_hierarchy.assert_called_once()

    def test_bridge_keeps_exact_candidate_binding(self) -> None:
        ctx, reason = self._create(xml=self._xml())
        self.assertEqual(reason, "")
        self.assertEqual(ctx["candidate_username"], "candidate")
        self.assertEqual(ctx["request_id"], "request-bridge")

    def test_story_wins_over_like_node(self) -> None:
        ctx, reason = self._create(xml=self._xml(story=True))
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_story_or_highlight_detected")

    def test_already_liked_fails_closed(self) -> None:
        ctx, reason = self._create(xml=self._xml(state="Unlike"))
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_already_liked")

    def test_candidate_mismatch_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(candidate_username="other"), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_candidate_mismatch")

    def test_binding_mismatch_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(request_id="wrong"), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_binding_mismatch")

    def test_v5_false_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(v5_positive=False), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_v5_rejected")

    def test_identity_false_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(post_identity_confirmed=False), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_v5_rejected")

    def test_stage_nonce_missing_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(stage_nonce=""), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_stage_nonce_missing")

    def test_source_fingerprint_missing_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(source_cell_fingerprint=""), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_source_fingerprint_missing")

    def test_navigation_generation_missing_fails_closed(self) -> None:
        ctx, reason = self._create(stage=self._stage(navigation_generation=""), xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_navigation_generation_missing")

    def test_generation_change_fails_closed(self) -> None:
        stage = self._stage(
            post_open_ui_generation=int(canary.runtime_context()["ui_generation"]) + 1
        )
        ctx, reason = self._create(stage=stage, xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_generation_changed")

    def test_tampered_hash_fails_closed(self) -> None:
        stage = self._stage()
        stage["source_cell_fingerprint"] = "tampered"
        ctx, reason = self._create(stage=stage, xml=self._xml())
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_post_open_hash_mismatch")

    def test_exact_like_missing_preserves_create_reason(self) -> None:
        xml = '<hierarchy><node text="Posts"/><node text="candidate"/></hierarchy>'
        ctx, reason = self._create(xml=xml)
        self.assertIsNone(ctx)
        self.assertEqual(reason, "liketapcontext_exact_like_missing")
        self.assertEqual(
            nav._liketapcontext_rejection_reason(
                ctx,
                create_reason=reason,
                validation_reason="liketapcontext_version_missing",
            ),
            "liketapcontext_exact_like_missing",
        )


if __name__ == "__main__":
    unittest.main()
