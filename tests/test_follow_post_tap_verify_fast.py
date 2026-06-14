from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import instagram_navigation as nav


class _Selector:
    def __init__(self, device: "_Device") -> None:
        self._device = device

    def exists(self, timeout: float = 0.0) -> bool:
        _ = timeout
        return self._device.current_info is not None

    @property
    def info(self) -> dict:
        info = dict(self._device.current_info or {})
        self._device.advance()
        return info


class _Device:
    def __init__(self, states: list[dict | None]) -> None:
        self._states = list(states)
        self._idx = 0
        self.click = MagicMock()

    @property
    def current_info(self) -> dict | None:
        if self._idx >= len(self._states):
            return self._states[-1] if self._states else None
        return self._states[self._idx]

    def advance(self) -> None:
        self._idx += 1

    def __call__(self, **kwargs):
        if kwargs.get("resourceId"):
            return _Selector(self)
        return MagicMock(exists=MagicMock(return_value=False), info={})


def _button() -> MagicMock:
    btn = MagicMock()
    btn.info = {
        "bounds": {"left": 20, "top": 100, "right": 220, "bottom": 180},
        "text": "Follow",
        "resourceName": "com.instagram.android:id/profile_header_follow_button",
    }
    return btn


def _context(username: str = "public_user") -> dict:
    return nav.build_pre_follow_tap_context(
        follower_username=username,
        source_profile_username="source_user",
        visual_candidate_id="vc-1",
        screen_guard={
            "ok": True,
            "follow_header_state": "follow",
            "navigation_state": "CANDIDATE_PROFILE",
            "action_bar_title": username,
        },
        private_gate={
            "reject": False,
            "private_profile_detected": False,
            "probe_ms": 12.0,
            "probe_reused": False,
            "private_probe_payload": {
                "private_profile_detected": False,
                "detection_method": "none",
                "confidence": 0.0,
                "probe_ms": 12.0,
                "hierarchy_fallback_used": False,
            },
        },
    )


def _run_follow(
    device: _Device,
    username: str = "public_user",
    *,
    return_review_mocks: bool = False,
) -> dict | tuple[dict, MagicMock, MagicMock]:
    with patch(
        "follow_action_engine.follow_action_surface_wait_and_select_element",
        return_value=(
            _button(),
            {"events": [], "exact_follow_fast_path": True, "last_ui_state": "follow"},
        ),
    ), patch.object(
        nav,
        "_try_review_before_follow_popup_confirm",
        return_value=False,
    ) as mock_review_confirm, patch.object(
        nav,
        "_review_before_follow_popup_visible",
        return_value=False,
    ) as mock_review_visible, patch(
        "instagram_navigation.time.sleep"
    ):
        out = nav.perform_follow_safe(
            device,
            username,
            "com.instagram.android",
            profile_already_open=True,
            source_profile_username="source_user",
            visual_candidate_id="vc-1",
            dont_follow_private_accounts=False,
            pre_follow_context=_context(username),
        )
    if return_review_mocks:
        return out, mock_review_confirm, mock_review_visible
    return out


