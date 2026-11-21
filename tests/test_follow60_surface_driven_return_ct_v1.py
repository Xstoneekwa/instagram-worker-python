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
    def test_private_return_publishes_only_fresh_exact_ct_and_bound_activity(self) -> None:
        package, activity = "com.instagram.androig", ".activity.MainTabActivity"
        for title, actual_package, native_authority, xml, reusable in (
            ("ct", package, True, "<hierarchy/>", True),
            ("foreign", package, True, "<hierarchy/>", False),
            ("ct", "foreign", True, "<hierarchy/>", False),
            ("ct", package, False, "<hierarchy/>", False),
            ("ct", package, True, "", False),
        ):
            with self.subTest(title=title, package=actual_package, authority=native_authority, xml=bool(xml)):
                proof = {"observed_at": 1, "detection": {"stale": True}}
                det = {
                    "is_followers_list": True, "action_bar_title": title,
                    "current_package": actual_package, "current_activity": activity,
                    "open_detection_method": "own_unified_follow_list",
                }
                device = _Device()
                with (
                    mock.patch.object(nav, "detect_followers_list_screen_fresh", return_value=(det, xml)) as fresh,
                    mock.patch.object(nav, "_follow_446_activity_authority", return_value=native_authority),
                    mock.patch("instagram_human_confirmation_challenge.guard_instagram_human_confirmation"),
                    mock.patch.object(nav, "followers_session_mark_list_committed_open"),
                    mock.patch.object(nav, "log"),
                ):
                    nav.return_to_followers_list(device, "ct", package, exact_return_snapshot=proof)
                fresh.assert_called_once()
                self.assertEqual(device.backs, 0)
                self.assertEqual(bool(proof), reusable)
                if reusable:
                    self.assertEqual(proof["detection"], det)

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

    def test_exact_mute_or_no_posts_return_uses_one_back_and_keeps_canonical_fallback(self):
        proof = {"is_followers_list": True, "action_bar_title": "ct", "current_package": PKG}
        for origin, xml_valid in (("mute", True), ("mute", False), ("no_posts", True),
                                  ("no_posts", False), ("other", False)):
            with self.subTest(origin=origin, xml_valid=xml_valid):
                device = _Device()
                with mock.patch.object(nav, "verify_app_foreground", return_value=True), mock.patch.object(
                    nav, "_FOLLOW_ROW_SESSION",
                ) as session, mock.patch.object(
                    nav, "_return_ct_446_xml_snapshot", return_value=proof if xml_valid else {},
                ) as fast, mock.patch.object(
                    nav, "detect_followers_list_screen", return_value=proof,
                ) as fallback:
                    session.get.return_value = ({"package": PKG}, "emitter")
                    ok, method, failure = nav.post_follow_controlled_return_to_followers_list(
                        device, pkg=PKG, source_profile_username="ct", follower_username="candidate",
                        visual_candidate_id="row", det=None, compact_after_follow_verified_mute=True,
                        immediate_candidate_back_proof=True, post_mute_exact_return=origin == "mute",
                        post_no_posts_exact_return=origin == "no_posts",
                    )
                self.assertEqual((ok, method, failure), (True, "fresh_candidate_proof_one_back_then_exact_ct", None))
                self.assertEqual(device.backs, 1)
                self.assertEqual(fast.call_count, int(origin in ("mute", "no_posts")))
                self.assertEqual(fallback.call_count, int(not xml_valid))


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


class Exact446ReturnTests(unittest.TestCase):
    def drive(self, surfaces, package=PKG):
        device = _Device()
        with mock.patch.object(nav, "follow_446_session", return_value=True), \
             mock.patch.object(nav, "classify_followers_recovery_surface", side_effect=surfaces) as observe, \
             mock.patch.object(nav, "_followers_current_pkg_activity", return_value=_meta(package)), \
             mock.patch.object(nav, "followers_invalidate_surface_for_navigation"), \
             mock.patch.object(nav, "followers_session_mark_list_committed_open"):
            ok, why, details = nav.recover_followers_after_candidate_profile_open_not_confirmed(
                device, "source", PKG, candidate_username="candidate", require_bound_446_surface=True)
        self.assertTrue(all(c.kwargs["candidate_username"] == "candidate" for c in observe.call_args_list))
        return ok, why, device.backs, details

    def test_before_post_no_viewer_back_and_already_returned_no_action(self):
        surface = nav.FollowersRecoverySurface
        for sequence, backs in (([surface.FOLLOWERS_LIST], 0),
                                ([surface.CANDIDATE_PROFILE, surface.FOLLOWERS_LIST], 1),
                                ([surface.POST_SURFACE, surface.CANDIDATE_PROFILE, surface.FOLLOWERS_LIST], 2)):
            rows = [(s, {"target_identity_proved": s == surface.FOLLOWERS_LIST}) for s in sequence]
            ok, _, actual, details = self.drive(rows)
            self.assertTrue(ok)
            self.assertEqual(backs, actual)
            self.assertEqual(len(details["observations"]), backs + 1)

    def test_wrong_clone_and_wrong_followers_list_have_no_transport(self):
        surface = nav.FollowersRecoverySurface
        self.assertEqual(self.drive([(surface.POST_SURFACE, {})], "com.instagram.other")[:3],
                         (False, "followers_recovery_package_unproved", 0))
        self.assertEqual(self.drive([(surface.FOLLOWERS_LIST, {"target_identity_proved": False})])[:3],
                         (False, "followers_recovery_target_unproved", 0))

    def test_direct_viewer_return_refuses_foreign_clone_before_back(self):
        device = _Device()
        with mock.patch.object(nav, "_followers_current_pkg_activity", return_value=_meta("com.instagram.other")):
            result = nav.visual_return_to_profile_from_post(device, expected_profile_username="candidate")
        self.assertEqual(result["failure_reason"], "profile_identity_package_mismatch")
        self.assertEqual(device.backs, 0)
