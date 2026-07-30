from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import instagram_navigation as nav


class _EmptySelector:
    def all(self) -> list[object]:
        return []


class _HierarchyDevice:
    def __call__(self, **_kwargs: object) -> _EmptySelector:
        return _EmptySelector()

    def dump_hierarchy(self, compressed: bool = False) -> str:
        _ = compressed
        return """\
<hierarchy>
  <node text="Follow" content-desc="" bounds="[20,300][250,420]" clickable="true" />
  <node text="" content-desc="Follow" bounds="[79,1966][1001,2084]" clickable="true" />
</hierarchy>
"""


class FollowReviewPopupConfirmationTest(unittest.TestCase):
    def test_sheet_candidate_excludes_profile_header_follow(self) -> None:
        candidates = nav._find_review_sheet_follow_candidates(
            _HierarchyDevice(),
            ww=1080,
            wh=2400,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["center_x"], 540)
        self.assertEqual(candidates[0]["center_y"], 2025)
        self.assertEqual(
            candidates[0]["detection_method"],
            "hierarchy_xml:content_desc_exact",
        )

    def test_follow_tap_is_sent_exactly_once_even_when_poll_times_out(self) -> None:
        device = MagicMock()
        device.window_size.return_value = (1080, 2400)
        candidates = [
            {
                "center_x": 540,
                "center_y": 2025,
                "score": 100.0,
                "detection_method": "hierarchy_xml:content_desc_exact",
                "text": "",
                "content_desc": "Follow",
                "resource_id": "",
                "bounds": {"left": 79, "top": 1966, "right": 1001, "bottom": 2084},
                "clickable": True,
            },
            {
                "center_x": 540,
                "center_y": 1900,
                "score": 80.0,
                "detection_method": "geometry_cancel_anchor",
                "text": "Follow",
                "content_desc": "",
                "resource_id": "",
                "bounds": {},
                "clickable": True,
            },
        ]
        with patch.object(
            nav, "_review_before_follow_popup_visible", return_value=True
        ), patch.object(
            nav, "_find_review_sheet_follow_candidates", return_value=candidates
        ), patch.object(
            nav, "_poll_review_sheet_dismissed_after_follow_tap", return_value=False
        ):
            handled = nav._try_review_before_follow_popup_confirm(
                device,
                target_username="family_a.6mad",
                visual_candidate_id="vc-review",
            )

        self.assertFalse(handled)
        device.click.assert_called_once_with(540, 2025)

    def test_late_sheet_dismissal_resumes_normal_follow_verification(self) -> None:
        device = MagicMock()
        with patch.object(
            nav,
            "_review_before_follow_popup_visible",
            side_effect=[True, False],
        ), patch.object(
            nav,
            "_follow_ui_state_snapshot",
            return_value="follow",
        ):
            dismissed = nav._poll_review_sheet_dismissed_after_follow_tap(
                device,
                target_username="family_a.6mad",
                visual_candidate_id="vc-review",
                tap_x=540,
                tap_y=2025,
                detection_method="hierarchy_xml:content_desc_exact",
            )

        self.assertTrue(dismissed)

    def test_gone_sheet_with_pending_state_is_not_a_fatal_abort(self) -> None:
        record = MagicMock()
        with patch.object(
            nav, "_review_before_follow_popup_visible", return_value=False
        ):
            outcome = nav._follow_review_popup_unhandled_abort(
                MagicMock(),
                target_username="family_a.6mad",
                visual_candidate_id="vc-review",
                events=[],
                record=record,
                state_before="follow",
                state_after="follow",
                reason="review_sheet_visible_after_initial_follow_tap",
            )

        self.assertIsNone(outcome)
        self.assertEqual(
            record.call_args.args[0],
            "follow_review_popup_late_dismissal_verification_resumed",
        )

    def test_sheet_still_visible_remains_fail_closed(self) -> None:
        record = MagicMock()
        with patch.object(
            nav, "_review_before_follow_popup_visible", return_value=True
        ), patch.object(
            nav,
            "_capture_follow_review_popup_unhandled_evidence",
            return_value={
                "screenshot_path": "/redacted/follow_review.png",
                "xml_path": "/redacted/follow_review.xml",
                "foreground_package": "com.instagram.android",
                "foreground_activity": ".MainActivity",
            },
        ):
            outcome = nav._follow_review_popup_unhandled_abort(
                MagicMock(),
                target_username="family_a.6mad",
                visual_candidate_id="vc-review",
                events=[],
                record=record,
                state_before="follow",
                state_after="follow",
                reason="review_sheet_visible_after_initial_follow_tap",
            )

        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertFalse(outcome["ok"])
        self.assertEqual(
            outcome["failure_code"],
            nav.FOLLOW_REVIEW_POPUP_UNHANDLED_FAILURE_CODE,
        )
        self.assertEqual(
            outcome["evidence"]["foreground_package"],
            "com.instagram.android",
        )
        safe_stop_payload = next(
            call.args[1]
            for call in record.call_args_list
            if call.args[0] == "follow_review_popup_unhandled_safe_stop"
        )
        self.assertEqual(
            safe_stop_payload["evidence"]["screenshot_path"],
            "/redacted/follow_review.png",
        )

    def test_evidence_capture_is_invoked_before_safe_stop_record(self) -> None:
        order: list[str] = []

        def capture(*_args, **_kwargs):
            order.append("capture")
            return {"screenshot_path": "/redacted/a.png", "xml_path": "/redacted/a.xml"}

        def record(event, _payload):
            order.append(str(event))

        with patch.object(
            nav, "_review_before_follow_popup_visible", return_value=True
        ), patch.object(
            nav, "_capture_follow_review_popup_unhandled_evidence", side_effect=capture
        ):
            nav._follow_review_popup_unhandled_abort(
                MagicMock(),
                target_username="family_a.6mad",
                visual_candidate_id="vc-review",
                events=[],
                record=record,
                state_before="follow",
                state_after="follow",
                reason="review_sheet_visible_after_initial_follow_tap",
            )

        self.assertEqual(order[0], "capture")
        self.assertEqual(order[1], "follow_review_popup_unhandled_safe_stop")


if __name__ == "__main__":
    unittest.main()