class FollowPostTapVerifyFastTest(unittest.TestCase):
    def _state_info(self, text: str, desc: str = "") -> dict:
        return {
            "text": text,
            "contentDescription": desc,
            "resourceName": "com.instagram.android:id/profile_header_follow_button",
            "bounds": {"left": 20, "top": 100, "right": 220, "bottom": 180},
        }

    def test_rid_following_succeeds_without_full_snapshot(self) -> None:
        device = _Device([self._state_info("Following")])
        with patch.object(nav, "_follow_ui_state_snapshot") as mock_snapshot:
            out, mock_review_confirm, mock_review_visible = _run_follow(
                device, return_review_mocks=True
            )

        self.assertTrue(out["ok"])
        self.assertEqual(out["follow_state_after"], "following")
        self.assertEqual(out["verify_attempts"], 1)
        mock_snapshot.assert_not_called()
        mock_review_confirm.assert_not_called()
        mock_review_visible.assert_not_called()

    def test_rid_requested_succeeds_without_full_snapshot(self) -> None:
        device = _Device([self._state_info("Requested")])
        with patch.object(nav, "_follow_ui_state_snapshot") as mock_snapshot:
            out, mock_review_confirm, mock_review_visible = _run_follow(
                device, return_review_mocks=True
            )

        self.assertTrue(out["ok"])
        self.assertEqual(out["follow_state_after"], "requested")
        mock_snapshot.assert_not_called()
        mock_review_confirm.assert_not_called()
        mock_review_visible.assert_not_called()

    def test_rid_still_follow_polls_then_falls_back_to_snapshot(self) -> None:
        device = _Device(
            [
                self._state_info("Follow"),
                self._state_info("Follow"),
                self._state_info("Follow"),
            ]
        )
        with patch.object(
            nav,
            "_follow_ui_state_snapshot",
            return_value="following",
        ) as mock_snapshot:
            out, mock_review_confirm, mock_review_visible = _run_follow(
                device, return_review_mocks=True
            )

        self.assertTrue(out["ok"])
        self.assertEqual(out["follow_state_after"], "following")
        self.assertEqual(out["verify_attempts"], 3)
        mock_snapshot.assert_called_once()
        self.assertGreaterEqual(mock_review_confirm.call_count, 1)
        self.assertGreaterEqual(mock_review_visible.call_count, 1)

    def test_rid_absent_falls_back_to_snapshot(self) -> None:
        device = _Device([None])
        with patch.object(
            nav,
            "_follow_ui_state_snapshot",
            return_value="following",
        ) as mock_snapshot:
            out, mock_review_confirm, mock_review_visible = _run_follow(
                device, return_review_mocks=True
            )

        self.assertTrue(out["ok"])
        self.assertEqual(out["follow_state_after"], "following")
        mock_snapshot.assert_called_once()
        self.assertGreaterEqual(mock_review_confirm.call_count, 1)
        self.assertGreaterEqual(mock_review_visible.call_count, 1)

    def test_ambiguous_rid_text_falls_back_to_snapshot(self) -> None:
        device = _Device([self._state_info("See options")])
        with patch.object(
            nav,
            "_follow_ui_state_snapshot",
            return_value="following",
        ) as mock_snapshot:
            out, mock_review_confirm, mock_review_visible = _run_follow(
                device, return_review_mocks=True
            )

        self.assertTrue(out["ok"])
        self.assertEqual(out["follow_state_after"], "following")
        mock_snapshot.assert_called_once()
        self.assertGreaterEqual(mock_review_confirm.call_count, 1)
        self.assertGreaterEqual(mock_review_visible.call_count, 1)

    def test_review_sheet_visible_uses_existing_abort_path(self) -> None:
        device = _Device([self._state_info("Follow")])
        sentinel = {
            "ok": False,
            "failure_code": 36,
            "visual_follow_failure_reason": "review_sheet_visible",
        }
        with patch(
            "follow_action_engine.follow_action_surface_wait_and_select_element",
            return_value=(
                _button(),
                {
                    "events": [],
                    "exact_follow_fast_path": True,
                    "last_ui_state": "follow",
                },
            ),
        ), patch.object(
            nav,
            "_try_review_before_follow_popup_confirm",
            return_value=False,
        ), patch.object(
            nav,
            "_review_before_follow_popup_visible",
            return_value=True,
        ), patch.object(
            nav,
            "_follow_review_popup_unhandled_abort",
            return_value=sentinel,
        ) as mock_abort, patch(
            "instagram_navigation.time.sleep"
        ), patch.object(
            nav,
            "_follow_ui_state_snapshot",
            return_value="follow",
        ):
            out = nav.perform_follow_safe(
                device,
                "public_user",
                "com.instagram.android",
                profile_already_open=True,
                source_profile_username="source_user",
                visual_candidate_id="vc-1",
                dont_follow_private_accounts=False,
                pre_follow_context=_context("public_user"),
            )

        self.assertIs(out, sentinel)
        mock_abort.assert_called_once()


if __name__ == "__main__":
    unittest.main()
