from types import SimpleNamespace
from unittest import TestCase, mock

import instagram_navigation as nav


class _Device:
    def window_size(self):
        return (1080, 2340)


class Follow60PostOpenLikeProofReuseV1Tests(TestCase):
    def test_positive_first_viewer_probe_has_no_fixed_sleep(self):
        detected = {
            "post_detected": True,
            "viewer_detect_path": "phase_a2_exact_like_desc_fast",
            "viewer_detect_total_ms": 4.0,
        }
        with mock.patch.object(
            nav, "_visual_detect_post_viewer_opened_after_tap", return_value=detected
        ), mock.patch.object(nav.time, "sleep") as sleep:
            out = nav._visual_wait_post_viewer_opened_after_tap(
                _Device(),
                pkg="com.instagram.android",
                expected_follower_username="candidate",
                act_before="MainActivity",
                post_follow_fast=True,
            )
        self.assertTrue(out["post_detected"])
        self.assertEqual(out["poll_count"], 1)
        self.assertEqual(out["viewer_open_poll_sleep_ms"], 0.0)
        sleep.assert_not_called()

    def test_fresh_tap_proof_transports_frame_classification_and_cell_identity(self):
        xml = '<hierarchy><node text="candidate" bounds="[0,0][1080,2340]"/></hierarchy>'
        classified = {
            "outcome": "POST_ROW_POSITIVE_SAFE",
            "identity_exact": True,
            "grid_selected": True,
            "post_count_positive": True,
            "tap_safe": True,
            "post_bounds": {
                "left": 0, "top": 1100, "right": 360, "bottom": 1460,
                "center_x": 180, "center_y": 1280,
            },
            "viewport_fingerprint": "viewport",
            "absolute_row_index": 1,
            "absolute_column_index": 1,
            "selected_absolute_cell": {"row": 1, "column": 1, "identity": "cell"},
        }
        frame = {"version": "coordinate_frame_v1", "raw_window_size": (1080, 2340)}
        proof = SimpleNamespace(
            bounds=classified["post_bounds"],
            metadata={
                "classification": "POST_ROW_POSITIVE_SAFE",
                "cell_identity": "cell",
                "absolute_row_index": 1,
                "absolute_column_index": 1,
            },
        )
        captured = {}

        def _stash(_purpose, **kwargs):
            captured.update(kwargs)
            return proof

        with mock.patch.object(
            nav, "_post_follow_post_grid_evidence_from_xml", return_value=classified
        ), mock.patch.object(
            nav, "_post_follow_screen_dimensions_from_hierarchy",
            return_value={"coordinate_frame": frame},
        ), mock.patch.object(
            nav, "_followers_current_pkg_activity",
            return_value={"current_package": "com.instagram.android", "current_activity": "MainActivity"},
        ), mock.patch(
            "follow_60s_canary.stash_fresh_tap_proof", side_effect=_stash
        ), mock.patch(
            "follow_60s_canary.consume_fresh_tap_proof",
            return_value=(proof, 3.0, ""),
        ):
            out = nav._post_follow_create_fresh_tap_proof_from_grid(
                _Device(),
                source_profile_username="ct",
                candidate_username="candidate",
                pkg="com.instagram.android",
                hierarchy_xml=xml,
            )

        self.assertTrue(out["ok"])
        metadata = captured["metadata"]
        self.assertTrue(metadata["candidate_bound"])
        self.assertEqual(metadata["cell_identity"], "cell")
        self.assertEqual(metadata["absolute_row_index"], 1)
        self.assertEqual(metadata["absolute_column_index"], 1)
        self.assertEqual(metadata["coordinate_frame"], frame)
        self.assertTrue(metadata["xml_hash"])
        self.assertEqual(out["proof_metadata"]["cell_identity"], "cell")


if __name__ == "__main__":
    import unittest

    unittest.main()
