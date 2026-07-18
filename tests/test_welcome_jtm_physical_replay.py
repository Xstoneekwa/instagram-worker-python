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
import account_session_orchestrator
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

    def wait(self, **_kwargs: object) -> bool:
        return self.exists()

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
        elif self.stage == "profile":
            self.stage = "thread"

    def advance_from_selector(self, filters: dict[str, object]) -> None:
        if self.stage == "profile" and any(
            "Message" in str(filters.get(key) or "")
            for key in ("text", "textContains", "description", "descriptionContains")
        ):
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

    def test_physical_fixture_documents_baseline_identity_mismatch(self) -> None:
        title = nav._welcome_dm_thread_header_from_hierarchy(self.surfaces["thread"])
        subtitle = nav._welcome_dm_thread_subtitle_from_hierarchy(
            self.surfaces["thread"]
        )

        baseline_ok = bool(
            nav._normalize_handle(subtitle)
            == nav._normalize_handle(USERNAME)
        )

        self.assertEqual(title, USERNAME)
        self.assertEqual(subtitle, "Business chat")
        self.assertFalse(baseline_ok)

    def test_physical_chain_accepts_proven_generic_subtitle_after_patch(self) -> None:
        device = _PhysicalReplayDevice(self.surfaces)
        machine = sender._WelcomeStateMachine(account_id=ACCOUNT_ID, run_id=RUN_ID)
        message_body = "jtm.signature physical replay message"
        outbound_xml = self.surfaces["thread"].replace(
            "</hierarchy>",
            '<node resource-id="com.instagram.androie:id/direct_text_message_text_view" '
            f'text="{message_body}" /></hierarchy>',
        )
        device.surfaces["outbound"] = outbound_xml

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            nav, "_XML_DIR", Path(temp_dir)
        ), patch.object(nav.config, "INSTAGRAM_PACKAGE", PACKAGE), patch.object(
            sender.config, "INSTAGRAM_PACKAGE", PACKAGE
        ):
            thread_state, navigation_ok, navigation_meta = (
                sender._navigate_followers_row_to_dm(
                    device,
                    USERNAME,
                    pkg=PACKAGE,
                    account_username="i_m_your_traker",
                    scan_anchors={},
                    planned_job_context={
                        "job_id": JOB_ID,
                        "run_id": RUN_ID,
                        "scan_generation": RUN_ID,
                        "navigation_generation": f"{RUN_ID}:physical-replay",
                    },
                    state_machine=machine,
                )
            )
            self.assertTrue(navigation_ok)
            self.assertNotIn(
                thread_state,
                {
                    "unknown",
                    "foreground_package_mismatch",
                    "profile_username_mismatch",
                    "thread_recipient_identity_mismatch",
                },
            )
            self.assertEqual(navigation_meta["lookup_path_used"], "fresh_visible")
            self.assertEqual(device.stage, "thread")

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
            self.assertIsNotNone(evidence)
            self.assertEqual(evidence_reason, "ok")
            self.assertEqual(evidence_observed, USERNAME)
            machine.transition(
                "composer_exact",
                proof="production_fresh_composer_evidence",
                owner="physical_replay",
                job_id=JOB_ID,
                username=USERNAME,
            )

            machine.transition(
                "draft_exact",
                proof="simulated_exact_draft_readback",
                owner="physical_replay_simulation",
                job_id=JOB_ID,
                username=USERNAME,
            )
            pre_signature = nav._dm_thread_message_signature(
                device,
                message_body,
                hierarchy_xml=self.surfaces["thread"],
            )
            machine.transition(
                "send_tapped",
                proof="simulated_exact_send_selector_tap",
                owner="physical_replay_simulation",
                job_id=JOB_ID,
                username=USERNAME,
            )
            device.stage = "outbound"
            with patch.object(
                nav.config, "DM_OUTBOUND_SEND_VERIFY_MAX_S", 0.05, create=True
            ), patch.object(
                nav.config, "DM_OUTBOUND_SEND_VERIFY_POLL_S", 0, create=True
            ):
                outbound_ok, outbound_reason, _ = (
                    nav._dm_verify_outbound_message_after_send(
                        device,
                        message_body,
                        pre_signature=pre_signature,
                        expected_username=USERNAME,
                    )
                )
            self.assertTrue(outbound_ok)
            self.assertEqual(outbound_reason, "outbound_bubble_after_tap")
            machine.transition(
                "outbound_verified",
                proof=outbound_reason,
                owner="physical_replay_production_probe",
                job_id=JOB_ID,
                username=USERNAME,
            )

            device.stage = "followers"
            restored = sender._restore_followers_after_job(
                device,
                USERNAME,
                pkg=PACKAGE,
                account_username="i_m_your_traker",
                job_id=JOB_ID,
                state_machine=machine,
            )
            self.assertTrue(restored)
            machine.transition(
                "next_job_ready",
                proof="structured_plan_exhausted",
                owner="physical_replay_sender_loop",
                job_id=JOB_ID,
                username=USERNAME,
            )
            run_follow, handoff_reason = (
                account_session_orchestrator._should_run_follow_after_welcome(
                    welcome_enabled=True,
                    real_send_enabled=True,
                    welcome_phase_executed=True,
                    welcome_exit_code=0,
                    scan_summary={"status": "success"},
                    sender_summary={
                        "sender_status": "success",
                        "jobs_failed_count": 0,
                    },
                    welcome_session_status="success",
                )
            )
            self.assertTrue(run_follow)
            self.assertEqual(handoff_reason, "welcome_closed_success")

        self.assertEqual(machine.current, "next_job_ready")
        self.assertEqual(
            [entry["state"] for entry in machine.history],
            [
                "followers_stable",
                "planned_row_freshly_resolved",
                "target_profile_exact",
                "dm_thread_exact",
                "composer_exact",
                "draft_exact",
                "send_tapped",
                "outbound_verified",
                "thread_exit",
                "followers_restored",
                "next_job_ready",
            ],
        )
        self.assertEqual(device.focus_count, 0)
        self.assertEqual(device.draft_count, 0)
        self.assertEqual(device.send_count, 0)
        print("REPLAY_PHYSICAL_FAILURE_FIXED")

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

    def test_title_subtitle_matrix(self) -> None:
        cases = [
            ("exact/exact", USERNAME, USERNAME, "accept", "subtitle_exact", "exact_username"),
            ("display/exact", "JTM Signature", USERNAME, "accept", "subtitle_exact", "exact_username"),
            ("exact/business", USERNAME, "Business chat", "accept", "title_exact_with_generic_subtitle", "generic_ui_label"),
            ("exact/other", USERNAME, "other.real.user", "block", "none", "contradictory_username"),
            ("exact/no-subtitle", USERNAME, "", "accept", "title_exact_legacy_no_subtitle", "absent"),
            ("display/generic", "JTM Signature", "Business chat", "block", "none", "generic_ui_label"),
            ("exact/unknown", USERNAME, "Creator account", "block", "none", "unknown"),
            ("generic/exact", "Business chat", USERNAME, "accept", "subtitle_exact", "exact_username"),
            ("empty/empty", "", "", "block", "none", "absent"),
            ("normalized", "unused", "  @JTM.Signature  ", "accept", "subtitle_exact", "exact_username"),
        ]

        for label, title, subtitle, decision, source, subtitle_kind in cases:
            with self.subTest(label=label):
                evaluation = nav._evaluate_welcome_thread_identity(
                    expected_username=USERNAME,
                    header_title=title,
                    header_subtitle=subtitle,
                )
                self.assertEqual(evaluation["decision"], decision)
                self.assertEqual(evaluation["identity_source"], source)
                self.assertEqual(evaluation["subtitle_kind"], subtitle_kind)


if __name__ == "__main__":
    unittest.main()
