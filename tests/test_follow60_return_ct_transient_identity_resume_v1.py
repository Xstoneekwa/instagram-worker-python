from __future__ import annotations

import unittest
from unittest import mock

import account_session_orchestrator as aso
import instagram_navigation as nav


class _Device:
    def app_current(self):
        return {
            "package": "com.instagram.android",
            "activity": "com.instagram.mainactivity.InstagramMainActivity",
        }

    def press(self, _key):
        return None


class Follow60ReturnCtTransientIdentityResumeTests(unittest.TestCase):
    def test_ct_username_render_is_reobserved_without_second_ui_action(self):
        device = _Device()
        observed = iter(["", "", "source.ct"])

        with mock.patch.object(nav, "detect_followers_list_screen_fresh", return_value=({"is_followers_list": False}, "")), mock.patch.object(
            nav, "verify_profile", return_value=True
        ), mock.patch.object(
            nav,
            "read_current_profile_username_for_follow_gate",
            side_effect=lambda _d: next(observed),
        ), mock.patch.object(
            nav, "open_followers_list_from_profile", return_value=(True, {})
        ) as reopen, mock.patch.object(
            nav, "_profile_signal_b_username_top_band", return_value=None
        ), mock.patch.object(
            nav.time, "sleep", return_value=None
        ):
            ok, method = nav.return_to_followers_list(
                device,
                "source.ct",
                "com.instagram.android",
                max_retries=0,
            )

        self.assertTrue(ok)
        self.assertEqual(method, "reopen_from_source_profile")
        reopen.assert_called_once()

    def test_ct_username_missing_remains_fail_closed_after_bounded_rechecks(self):
        device = _Device()
        reads = []

        def missing(_device):
            reads.append(1)
            return ""

        with mock.patch.object(nav, "detect_followers_list_screen_fresh", return_value=({"is_followers_list": False}, "")), mock.patch.object(
            nav, "verify_profile", return_value=True
        ), mock.patch.object(
            nav, "read_current_profile_username_for_follow_gate", side_effect=missing
        ), mock.patch.object(
            nav, "open_followers_list_from_profile"
        ) as reopen, mock.patch.object(
            nav, "_profile_signal_b_username_top_band", return_value=None
        ), mock.patch.object(
            nav.time, "sleep", return_value=None
        ), mock.patch.object(
            nav, "_followers_entry_search_surface_recovery_fallback_enabled", return_value=False
        ):
            ok, method = nav.return_to_followers_list(
                device,
                "source.ct",
                "com.instagram.android",
                max_retries=0,
            )

        self.assertFalse(ok)
        self.assertEqual(method, "followers_surface_reacquisition_unproved")
        self.assertEqual(len(reads), 4)
        reopen.assert_not_called()

    def test_transient_return_ct_exit_is_resumable_with_remaining_quota(self):
        termination = aso._session_termination_class(
            session_status="failed",
            follow_phase_executed=True,
            follow_exit_code=42,
            follow_quota_remaining=78,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
            follow_phase_skipped_reason=None,
            transition_reason="welcome_disabled_bypass",
            follow_session_outcome="partial_resumable",
        )
        eligibility = aso._restart_eligibility(
            session_termination_class=termination,
            follow_quota_remaining=78,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
        )

        self.assertEqual(termination, "partial_resumable")
        self.assertEqual(eligibility, ("eligible", "quota_remaining"))

    def test_other_exit_42_remains_non_recoverable(self):
        termination = aso._session_termination_class(
            session_status="failed",
            follow_phase_executed=True,
            follow_exit_code=42,
            follow_quota_remaining=78,
            follow_to_unfollow_diagnostic={},
            follow_to_unfollow_real={},
            follow_phase_skipped_reason=None,
            transition_reason="welcome_disabled_bypass",
            follow_session_outcome="",
        )
        self.assertEqual(termination, "non_recoverable_failure")

    @staticmethod
    def _ct_xml(pkg, title="source.ct", *, selected="true"):
        return f"""<hierarchy>
            <node package="{pkg}" resource-id="{pkg}:id/action_bar_title" text="{title}"/>
            <node package="{pkg}" resource-id="{pkg}:id/unified_follow_list_tab_layout">
                <node text="8 followers" selected="{selected}"/>
            </node>
            <node class="androidx.recyclerview.widget.RecyclerView">
                <node package="{pkg}" resource-id="{pkg}:id/follow_list_container">
                    <node package="{pkg}" resource-id="{pkg}:id/follow_list_username" text="next.user"/>
                </node>
            </node>
        </hierarchy>"""

    def _fresh_return(self, pkg, hierarchy, *, current=None, activity_ok=True,
                      generations=(1, 1), stopped=(False, False)):
        device = _Device()
        with mock.patch.object(nav.config, "INSTAGRAM_PACKAGE", pkg), mock.patch.object(
            nav, "_followers_current_pkg_activity", return_value={
                "current_package": current or pkg, "current_activity": ".activity.MainTabActivity",
            }
        ), mock.patch.object(
            nav, "_follow_446_activity_authority", return_value=activity_ok,
        ), mock.patch.object(nav, "_followers_surface_scope"), mock.patch.object(
            nav.followers_proof, "current_generations", side_effect=generations,
        ), mock.patch.object(nav.device_action_latch, "stop_requested", side_effect=stopped), mock.patch.object(
            nav, "followers_dump_fresh_hierarchy", return_value=hierarchy,
        ) as read:
            output = nav._return_ct_446_xml_snapshot(device, package=pkg, source="source.ct", candidate="different")
            return output, read.call_count

    def test_exact_clone_or_primary_fresh_list_with_one_capture(self):
        for pkg in ("com.instagram.android", "org.assignedfixture.slot7"):
            with self.subTest(pkg=pkg):
                det, reads = self._fresh_return(pkg, self._ct_xml(pkg))
                self.assertEqual(reads, 1)
                self.assertTrue(det["is_followers_list"])
                self.assertEqual(det["action_bar_title"], "source.ct")
                self.assertEqual(det["visible_usernames_sample"], ["next.user"])

    def test_exact_ct_proof_denies_stale_foreign_and_contradictory_surfaces(self):
        pkg = "org.assignedfixture.slot7"
        valid = self._ct_xml(pkg)
        contradictory = valid.replace(
            "</hierarchy>",
            f'<node package="{pkg}" resource-id="{pkg}:id/action_bar_title" text="foreign.ct"/></hierarchy>',
        )
        cases = (
            (valid, {"current": "foreign.pkg"}, 0),
            (valid, {"activity_ok": False}, 0),
            (valid, {"generations": (1, 2)}, 1),
            (valid, {"stopped": (False, True)}, 1),
            (valid, {"stopped": (True,)}, 0),
            (self._ct_xml(pkg, "different"), {}, 1),
            (self._ct_xml(pkg, ""), {}, 1),
            (self._ct_xml(pkg, selected="false"), {}, 1),
            (self._ct_xml("foreign.pkg"), {}, 1),
            (contradictory, {}, 1),
        )
        for hierarchy, options, expected_reads in cases:
            with self.subTest(options=options, hierarchy=hierarchy[:100]):
                det, reads = self._fresh_return(pkg, hierarchy, **options)
                self.assertFalse(det)
                self.assertEqual(reads, expected_reads)


if __name__ == "__main__":
    unittest.main()
