from __future__ import annotations

import time
import unittest
from unittest import mock

import instagram_navigation as nav


PKG = "com.instagram.android"
ACTIVITY = "com.instagram.mainactivity.InstagramMainActivity"
RID = f"{PKG}:id/row_search_user_username"


class _Selector:
    def __init__(self, elements):
        self._elements = list(elements)

    def all(self):
        return list(self._elements)


class _ExactRow:
    def __init__(self, text: str = "Exact.Target") -> None:
        self.text = text
        self.get_text_calls = 0
        self.info_calls = 0

    def get_text(self):
        self.get_text_calls += 1
        return self.text

    @property
    def info(self):
        self.info_calls += 1
        return {
            "bounds": {"left": 180, "top": 560, "right": 760, "bottom": 650},
            "resourceName": RID,
            "className": "android.widget.TextView",
        }


class _RowDevice:
    def __init__(self, row: _ExactRow) -> None:
        self.row = row
        self.window_size_calls = 0

    def __call__(self, **kwargs):
        if kwargs.get("resourceId") == RID:
            return _Selector([self.row])
        return _Selector([])

    def window_size(self):
        self.window_size_calls += 1
        return 1080, 2400


class _CurrentDevice:
    def __init__(self) -> None:
        self.app_current_calls = 0

    def app_current(self):
        self.app_current_calls += 1
        return {"package": PKG, "activity": ACTIVITY}


class CtResumeCplusResidualOptimizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old = {
            "instagram_package": nav.config.INSTAGRAM_PACKAGE,
            "active": nav._FOLLOW_CT_SEARCH_CONTEXT_ACTIVE,
            "context": nav._FOLLOW_CT_SEARCH_CONTEXT_GENERATION,
            "navigation": nav._FOLLOW_CT_SEARCH_NAVIGATION_GENERATION,
            "surface": nav._FOLLOW_CT_SEARCH_SURFACE_GENERATION,
            "hierarchy": nav._FOLLOW_CT_SEARCH_HIERARCHY_GENERATION,
            "account_scope": nav._FOLLOW_CT_SEARCH_ACCOUNT_SCOPE,
            "consumed_surface": nav._FOLLOW_CT_SEARCH_SURFACE_PROOF_CONSUMED_GENERATION,
        }
        nav.config.INSTAGRAM_PACKAGE = PKG
        nav._FOLLOW_CT_SEARCH_CONTEXT_ACTIVE = True
        nav._FOLLOW_CT_SEARCH_CONTEXT_GENERATION = 31
        nav._FOLLOW_CT_SEARCH_NAVIGATION_GENERATION = 47
        nav._FOLLOW_CT_SEARCH_SURFACE_GENERATION = 59
        nav._FOLLOW_CT_SEARCH_HIERARCHY_GENERATION = 71
        nav._FOLLOW_CT_SEARCH_ACCOUNT_SCOPE = "account-a"
        nav._FOLLOW_CT_SEARCH_SURFACE_PROOF_CONSUMED_GENERATION = 0

    def tearDown(self) -> None:
        nav.config.INSTAGRAM_PACKAGE = self._old["instagram_package"]
        nav._FOLLOW_CT_SEARCH_CONTEXT_ACTIVE = self._old["active"]
        nav._FOLLOW_CT_SEARCH_CONTEXT_GENERATION = self._old["context"]
        nav._FOLLOW_CT_SEARCH_NAVIGATION_GENERATION = self._old["navigation"]
        nav._FOLLOW_CT_SEARCH_SURFACE_GENERATION = self._old["surface"]
        nav._FOLLOW_CT_SEARCH_HIERARCHY_GENERATION = self._old["hierarchy"]
        nav._FOLLOW_CT_SEARCH_ACCOUNT_SCOPE = self._old["account_scope"]
        nav._FOLLOW_CT_SEARCH_SURFACE_PROOF_CONSUMED_GENERATION = self._old[
            "consumed_surface"
        ]

    def _trace(self):
        return {
            "follow_ct": True,
            "username": "@exact.target",
            "expected_package": PKG,
            "hierarchy_generation": nav._FOLLOW_CT_SEARCH_HIERARCHY_GENERATION,
            "navigation_generation": nav._FOLLOW_CT_SEARCH_NAVIGATION_GENERATION,
            "search_surface_generation": nav._FOLLOW_CT_SEARCH_SURFACE_GENERATION,
            "search_context_generation": nav._FOLLOW_CT_SEARCH_CONTEXT_GENERATION,
            "trace_state": {},
        }

    def test_exact_row_proof_removes_duplicate_downstream_element_reads(self) -> None:
        row = _ExactRow()
        device = _RowDevice(row)
        element, proof = nav.find_first_row_search_username_hot(
            device,
            "@EXACT.TARGET",
            trace_context=self._trace(),
            return_exact_proof=True,
        )

        self.assertIs(row, element)
        self.assertIsNotNone(proof)
        self.assertEqual(1, row.get_text_calls)
        self.assertEqual(1, row.info_calls)
        self.assertEqual(1, device.window_size_calls)

        evaluated = nav.evaluate_row_search_username_element(
            device,
            row,
            "exact.target",
            mixed_results=True,
            follow_ct_search_context=True,
            resource_id_hint=RID,
            exact_row_proof=proof,
        )

        self.assertTrue(evaluated["accept"])
        self.assertEqual(1, row.get_text_calls)
        self.assertEqual(1, row.info_calls)
        self.assertEqual(1, device.window_size_calls)

    def test_hierarchy_generation_change_invalidates_proof_and_uses_fallback(self) -> None:
        row = _ExactRow()
        device = _RowDevice(row)
        _, proof = nav.find_first_row_search_username_hot(
            device,
            "exact.target",
            trace_context=self._trace(),
            return_exact_proof=True,
        )
        nav._advance_follow_ct_search_hierarchy_generation()

        evaluated = nav.evaluate_row_search_username_element(
            device,
            row,
            "exact.target",
            mixed_results=True,
            follow_ct_search_context=True,
            resource_id_hint=RID,
            exact_row_proof=proof,
        )

        self.assertTrue(evaluated["accept"])
        self.assertGreaterEqual(row.get_text_calls, 2)
        self.assertGreaterEqual(row.info_calls, 2)
        self.assertGreaterEqual(device.window_size_calls, 2)

    def test_navigation_generation_change_invalidates_exact_row_proof(self) -> None:
        row = _ExactRow()
        device = _RowDevice(row)
        _, proof = nav.find_first_row_search_username_hot(
            device,
            "exact.target",
            trace_context=self._trace(),
            return_exact_proof=True,
        )
        nav._advance_follow_ct_search_navigation_generation("test_navigation")

        valid, reason = nav._exact_search_row_proof_valid(
            proof,
            username="exact.target",
            expected_package=PKG,
        )

        self.assertFalse(valid)
        self.assertEqual("navigation_generation_changed", reason)

    def test_account_scope_change_invalidates_exact_row_proof(self) -> None:
        row = _ExactRow()
        device = _RowDevice(row)
        _, proof = nav.find_first_row_search_username_hot(
            device,
            "exact.target",
            trace_context=self._trace(),
            return_exact_proof=True,
        )
        nav._FOLLOW_CT_SEARCH_ACCOUNT_SCOPE = "account-b"

        valid, reason = nav._exact_search_row_proof_valid(
            proof,
            username="exact.target",
            expected_package=PKG,
        )

        self.assertFalse(valid)
        self.assertEqual("account_scope_changed", reason)

    def test_partial_username_never_creates_exact_row_proof(self) -> None:
        row = _ExactRow(text="exact")
        device = _RowDevice(row)
        element, proof = nav.find_first_row_search_username_hot(
            device,
            "exact.target",
            trace_context=self._trace(),
            return_exact_proof=True,
        )

        self.assertIsNone(element)
        self.assertIsNone(proof)

    def test_wrong_exact_username_never_reuses_proof(self) -> None:
        row = _ExactRow()
        device = _RowDevice(row)
        _, proof = nav.find_first_row_search_username_hot(
            device,
            "exact.target",
            trace_context=self._trace(),
            return_exact_proof=True,
        )

        valid, reason = nav._exact_search_row_proof_valid(
            proof,
            username="other.target",
            expected_package=PKG,
        )

        self.assertFalse(valid)
        self.assertEqual("exact_username_mismatch", reason)

    def _token(self, **overrides):
        values = {
            "package": PKG,
            "activity": ACTIVITY,
            "activity_family": "InstagramMainActivity",
            "account_scope": nav._FOLLOW_CT_SEARCH_ACCOUNT_SCOPE,
            "search_context_generation": nav._FOLLOW_CT_SEARCH_CONTEXT_GENERATION,
            "navigation_generation": nav._FOLLOW_CT_SEARCH_NAVIGATION_GENERATION,
            "surface_generation": nav._FOLLOW_CT_SEARCH_SURFACE_GENERATION,
            "observed_at_monotonic": time.monotonic(),
        }
        values.update(overrides)
        return nav.SearchSurfaceProofToken(**values)

    def test_valid_search_surface_token_skips_redundant_app_current(self) -> None:
        device = _CurrentDevice()
        nav._mark_search_surface_ok(device, PKG, proof_token=self._token())
        self.assertEqual(0, device.app_current_calls)

    def test_search_surface_token_is_single_transition_only(self) -> None:
        device = _CurrentDevice()
        token = self._token()

        nav._mark_search_surface_ok(device, PKG, proof_token=token)
        nav._mark_search_surface_ok(device, PKG, proof_token=token)

        self.assertEqual(1, device.app_current_calls)

    def test_absent_or_stale_search_surface_token_uses_existing_fallback(self) -> None:
        absent_device = _CurrentDevice()
        nav._mark_search_surface_ok(absent_device, PKG)
        self.assertEqual(1, absent_device.app_current_calls)

        stale_device = _CurrentDevice()
        nav._mark_search_surface_ok(
            stale_device,
            PKG,
            proof_token=self._token(observed_at_monotonic=time.monotonic() - 2.0),
        )
        self.assertEqual(1, stale_device.app_current_calls)

    def test_package_activity_and_navigation_mismatch_use_existing_fallback(self) -> None:
        cases = (
            self._token(package="com.example.wrong"),
            self._token(
                activity="com.instagram.direct.DirectActivity",
                activity_family="DirectActivity",
            ),
            self._token(
                navigation_generation=nav._FOLLOW_CT_SEARCH_NAVIGATION_GENERATION - 1
            ),
        )
        for token in cases:
            with self.subTest(token=token):
                device = _CurrentDevice()
                nav._mark_search_surface_ok(device, PKG, proof_token=token)
                self.assertEqual(1, device.app_current_calls)

    def test_strict_search_proof_reuses_the_same_app_current_sample(self) -> None:
        device = _CurrentDevice()
        edittext = mock.Mock()
        proof_out = {}
        with mock.patch.object(nav, "_edittext_package_name", return_value=PKG), mock.patch.object(
            nav, "_search_edittext_text_strip", return_value="Search"
        ), mock.patch.object(
            nav, "_visible_text_suggests_android_launcher_search", return_value=False
        ), mock.patch.object(
            nav, "is_followers_list_surface_quick", return_value=False
        ):
            ok, reason = nav.instagram_search_surface_strict_ok(
                device,
                edittext,
                pkg=PKG,
                proof_out=proof_out,
            )
            nav._mark_search_surface_ok(
                device,
                PKG,
                proof_token=proof_out.get("token"),
            )

        self.assertTrue(ok, reason)
        self.assertIsNotNone(proof_out.get("token"))
        self.assertEqual(1, device.app_current_calls)


if __name__ == "__main__":
    unittest.main()
