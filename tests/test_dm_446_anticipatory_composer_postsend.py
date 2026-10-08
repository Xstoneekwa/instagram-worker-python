"""Bounded offline qualification for screenshot-derived 446 composer states."""

from __future__ import annotations

import copy
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import instagram_navigation as nav
from test_dm_446_readers import Device, Clock


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/dm_446_anticipatory_composer_states.json").read_text()
)
EXPECTED = "salut test"
PACKAGE = "com.instagram.android"
TARGET = "sunetbassonmua"


def _fixture_xml(name: str) -> str:
    return FIXTURE["states"][name]["xml"]


def _node(root: ET.Element, suffix: str) -> ET.Element:
    return next(
        n
        for n in root.iter("node")
        if n.get("resource-id", "").endswith(":id/" + suffix)
    )


class AnticipatoryComposerPostsendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.clock.now = 0.0
        self.time_patch = patch.object(nav, "time", self.clock)
        self.time_patch.start()
        self.log_patch = patch.object(nav, "log", lambda *args, **kwargs: None)
        self.log_patch.start()

    def tearDown(self) -> None:
        self.log_patch.stop()
        self.time_patch.stop()

    def test_fixture_provenance_is_explicitly_synthetic(self) -> None:
        self.assertEqual(FIXTURE["SOURCE"], "SCREENSHOT")
        self.assertEqual(FIXTURE["PHYSICAL_XML"], "NO")
        self.assertTrue(FIXTURE["PHYSICAL_CERTIFICATION_PENDING"])

    def test_state_c_keeps_exact_composer_and_send_authority(self) -> None:
        xml = _fixture_xml("C")
        root = ET.fromstring(xml)
        editor = _node(root, "row_thread_composer_edittext")
        self.assertEqual(editor.get("text"), EXPECTED)
        self.assertEqual(_node(root, "header_subtitle").get("content-desc"), TARGET)
        bounds = dict(
            zip(("left", "top", "right", "bottom"), map(int, editor.get("bounds").strip("[]").replace("][", ",").split(",")))
        )
        visual = nav._dm_visual_send_candidates_from_hierarchy_xml(
            xml, bounds, 1080, 2340
        )
        self.assertTrue(
            any(
                c.get("resourceId", "").endswith("row_thread_composer_send_button_background")
                for c in visual["candidates"]
            )
        )

    def test_state_d_uses_normal_outbound_marker_and_preserves_thread(self) -> None:
        xml = _fixture_xml("D")
        d = Device(xml, clock=self.clock)
        pre = nav._dm_thread_message_signature(d, EXPECTED, hierarchy_xml=_fixture_xml("C"))
        post = nav._dm_thread_message_signature(d, EXPECTED, hierarchy_xml=xml)
        self.assertEqual(post["outbound_expected_text_match_mode"], "exact_or_normalized")
        self.assertTrue(post["outbound_expected_text_present"])
        self.assertTrue(nav._dm_outbound_signature_advanced(pre, post))

    def test_state_e_requires_the_complete_invite_surface(self) -> None:
        xml = (ROOT / "tests/fixtures/dm_446_invite_postsend_446.xml").read_text()
        d = Device(xml)
        sig = nav._dm_thread_message_signature(d, EXPECTED, hierarchy_xml=xml)
        self.assertEqual(sig["outbound_expected_text_match_mode"], "invite_postsend_exact")
        self.assertTrue(sig["outbound_expected_text_present"])
        self.assertEqual(sig["composer_text_len"], 0)

    def test_negative_matrix_has_no_invite_false_positives(self) -> None:
        xml = (ROOT / "tests/fixtures/dm_446_invite_postsend_446.xml").read_text()
        cases = {}
        for name, mutate in (
            ("missing_title", lambda r: _node(r, "thread_disabled_title").set("text", "")),
            (
                "missing_explanation",
                lambda r: _node(r, "thread_disabled_bottom_description").set("text", ""),
            ),
            ("wrong_text", lambda r: next(n for n in r.iter("node") if n.get("text") == EXPECTED).set("text", "other")),
        ):
            root = ET.fromstring(xml)
            mutate(root)
            cases[name] = ET.tostring(root, encoding="unicode")
        generic = ET.fromstring(xml)
        msg_list = _node(generic, "message_list")
        for n in list(msg_list):
            msg_list.remove(n)
        ET.SubElement(
            generic,
            "node",
            {
                "resource-id": "",
                "class": "android.widget.TextView",
                "package": PACKAGE,
                "text": EXPECTED,
                "bounds": "[100,1200][800,1300]",
            },
        )
        cases["generic_text_outside_message_list"] = ET.tostring(generic, encoding="unicode")
        for name, candidate in cases.items():
            with self.subTest(name=name):
                sig = nav._dm_thread_message_signature(
                    Device(candidate), EXPECTED, hierarchy_xml=candidate
                )
                self.assertFalse(sig["outbound_expected_text_present"])

    def test_372_contract_fixture_is_not_reinterpreted(self) -> None:
        # The anticipatory branch is invite-specific and cannot affect the
        # existing A0/372 message-marker path.
        xml = _fixture_xml("D")
        self.assertIn("direct_text_message_text_view", xml)
        self.assertEqual(nav._dm_outbound_expected_message_match_mode(xml, EXPECTED), "exact_or_normalized")


if __name__ == "__main__":
    unittest.main()
