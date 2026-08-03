from __future__ import annotations

import unittest
from unittest import mock

import instagram_navigation as nav


PKG = "com.instagram.android"
ACT = "com.instagram.mainactivity.InstagramMainActivity"


class _Node:
    def __init__(self, text: str) -> None:
        self.text = text

    def exists(self, timeout: float = 0.0) -> bool:
        return bool(self.text)

    def get_text(self) -> str:
        return self.text


class _Device:
    def __init__(self, title: str = "candidate") -> None:
        self.title = title
        self.backs = 0
        self.screenshots = 0
        self.dumps = 0

    def __call__(self, **kwargs: object) -> _Node:
        return _Node(self.title if "resourceIdMatches" in kwargs else "")

    def press(self, key: str) -> None:
        self.backs += 1
        self.title = "candidate"

    def screenshot(self, *args: object, **kwargs: object) -> None:
        self.screenshots += 1

    def dump_hierarchy(self, *args: object, **kwargs: object) -> str:
        self.dumps += 1
        return "<hierarchy/>"


def _meta(package: str = PKG, activity: str = ACT) -> dict[str, str]:
    return {"current_package": package, "current_activity": activity}


class SurfaceProbeContractTest(unittest.TestCase):
    def _probe(
        self,
        title: str,
        *,
        package: str = PKG,
        activity: str = ACT,
    ) -> dict[str, object]:
        with mock.patch.object(
            nav, "_followers_current_pkg_activity", return_value=_meta(package, activity)
        ):
            return nav._post_follow_like_failure_surface_probe(
                _Device(title), pkg=PKG, follower_username="candidate"
            )

    def test_candidate_profile_is_exact(self) -> None:
        self.assertEqual(self._probe("candidate")["surface"], "candidate_profile_exact")

    def test_post_viewer_posts_title_is_exact(self) -> None:
        self.assertEqual(self._probe("Posts")["surface"], "post_viewer_exact")

    def test_unknown_title_is_ambiguous(self) -> None:
        self.assertEqual(self._probe("Explore")["surface"], "ambiguous")

    def test_package_mismatch_fails_closed(self) -> None:
        self.assertEqual(
            self._probe("candidate", package="com.other")["surface"],
            "package_mismatch",
        )

    def test_activity_mismatch_fails_closed(self) -> None:
        self.assertEqual(
            self._probe("candidate", activity="com.instagram.StoryViewer")["surface"],
            "activity_mismatch",
        )


class SurfaceDrivenPreparationTest(unittest.TestCase):
    def _prepare(self, device: _Device) -> dict[str, object]:
        return nav._post_follow_like_failure_prepare_fast_return(
            device,
            pkg=PKG,
            source_profile_username="ct",
            follower_username="candidate",
            visual_candidate_id="vcid",
        )

    def test_candidate_profile_uses_fast_route_without_back(self) -> None:
        device = _Device("candidate")
        with mock.patch.object(nav, "_followers_current_pkg_activity", return_value=_meta()):
            out = self._prepare(device)
        self.assertEqual(out["route"], "fast_candidate_profile")
        self.assertEqual(device.backs, 0)

    def test_viewer_uses_one_back_then_fast_route(self) -> None:
        device = _Device("Posts")
        with mock.patch.object(nav, "_followers_current_pkg_activity", return_value=_meta()), mock.patch.object(
            nav, "verify_app_foreground", return_value=True
        ):
            out = self._prepare(device)
        self.assertEqual(out["route"], "fast_viewer_back_candidate_profile")
        self.assertEqual(device.backs, 1)

    def test_viewer_that_does_not_return_to_candidate_uses_recovery(self) -> None:
        device = _Device("Posts")
        device.press = mock.Mock(side_effect=lambda key: setattr(device, "title", "Explore"))
        with mock.patch.object(nav, "_followers_current_pkg_activity", return_value=_meta()), mock.patch.object(
            nav, "verify_app_foreground", return_value=True
        ):
            out = self._prepare(device)
        self.assertEqual(out["route"], "recovery")
        self.assertEqual(device.press.call_count, 1)

    def test_viewer_not_foreground_uses_recovery_without_back(self) -> None:
        device = _Device("Posts")
        with mock.patch.object(nav, "_followers_current_pkg_activity", return_value=_meta()), mock.patch.object(
            nav, "verify_app_foreground", return_value=False
        ):
            out = self._prepare(device)
        self.assertEqual(out["route"], "recovery")
        self.assertEqual(device.backs, 0)

    def test_fast_decision_adds_no_screenshot_or_xml(self) -> None:
        device = _Device("candidate")
        with mock.patch.object(nav, "_followers_current_pkg_activity", return_value=_meta()):
            out = self._prepare(device)
        self.assertTrue(out["candidate_profile_exact"])
        self.assertEqual(device.screenshots, 0)
        self.assertEqual(device.dumps, 0)

    def test_capped_return_forwards_immediate_candidate_proof(self) -> None:
        with mock.patch.object(
            nav,
            "post_follow_controlled_return_to_followers_list",
            return_value=(True, "fast", None),
        ) as controlled:
            result = nav._post_follow_likes_failure_return_ct_capped(
                _Device(),
                pkg=PKG,
                source_profile_username="ct",
                follower_username="candidate",
                visual_candidate_id="vcid",
                immediate_candidate_back_proof=True,
            )
        self.assertEqual(result, (True, "fast", None))
        self.assertTrue(controlled.call_args.kwargs["immediate_candidate_back_proof"])


if __name__ == "__main__":
    unittest.main()
