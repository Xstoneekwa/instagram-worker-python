from __future__ import annotations

import unittest
from unittest import mock

import instagram_navigation as nav


PKG = "com.instagram.androie"
MAIN = "com.instagram.mainactivity.InstagramMainActivity"
MODAL = "com.instagram.modal.ModalActivity"


class _MissingSelector:
    def wait(self, timeout: float = 0.0) -> bool:
        return False


class _Device:
    def __init__(self, *, package: str = PKG, activity: str = MAIN) -> None:
        self.package = package
        self.activity = activity
        self.clicks: list[tuple[int, int]] = []

    def app_current(self) -> dict[str, str]:
        return {"package": self.package, "activity": self.activity}

    def window_size(self) -> tuple[int, int]:
        return 1080, 2340

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def press(self, _key: str) -> None:
        return None

    def __call__(self, **_kwargs: object) -> _MissingSelector:
        return _MissingSelector()


class FollowProfileIdentityBoundaryV1Test(unittest.TestCase):
    def test_exact_username_on_main_activity_is_confirmed(self) -> None:
        ok, meta = nav._expected_profile_identity_boundary(
            _Device(), "candidate_exact", PKG, observed_username="candidate_exact"
        )
        self.assertTrue(ok)
        self.assertEqual(meta["identity_method"], "exact_observed_username")

    def test_dm_modal_never_becomes_profile_from_generic_chrome(self) -> None:
        ok, meta = nav._expected_profile_identity_boundary(
            _Device(activity=MODAL),
            "candidate_exact",
            PKG,
            allow_stage_scoped_transition=True,
            stage_scoped_transition_proven=True,
            stage_binding_id="visual-candidate-1",
        )
        self.assertFalse(ok)
        self.assertEqual(meta["reason"], "profile_identity_dangerous_activity")

    def test_wrong_own_profile_never_matches_expected_source(self) -> None:
        ok, meta = nav._expected_profile_identity_boundary(
            _Device(),
            "baocanteenlille",
            PKG,
            observed_username="rex_gen_boost_ai",
        )
        self.assertFalse(ok)
        self.assertEqual(meta["reason"], "profile_identity_username_mismatch")

    def test_visual_transition_can_cover_temporarily_missing_header(self) -> None:
        ok, meta = nav._expected_profile_identity_boundary(
            _Device(),
            "candidate_exact",
            PKG,
            allow_stage_scoped_transition=True,
            stage_scoped_transition_proven=True,
            stage_binding_id="visual-candidate-1",
        )
        self.assertTrue(ok)
        self.assertEqual(meta["identity_method"], "stage_scoped_visual_transition")

    def test_non_visual_candidate_cannot_use_stage_exception(self) -> None:
        ok, meta = nav._expected_profile_identity_boundary(
            _Device(),
            "candidate_exact",
            PKG,
            stage_scoped_transition_proven=True,
        )
        self.assertFalse(ok)
        self.assertEqual(meta["reason"], "profile_identity_username_unproven")

    def test_non_visual_open_rejects_nab_dm_false_positive(self) -> None:
        device = _Device(activity=MODAL)
        with mock.patch.object(nav, "verify_profile", return_value=True), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", return_value=""
        ), mock.patch.object(nav.time, "sleep", return_value=None):
            ok = nav.open_follower_profile_from_list(
                device,
                {"username": "sefalocks", "row_center": [320, 420]},
                source_profile_username="nab_youss",
                pkg=PKG,
            )
        self.assertFalse(ok)
        self.assertEqual(device.clicks, [(320, 420)])

    def test_return_ct_rejects_rex_wrong_profile_before_reopen(self) -> None:
        device = _Device()
        with mock.patch.object(
            nav, "detect_followers_list_screen", return_value={"is_followers_list": False}
        ), mock.patch.object(nav, "verify_profile", return_value=True), mock.patch.object(
            nav,
            "read_current_profile_username_for_follow_gate",
            return_value="rex_gen_boost_ai",
        ), mock.patch.object(
            nav, "_followers_entry_search_surface_recovery_fallback_enabled", return_value=False
        ), mock.patch.object(
            nav, "open_followers_list_from_profile"
        ) as reopen, mock.patch.object(nav.time, "sleep", return_value=None):
            ok, reason = nav.return_to_followers_list(
                device, "baocanteenlille", PKG, max_retries=0
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "failed")
        reopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
