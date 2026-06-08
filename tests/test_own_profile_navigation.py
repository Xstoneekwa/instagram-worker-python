from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import own_profile_navigation as nav


class VerifyOwnProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.device = MagicMock()
        self.device.window_size.return_value = (1080, 2400)

    def _verify(self, *, expected: str = "j_automatise_pour_toi", **kwargs) -> tuple[bool, dict]:
        patches = {
            "detect_followers_list_screen": {"is_followers_list": False},
            "_followers_profile_tabs_visible": True,
            "_collect_profile_stats_band_texts": ["12 posts", "100 followers"],
            "_header_has_follow_others_cta": False,
            "_strict_own_profile_username_verified": (False, {}),
        }
        patches.update(kwargs)
        with (
            patch.object(
                nav,
                "detect_followers_list_screen",
                return_value=patches["detect_followers_list_screen"],
            ),
            patch.object(
                nav,
                "_followers_profile_tabs_visible",
                return_value=patches["_followers_profile_tabs_visible"],
            ),
            patch.object(
                nav,
                "_collect_profile_stats_band_texts",
                return_value=patches["_collect_profile_stats_band_texts"],
            ),
            patch.object(
                nav,
                "_header_has_follow_others_cta",
                return_value=patches["_header_has_follow_others_cta"],
            ),
            patch.object(
                nav,
                "_strict_own_profile_username_verified",
                return_value=patches["_strict_own_profile_username_verified"],
            ),
        ):
            return nav.verify_own_profile(self.device, expected)

    def test_follow_cta_ignored_when_strict_username_proof_ok(self) -> None:
        ok, meta = self._verify(
            _header_has_follow_others_cta=True,
            _strict_own_profile_username_verified=(
                True,
                {
                    "verify_profile_ok": True,
                    "verification_method": "own_profile_username_exact:title_resource_id",
                },
            ),
        )
        self.assertTrue(ok)
        self.assertTrue(meta["follow_cta_on_header"])
        self.assertEqual(meta["verification_method"], "own_profile_username_exact:title_resource_id")

    def test_follow_cta_rejected_without_username_proof(self) -> None:
        ok, meta = self._verify(
            _header_has_follow_others_cta=True,
            _strict_own_profile_username_verified=(False, {"verify_profile_ok": False}),
        )
        self.assertFalse(ok)
        self.assertTrue(meta["follow_cta_on_header"])
        self.assertFalse(meta.get("verify_profile_ok"))

    def test_external_profile_mismatch_with_follow_cta_rejected(self) -> None:
        ok, meta = self._verify(
            expected="other_user",
            _header_has_follow_others_cta=True,
            _strict_own_profile_username_verified=(False, {"verify_profile_ok": False}),
        )
        self.assertFalse(ok)
        self.assertTrue(meta["follow_cta_on_header"])

    def test_missing_strict_proof_and_no_follow_cta_uses_profile_chrome_fallback(self) -> None:
        ok, meta = self._verify(
            _strict_own_profile_username_verified=(False, {"verify_profile_ok": False}),
            _header_has_follow_others_cta=False,
        )
        self.assertTrue(ok)
        self.assertEqual(meta.get("verify_profile_fallback"), "profile_chrome_without_username_match")

    def test_missing_expected_username_and_no_signals_rejected(self) -> None:
        ok, meta = self._verify(
            expected="",
            _followers_profile_tabs_visible=False,
            _collect_profile_stats_band_texts=[],
            _strict_own_profile_username_verified=(False, {}),
        )
        self.assertFalse(ok)
        self.assertEqual(meta["expected_username"], "")


class StrictOwnProfileUsernameVerifiedTests(unittest.TestCase):
    def test_hierarchy_title_resource_id_matches(self) -> None:
        device = MagicMock()
        hierarchy = (
            '<hierarchy><node resource-id="com.instagram.androif:id/title" '
            'text="j_automatise_pour_toi"/></hierarchy>'
        )
        with (
            patch.object(nav, "verify_profile", return_value=False),
            patch("account_identity_guard._dump_hierarchy", return_value=hierarchy),
        ):
            ok, meta = nav._strict_own_profile_username_verified(device, "j_automatise_pour_toi")
        self.assertTrue(ok)
        self.assertEqual(meta["verification_method"], "own_profile_username_exact:title_resource_id")


if __name__ == "__main__":
    unittest.main()
