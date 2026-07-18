from __future__ import annotations

import gzip
import hashlib
import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import dm_sender_engine
import instagram_navigation as nav
import welcome_list_sender as sender


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "welcome_jtm_physical_d4c3a70d"
PACKAGE = "com.instagram.androie"
ACCOUNT_ID = "83de9cc9-5c37-42d1-9edc-c924352b17b1"
RUN_ID = "d4c3a70d-460a-483e-995a-8beecb786430"
JOB_ID = "ffc99ef0-c35f-43ab-a6e8-67c60d5b4f2d"
USERNAME = "jtm.signature"


def _bounds(raw: str) -> dict[str, int]:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", raw or "")
    if not match:
        return {}
    left, top, right, bottom = (int(value) for value in match.groups())
    return {"left": left, "top": top, "right": right, "bottom": bottom}


class _PhysicalNode:
    def __init__(self, element: ET.Element) -> None:
        self.element = element

    @property
    def info(self) -> dict[str, object]:
        attrs = self.element.attrib
        return {
            "text": attrs.get("text", ""),
            "contentDescription": attrs.get("content-desc", ""),
            "resourceName": attrs.get("resource-id", ""),
            "className": attrs.get("class", ""),
            "bounds": _bounds(attrs.get("bounds", "")),
            "selected": attrs.get("selected") == "true",
            "scrollable": attrs.get("scrollable") == "true",
        }

    def get_text(self) -> str:
        return str(self.element.attrib.get("text") or self.element.attrib.get("content-desc") or "")


class _PhysicalSelector:
    def __init__(self, device: "_PhysicalReplayDevice", filters: dict[str, object]) -> None:
        self.device = device
        self.filters = filters

    def _matches(self, element: ET.Element) -> bool:
        attrs = element.attrib
        for key, expected in self.filters.items():
            if key == "text" and attrs.get("text", "") != expected:
                return False
            if key == "textContains" and str(expected) not in attrs.get("text", ""):
                return False
            if key == "description" and attrs.get("content-desc", "") != expected:
                return False
            if key == "descriptionContains" and str(expected) not in attrs.get("content-desc", ""):
                return False
            if key == "resourceId" and attrs.get("resource-id", "") != expected:
                return False
            if key == "resourceIdMatches" and not re.match(str(expected), attrs.get("resource-id", "")):
                return False
            if key == "className" and attrs.get("class", "") != expected:
                return False
            if key == "classNameMatches" and not re.match(str(expected), attrs.get("class", "")):
                return False
            if key == "scrollable" and (attrs.get("scrollable") == "true") != bool(expected):
                return False
        return True

    def all(self) -> list[_PhysicalNode]:
        return [
            _PhysicalNode(element)
            for element in self.device.root.iter()
            if self._matches(element)
        ]

    @property
    def count(self) -> int:
        return len(self.all())

    def exists(self, **_kwargs: object) -> bool:
        return bool(self.all())

    @property
    def info(self) -> dict[str, object]:
        nodes = self.all()
        return nodes[0].info if nodes else {}

    def get_text(self) -> str:
        nodes = self.all()
        return nodes[0].get_text() if nodes else ""

    def click(self) -> None:
        nodes = self.all()
        if not nodes:
            raise RuntimeError("physical_selector_absent")
        self.device.advance_from_selector(self.filters)


class _PhysicalReplayDevice:
    def __init__(self, surfaces: dict[str, str]) -> None:
        self.surfaces = surfaces
        self.stage = "followers"
        self.clicks: list[tuple[int, int]] = []
        self.focus_count = 0
        self.draft_count = 0
        self.send_count = 0

    @property
    def xml(self) -> str:
        return self.surfaces[self.stage]

    @property
    def root(self) -> ET.Element:
        return ET.fromstring(self.xml)

    def __call__(self, **filters: object) -> _PhysicalSelector:
        return _PhysicalSelector(self, filters)

    def app_current(self) -> dict[str, str]:
        activity = (
            "com.instagram.modal.ModalActivity"
            if self.stage == "thread"
            else "com.instagram.mainactivity.InstagramMainActivity"
        )
        return {"package": PACKAGE, "activity": activity}

    def dump_hierarchy(self, **_kwargs: object) -> str:
        return self.xml

    def window_size(self) -> tuple[int, int]:
        return 1080, 2340

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))
        if self.stage == "followers":
            self.stage = "profile"

    def advance_from_selector(self, filters: dict[str, object]) -> None:
        if self.stage == "profile" and filters.get("description") == "Message":
            self.stage = "thread"
            return
        raise RuntimeError(f"unsupported_simulated_navigation:{self.stage}:{filters}")


class WelcomeJtmPhysicalReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((FIXTURE_DIR / "provenance.json").read_text())
        cls.surfaces = {
            "followers": (FIXTURE_DIR / "followers-jtm.xml").read_text(),
            "profile": (FIXTURE_DIR / "profile-jtm.xml").read_text(),
            "thread": gzip.decompress(
                (FIXTURE_DIR / "thread-jtm-business-chat.xml.gz").read_bytes()
            ).decode(),
        }

    def test_physical_chain_reproduces_baseline_identity_mismatch(self) -> None:
        device = _PhysicalReplayDevice(self.surfaces)
        machine = sender._WelcomeStateMachine(account_id=ACCOUNT_ID, run_id=RUN_ID)

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            nav, "_XML_DIR", Path(temp_dir)
        ), patch.object(nav.config, "INSTAGRAM_PACKAGE", PACKAGE), patch.object(
            sender.config, "INSTAGRAM_PACKAGE", PACKAGE
        ):
            followers_det, _ = nav.detect_followers_list_screen_fresh(
                device,
                source_profile_username="i_m_your_traker",
                hierarchy_xml=self.surfaces["followers"],
            )
            self.assertTrue(followers_det["is_followers_list"])

            row, scrolls, lookup_path, _ = sender._resolve_followers_row(
                device,
                USERNAME,
                account_username="i_m_your_traker",
                scan_anchors={},
                job_id=JOB_ID,
                scan_generation=RUN_ID,
                navigation_generation=f"{RUN_ID}:physical-replay",
            )
            self.assertIsNotNone(row)
            self.assertEqual(scrolls, 0)
            self.assertEqual(lookup_path, "fresh_visible")
            self.assertEqual(row["username"], USERNAME)
            self.assertEqual(row["row_cta_xml_class"], "follow_back")

            allowed, reason, _, observed = sender._authorize_welcome_row_tap(
                row,
                expected_username=USERNAME,
                job_id=JOB_ID,
                scan_generation=RUN_ID,
                navigation_generation=f"{RUN_ID}:physical-replay",
            )
            self.assertTrue(allowed, reason)
            self.assertEqual(observed, USERNAME)
            machine.transition(
                "followers_stable",
                proof="physical_followers_detector",
                owner="physical_replay",
                job_id=JOB_ID,
                username=USERNAME,
            )
            machine.transition(
                "planned_row_freshly_resolved",
                proof="production_fresh_row_resolver",
                owner="physical_replay",
                job_id=JOB_ID,
                username=USERNAME,
            )

            tapped, _, _ = nav.tap_followers_list_username_row(
                device, row, username=USERNAME
            )
            self.assertTrue(tapped)
            self.assertEqual(device.stage, "profile")

            profile_ok, profile_reason, profile_observed = (
                nav.verify_welcome_profile_username_exact(device, USERNAME, PACKAGE)
            )
            self.assertTrue(profile_ok, profile_reason)
            self.assertEqual(profile_observed, USERNAME)
            machine.transition(
                "target_profile_exact",
                proof=profile_reason,
                owner="physical_replay",
                job_id=JOB_ID,
                username=USERNAME,
            )

            message_cta = device(description="Message")
            self.assertTrue(message_cta.exists())
            message_cta.click()
            self.assertEqual(device.stage, "thread")

            identity_ok, identity_reason, identity_observed = (
                nav.verify_welcome_dm_thread_recipient_exact(device, USERNAME, PACKAGE)
            )
            self.assertFalse(identity_ok)
            self.assertEqual(identity_reason, "thread_recipient_identity_mismatch")
            self.assertEqual(identity_observed, "Business chat")

            evidence, evidence_reason, evidence_observed = (
                dm_sender_engine._fresh_welcome_composer_evidence(
                    device,
                    pkg=PACKAGE,
                    account_id=ACCOUNT_ID,
                    run_id=RUN_ID,
                    job_id=JOB_ID,
                    expected_username=USERNAME,
                    navigation_generation=f"{RUN_ID}:physical-replay:thread",
                )
            )
            self.assertIsNone(evidence)
            self.assertEqual(evidence_reason, "thread_recipient_identity_mismatch")
            self.assertEqual(evidence_observed, "Business chat")
            machine.fail(
                sender._welcome_structured_failure_reason(identity_reason),
                owner="physical_replay",
                job_id=JOB_ID,
                username=USERNAME,
            )

        self.assertEqual(machine.current, "target_profile_exact")
        self.assertEqual(
            [entry["state"] for entry in machine.history],
            ["followers_stable", "planned_row_freshly_resolved", "target_profile_exact"],
        )
        self.assertEqual(device.focus_count, 0)
        self.assertEqual(device.draft_count, 0)
        self.assertEqual(device.send_count, 0)
        print("REPLAY_PHYSICAL_FAILURE_REPRODUCED")

    def test_fixture_hashes_match_immutable_manifest(self) -> None:
        for artifact in self.manifest["artifacts"]:
            fixture_path = FIXTURE_DIR / artifact["file"]
            digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
            self.assertEqual(digest, artifact["sha256"], artifact["file"])
            if "uncompressed_sha256" in artifact:
                uncompressed = gzip.decompress(fixture_path.read_bytes())
                uncompressed_digest = hashlib.sha256(uncompressed).hexdigest()
                self.assertEqual(
                    uncompressed_digest,
                    artifact["uncompressed_sha256"],
                    artifact["file"],
                )

    def test_current_title_subtitle_matrix(self) -> None:
        cases = [
            ("exact/exact", USERNAME, USERNAME, True, "synthetic"),
            ("display/exact", "JTM Signature", USERNAME, True, "synthetic"),
            ("exact/business", USERNAME, "Business chat", False, "physical"),
            ("exact/other", USERNAME, "other.real.user", False, "synthetic"),
            ("exact/no-subtitle", USERNAME, "", True, "synthetic"),
            ("display/generic", "JTM Signature", "Business chat", False, "synthetic"),
            ("empty/empty", "", "", False, "synthetic"),
        ]

        for label, title, subtitle, expected_ok, evidence_type in cases:
            with self.subTest(label=label, evidence_type=evidence_type):
                if evidence_type == "physical":
                    xml = self.surfaces["thread"]
                else:
                    subtitle_node = (
                        f'<node resource-id="{PACKAGE}:id/header_subtitle" '
                        f'content-desc="{subtitle}" />'
                        if subtitle
                        else ""
                    )
                    xml = (
                        f'<hierarchy><node resource-id="{PACKAGE}:id/header_title" '
                        f'content-desc="{title}" />{subtitle_node}</hierarchy>'
                    )
                ok, _, _ = nav._welcome_dm_thread_recipient_identity_from_hierarchy(
                    xml, USERNAME
                )
                self.assertEqual(ok, expected_ok)


if __name__ == "__main__":
    unittest.main()
