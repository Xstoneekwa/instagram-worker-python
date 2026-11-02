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
        package_patch = patch.object(nav.config, "INSTAGRAM_PACKAGE", PACKAGE)
        package_patch.start()
        self.addCleanup(package_patch.stop)
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
        cases["wrong_instagram_instance"] = xml.replace(PACKAGE, "com.instagram.androif")
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


class NativeComposePostsendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / "tests/fixtures/dm_446_normal_postsend_observed.json").read_text())
        cls.expected = cls.data["expected_text"]
        cls.target = cls.data["target"]

    def setUp(self):
        self.clock = Clock()
        self.clock.now = 0.0
        for name, value in [("time", self.clock), ("log", lambda *a, **k: None),
                            ("_dm_read_composer_text_len", lambda d: 0)]:
            p = patch.object(nav, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(nav.config, "INSTAGRAM_PACKAGE", PACKAGE)
        p.start()
        self.addCleanup(p.stop)

    def xml(self, i=30):
        return self.data["states"][str(i)]["xml"]

    def signature(self, xml):
        return nav._dm_thread_message_signature(Device(xml), self.expected, hierarchy_xml=xml)

    def verify(self, post, *, pre=None, target=None):
        return nav._dm_verify_outbound_message_after_send(
            Device(post, clock=self.clock), self.expected,
            pre_signature=self.signature(self.xml(27) if pre is None else pre),
            expected_username=self.target if target is None else target,
        )

    def bubble(self, root):
        return next(n for n in root.iter() if n.get("class") == "android.widget.TextView"
                    and n.get("text") == self.expected)

    def test_native_provenance_and_exact_pre_post_028_through_031(self):
        import hashlib
        self.assertFalse(self.data["physical_certification_of_candidate"])
        self.assertEqual(self.data["states"]["30"]["native_source_sha256"],
                         "80f4ab745ca457403ba35c000df111cffbd52ddc803e08bc829aea5c923be72f")
        before = self.signature(self.xml(27))
        self.assertEqual(before["compose_expected_message_count"], 0)
        self.assertFalse(before["outbound_expected_text_present"])
        for i in range(28, 32):
            with self.subTest(xml=i):
                self.clock.now = 0.0
                xml = self.xml(i)
                self.assertEqual(hashlib.sha256(xml.encode()).hexdigest(), self.data["states"][str(i)]["sha256"])
                post = self.signature(xml)
                self.assertEqual(post["outbound_expected_text_match_mode"], "compose_outgoing_exact")
                self.assertTrue(nav._dm_outbound_signature_advanced(before, post))
                ok, reason, sig = self.verify(xml)
                self.assertTrue(ok)
                self.assertEqual(reason, "outbound_bubble_after_tap")
                self.assertEqual(sig["compose_expected_message_count"], 1)

    def test_preexisting_bubble_and_shape_becoming_qualified_are_not_new(self):
        for pre in [self.xml(30), self.xml(28)]:
            self.clock.now = 0.0
            self.assertFalse(self.verify(self.xml(31), pre=pre)[0])
        root = ET.fromstring(self.xml())
        self.bubble(root).set("clickable", "true")
        before = ET.tostring(root, encoding="unicode")
        self.assertFalse(self.signature(before)["outbound_expected_text_present"])
        self.assertEqual(self.signature(before)["compose_expected_message_count"], 1)
        self.clock.now = 0.0
        self.assertFalse(self.verify(self.xml(), pre=before)[0])

    def test_exact_target_and_unchanged_thread_are_required(self):
        for target in ["other_recipient", ""]:
            with self.subTest(target=target):
                self.clock.now = 0.0
                self.assertFalse(self.verify(self.xml(), target=target)[0])
        for key in ["header_title", "header_subtitle"]:
            pre = ET.fromstring(self.xml(27))
            n = _node(pre, key)
            n.set("text", "other_identity")
            n.set("content-desc", "other_identity")
            self.clock.now = 0.0
            self.assertFalse(self.verify(self.xml(), pre=ET.tostring(pre, encoding="unicode"))[0])

    def test_incoming_mirror_is_not_outgoing(self):
        root = ET.fromstring(self.xml())
        parents = {c: p for p in root.iter() for c in p}
        row = parents[self.bubble(root)]
        for n in row:
            b = nav._dm_parse_bounds_attr_xml(n.get("bounds"))
            n.set("bounds", "[%d,%d][%d,%d]" % (1080-b["right"], b["top"], 1080-b["left"], b["bottom"]))
        xml = ET.tostring(root, encoding="unicode")
        self.assertFalse(nav._dm_outbound_expected_message_match_mode(xml, self.expected))
        self.assertFalse(self.verify(xml)[0])

    def test_exact_complete_text_not_substring_normalized_or_symbol_redacted(self):
        for value in [self.expected[:-1], self.expected+" extra", self.expected.lower(),
                      self.expected.replace("\n", " "), self.expected.replace("!", "🙂"),
                      self.expected+" ", " "+self.expected]:
            with self.subTest(value_length=len(value)):
                root = ET.fromstring(self.xml())
                self.bubble(root).set("text", value)
                self.assertFalse(nav._dm_outbound_expected_message_match_mode(ET.tostring(root, encoding="unicode"), self.expected))
        self.assertFalse(nav._dm_outbound_expected_message_match_mode(self.xml(), self.expected+" "))

    def test_ambiguous_and_unqualified_shapes_fail_closed(self):
        for case in ["duplicate_bubble", "duplicate_list", "duplicate_header", "missing_header",
                     "outside_list", "wrong_package", "hidden", "wrong_parent", "nested_text",
                     "extra_child", "outside_bounds"]:
            with self.subTest(case=case):
                root = ET.fromstring(self.xml())
                parents = {c: p for p in root.iter() for c in p}
                bubble = self.bubble(root)
                row = parents[bubble]
                ml = _node(root, "message_list")
                if case == "duplicate_bubble": row.append(copy.deepcopy(bubble))
                elif case == "duplicate_list": parents[ml].append(copy.deepcopy(ml))
                elif case == "duplicate_header":
                    n = _node(root, "header_subtitle")
                    parents[n].append(copy.deepcopy(n))
                elif case == "missing_header":
                    n = _node(root, "header_subtitle")
                    parents[n].remove(n)
                elif case == "outside_list":
                    row.remove(bubble)
                    root.append(bubble)
                elif case == "wrong_package": bubble.set("package", "com.instagram.androif")
                elif case == "hidden": bubble.set("visible-to-user", "false")
                elif case == "wrong_parent": parents[row].set("class", "android.widget.FrameLayout")
                elif case == "nested_text": ET.SubElement(bubble, "node", {"class":"android.widget.TextView"})
                elif case == "extra_child": row.append(copy.deepcopy(row[1]))
                elif case == "outside_bounds": bubble.set("bounds", "[561,20][1023,100]")
                xml = ET.tostring(root, encoding="unicode")
                self.assertFalse(nav._dm_outbound_expected_message_match_mode(xml, self.expected))
                self.clock.now = 0.0
                self.assertFalse(self.verify(xml)[0])

        for status in ["", "Failed", "Delivered", "other"]:
            with self.subTest(unobserved_status=status):
                root = ET.fromstring(self.xml(28))
                status_node = next(n for n in root.iter() if n.get("content-desc") == "Sending…")
                status_node.set("content-desc", status)
                xml = ET.tostring(root, encoding="unicode")
                self.assertFalse(nav._dm_outbound_expected_message_match_mode(xml, self.expected))
                self.clock.now = 0.0
                self.assertFalse(self.verify(xml)[0])

    def test_existing_unknown_pre_signature_cannot_establish_novelty(self):
        post = self.signature(self.xml())
        for pre in [{}, self.signature("<hierarchy/>"), self.signature("<bad")]:
            self.assertFalse(nav._dm_outbound_signature_advanced(pre, post))

    def test_identity_correlation_is_hashed_not_personal_text(self):
        sig = self.signature(self.xml())
        self.assertRegex(sig["compose_thread_identity"], r"^[a-f0-9]{64}$")
        self.assertNotIn(self.target, json.dumps(sig))
        self.assertNotIn("Fixture User", json.dumps(sig))

if __name__ == "__main__":
    unittest.main()
