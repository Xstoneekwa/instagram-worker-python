from __future__ import annotations

import unittest
from unittest import mock

import instagram_navigation as nav
import runner


PKG = "com.instagram.android"
MAIN = "com.instagram.mainactivity.InstagramMainActivity"


class _MissingSelector:
    def wait(self, timeout: float = 0.0) -> bool:
        return False

    def exists(self, timeout: float = 0.0) -> bool:
        return False


class _Device:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.clicks: list[tuple[int, int]] = []

    def app_current(self) -> dict[str, str]:
        return {"package": PKG, "activity": MAIN}

    def window_size(self) -> tuple[int, int]:
        return (1080, 2340)

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def press(self, key: str) -> None:
        self.events.append(("back", key))

    def __call__(self, **_kwargs: object) -> _MissingSelector:
        return _MissingSelector()


def _obs(surface: nav.FollowersRecoverySurface) -> tuple[nav.FollowersRecoverySurface, dict[str, object]]:
    return surface, {"surface": surface.value, "reason": "fixture"}


class FollowersWrongDepthRecoveryTests(unittest.TestCase):
    def test_mythyl_false_positive_source_ct_reopens_without_back(self) -> None:
        device = _Device()
        source = "aleksborys"
        candidate = "nikodembialet"
        row_token = mock.Mock(
            row_center=(320, 420),
            viewport_proof_id="vp-1",
            row_fingerprint="row-1",
            generations=mock.Mock(navigation_generation=0, scroll_generation=0),
        )
        with mock.patch.object(nav, "verify_profile", return_value=True), mock.patch.object(
            nav,
            "read_current_profile_username_for_follow_gate",
            return_value=source,
        ), mock.patch.object(
            nav,
            "detect_followers_list_screen_fresh",
            return_value=({"is_followers_list": False, "action_bar_title": source}, "<xml/>"),
        ), mock.patch.object(
            nav,
            "open_followers_list_from_profile",
            return_value=(True, {"is_followers_list": True}),
        ) as reopen, mock.patch.object(
            nav,
            "_followers_prepare_exact_row_action_token",
            return_value=(row_token, {"reason": "fixture"}),
        ), mock.patch.object(
            nav.followers_proof,
            "consume_row_action_token",
            return_value=(True, "row_action_token_consumed"),
        ), mock.patch.object(nav.time, "sleep", return_value=None):
            opened = nav.open_follower_profile_from_list(
                device,
                {"username": candidate, "row_center": [320, 420]},
                source,
                PKG,
            )
            recovered, reason, detail = (
                nav.recover_followers_after_candidate_profile_open_not_confirmed(
                    device,
                    source,
                    PKG,
                    candidate_username=candidate,
                )
            )

        self.assertFalse(opened)
        self.assertTrue(recovered)
        self.assertEqual(reason, "reopen_from_proved_source_ct_profile")
        self.assertEqual(detail["surface"], "SOURCE_CT_PROFILE")
        self.assertEqual(device.events, [])
        self.assertEqual(device.clicks, [(320, 420)])
        reopen.assert_called_once_with(device, source, PKG, profile_verified=True)

    def test_followers_already_present_requires_no_back_or_reopen(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav,
            "classify_followers_recovery_surface",
            return_value=_obs(nav.FollowersRecoverySurface.FOLLOWERS_LIST),
        ), mock.patch.object(nav, "open_followers_list_from_profile") as reopen:
            ok, reason, _detail = (
                nav.recover_followers_after_candidate_profile_open_not_confirmed(
                    device, "source.ct", PKG, candidate_username="candidate"
                )
            )
        self.assertTrue(ok)
        self.assertEqual(reason, "followers_already_present")
        self.assertEqual(device.events, [])
        reopen.assert_not_called()

    def test_candidate_profile_uses_one_back_then_fresh_observation(self) -> None:
        device = _Device()
        order: list[str] = []
        surfaces = iter(
            [
                _obs(nav.FollowersRecoverySurface.CANDIDATE_PROFILE),
                _obs(nav.FollowersRecoverySurface.FOLLOWERS_LIST),
            ]
        )

        def observe(*_args: object, **_kwargs: object):
            order.append("observe")
            return next(surfaces)

        original_press = device.press

        def press(key: str) -> None:
            order.append("back")
            original_press(key)

        device.press = press  # type: ignore[method-assign]
        with mock.patch.object(
            nav, "classify_followers_recovery_surface", side_effect=observe
        ):
            ok, reason, detail = (
                nav.recover_followers_after_candidate_profile_open_not_confirmed(
                    device, "source.ct", PKG, candidate_username="candidate"
                )
            )
        self.assertTrue(ok)
        self.assertEqual(reason, "followers_already_present")
        self.assertEqual(detail["navigation_actions_used"], 1)
        self.assertEqual(order, ["observe", "back", "observe"])

    def test_post_surface_reclassifies_between_each_back(self) -> None:
        device = _Device()
        order: list[str] = []
        surfaces = iter(
            [
                _obs(nav.FollowersRecoverySurface.POST_SURFACE),
                _obs(nav.FollowersRecoverySurface.CANDIDATE_PROFILE),
                _obs(nav.FollowersRecoverySurface.FOLLOWERS_LIST),
            ]
        )

        def observe(*_args: object, **_kwargs: object):
            order.append("observe")
            return next(surfaces)

        def press(_key: str) -> None:
            order.append("back")

        device.press = press  # type: ignore[method-assign]
        with mock.patch.object(
            nav, "classify_followers_recovery_surface", side_effect=observe
        ):
            ok, _reason, detail = (
                nav.recover_followers_after_candidate_profile_open_not_confirmed(
                    device, "source.ct", PKG, candidate_username="candidate"
                )
            )
        self.assertTrue(ok)
        self.assertEqual(detail["navigation_actions_used"], 2)
        self.assertEqual(
            order,
            ["observe", "back", "observe", "back", "observe"],
        )

    def test_posts_action_bar_is_not_misclassified_as_candidate_without_hint(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav,
            "detect_followers_list_screen_fresh",
            return_value=({"is_followers_list": False, "action_bar_title": "Posts"}, "<xml/>"),
        ), mock.patch.object(
            nav,
            "_ui_post_viewer_open_like_unlike_fast",
            return_value=(True, "xml_like", {}, {}),
        ):
            surface, detail = nav.classify_followers_recovery_surface(
                device, "source.ct", PKG
            )
        self.assertEqual(surface, nav.FollowersRecoverySurface.POST_SURFACE)
        self.assertEqual(detail["reason"], "trusted_post_surface_proved")

    def test_search_surface_uses_exact_source_recovery_then_reopen(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav,
            "classify_followers_recovery_surface",
            return_value=_obs(nav.FollowersRecoverySurface.EXPLORE_SEARCH),
        ), mock.patch.object(
            nav,
            "_followers_entry_maybe_recover_ct_profile_from_search_surface",
            return_value=(True, "profile_recovery_confirmed"),
        ) as search_recover, mock.patch.object(
            nav,
            "open_followers_list_from_profile",
            return_value=(True, {}),
        ) as reopen:
            ok, reason, _detail = (
                nav.recover_followers_after_candidate_profile_open_not_confirmed(
                    device, "source.ct", PKG, candidate_username="candidate"
                )
            )
        self.assertTrue(ok)
        self.assertEqual(reason, "reopen_after_proved_search_recovery")
        self.assertEqual(device.events, [])
        search_recover.assert_called_once()
        reopen.assert_called_once()

    def test_unknown_surface_fails_closed_without_ui_action(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav,
            "classify_followers_recovery_surface",
            return_value=_obs(nav.FollowersRecoverySurface.UNKNOWN),
        ) as classify:
            ok, reason, detail = (
                nav.recover_followers_after_candidate_profile_open_not_confirmed(
                    device, "source.ct", PKG, candidate_username="candidate"
                )
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "followers_recovery_surface_unproved")
        self.assertEqual(device.events, [])
        self.assertEqual(detail["navigation_actions_used"], 0)
        self.assertEqual(classify.call_count, 4)

    def test_recovery_failure_is_resumable_and_never_target_completed(self) -> None:
        summary = runner._followers_wrong_depth_recovery_failure_summary(
            processed=48,
            follows_completed_count=48,
            follows_goal_effective=50,
            global_follows_goal_effective=50,
            target_follow_budget_effective=50,
            source_profile_username="aleksborys",
            recovery_reason="followers_recovery_surface_unproved",
        )
        self.assertEqual(summary["exit_code"], 42)
        self.assertEqual(summary["follow_session_outcome"], "partial_resumable")
        self.assertEqual(
            summary["follow_stop_reason"], "followers_recovery_surface_unproved"
        )
        self.assertFalse(summary["target_completed"])
        self.assertFalse(summary["target_exhausted"])
        self.assertTrue(summary["safe_to_resume"])


if __name__ == "__main__":
    unittest.main()
