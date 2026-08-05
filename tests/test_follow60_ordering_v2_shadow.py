from __future__ import annotations

import unittest
from unittest import mock

import follow60_ordering_v2_shadow as shadow
import runner


ACCOUNT = "ba73eda4-d22a-4b93-9683-2af7b8aab764"


def _env(*, enabled: bool = True, account_ids: str = ACCOUNT) -> dict[str, str]:
    return {
        "FOLLOW60_ORDERING_V2_SHADOW_ENABLED": "1" if enabled else "0",
        "FOLLOW60_ORDERING_V2_SHADOW_ACCOUNT_IDS": account_ids,
    }


def _capture(xml: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "xml": xml,
        "xml_fingerprint": "fixture-only",
        "duration_ms": 100.0,
        "exact_identity": True,
        "profile_surface": True,
        "follow_cta_positive": True,
        "navigation_generation": "nav:1",
        "ui_generation": 1,
        "private_probe_payload": {"private_profile_detected": False},
    }
    payload.update(overrides)
    return payload


def _classify(xml: str, **capture_overrides: object) -> dict[str, object]:
    result = shadow.classify_existing_pre_follow_capture(
        _capture(xml, **capture_overrides),
        account_id=ACCOUNT,
        run_id="run-1",
        request_id="request-1",
        candidate_username="candidate",
        source_profile_username="source_ct",
        visual_candidate_id="xml_list:candidate",
        environ=_env(),
    )
    assert result is not None
    return result


class Follow60OrderingV2ShadowTests(unittest.TestCase):
    def test_disabled_by_default_and_allowlist_is_mandatory(self) -> None:
        self.assertFalse(shadow.enabled_for_account(ACCOUNT, environ={}))
        self.assertFalse(shadow.enabled_for_account(ACCOUNT, environ=_env(account_ids="")))
        self.assertFalse(shadow.enabled_for_account(ACCOUNT, environ=_env(account_ids="other")))
        self.assertTrue(shadow.enabled_for_account(ACCOUNT, environ=_env()))

    def test_direct_grid_safe_reuses_v1_classifier_without_acquisition(self) -> None:
        xml = """<hierarchy>
        <node text="candidate"/><node text="8 posts"/>
        <node resource-id="profile_tabs_container" bounds="[0,700][1080,850]">
          <node resource-id="profile_tab_icon_view" content-desc="Grid view"
                selected="true" bounds="[0,700][360,850]"/>
        </node>
        <node class="android.widget.ImageView" resource-id="profile_grid_media_0"
              content-desc="Post thumbnail, row 1, column 1" bounds="[0,900][360,1260]"/>
        <node class="android.widget.ImageView" resource-id="profile_grid_media_1"
              content-desc="Post thumbnail, row 1, column 2" bounds="[360,900][720,1260]"/>
        <node resource-id="bottom_navigation" bounds="[0,2200][1080,2340]"/>
        </hierarchy>"""
        out = _classify(xml)
        self.assertEqual("DIRECT_GRID_SAFE", out["classification"])
        self.assertTrue(out["eligible_for_ordering_v2"])
        self.assertEqual(0, out["acquisition_count"])
        self.assertEqual(0, out["extra_screenshots"])
        self.assertEqual(0, out["extra_xml"])
        self.assertFalse(out["behavior_changed"])

    def test_positive_posts_below_fold_is_not_eligible(self) -> None:
        xml = """<hierarchy><node bounds="[0,0][1080,2340]"/>
        <node text="candidate"/>
        <node resource-id="profile_header_count_container">
          <node text="14"/><node text="Posts"/>
        </node>
        <node text="Suggested for you"/>
        <node resource-id="profile_highlights_tray" content-desc="Story highlights"/>
        <node content-desc="Profile tab grid" selected="true"/>
        </hierarchy>"""
        out = _classify(xml)
        self.assertEqual("BELOW_FOLD", out["classification"])
        self.assertFalse(out["eligible_for_ordering_v2"])

    def test_no_posts_and_private_are_terminal_shadow_categories(self) -> None:
        no_posts_xml = """<hierarchy><node bounds="[0,0][1080,2340]"/>
        <node text="candidate"/>
        <node resource-id="profile_header_count_container">
          <node text="0"/><node text="Posts"/>
        </node><node content-desc="Profile tab grid" selected="true"/>
        </hierarchy>"""
        self.assertEqual("NO_POSTS", _classify(no_posts_xml)["classification"])
        private = _classify(
            '<hierarchy><node bounds="[0,0][1080,2340]"/></hierarchy>',
            ok=False,
            private_probe_payload={"private_profile_detected": True},
        )
        self.assertEqual("PRIVATE", private["classification"])
        self.assertFalse(private["eligible_for_ordering_v2"])

    def test_ambiguous_capture_fails_closed(self) -> None:
        out = _classify(
            '<hierarchy><node bounds="[0,0][1080,2340]"/></hierarchy>',
            ok=False,
            exact_identity=False,
        )
        self.assertEqual("AMBIGUOUS", out["classification"])
        self.assertFalse(out["eligible_for_ordering_v2"])

    def test_runner_bridge_is_observational_and_fail_open(self) -> None:
        with mock.patch.object(
            shadow,
            "classify_existing_pre_follow_capture",
            return_value={
                "schema": shadow.SCHEMA,
                "classification": "DIRECT_GRID_SAFE",
                "behavior_changed": False,
            },
        ), mock.patch.object(runner, "log") as log_mock:
            out = runner._record_follow60_ordering_v2_shadow_from_existing_capture(
                {"xml": "<hierarchy/>"},
                account_id=ACCOUNT,
                run_id="run-1",
                request_id="request-1",
                candidate_username="candidate",
                source_profile_username="source_ct",
                visual_candidate_id="xml_list:candidate",
            )
        self.assertEqual("DIRECT_GRID_SAFE", out["classification"])
        log_mock.assert_called_once_with(
            "info",
            "follow60_ordering_v2_shadow_evaluated",
            schema=shadow.SCHEMA,
            classification="DIRECT_GRID_SAFE",
            behavior_changed=False,
        )

        with mock.patch.object(
            shadow,
            "classify_existing_pre_follow_capture",
            side_effect=RuntimeError("shadow-only"),
        ), mock.patch.object(runner, "log"):
            self.assertIsNone(
                runner._record_follow60_ordering_v2_shadow_from_existing_capture(
                    None,
                    account_id=ACCOUNT,
                    run_id="run-1",
                    request_id="request-1",
                    candidate_username="candidate",
                    source_profile_username="source_ct",
                    visual_candidate_id="xml_list:candidate",
                )
            )


if __name__ == "__main__":
    unittest.main()
