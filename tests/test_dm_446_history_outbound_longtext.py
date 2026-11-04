"""Observed 446 message family; portable fixtures and optional private originals."""
from __future__ import annotations
import ast
import copy
import hashlib
import json
import os
import subprocess
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import dm_thread_truth as truth
import instagram_navigation as nav
from test_dm_446_readers import Clock, Device, Selector

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "tests/fixtures/dm_446_history_outbound_longtext.json").read_text())
P = "com.instagram.android"
TARGET = DATA["target"]
EXPECTED = DATA["expected_text"]


def node(root, role):
    return next(n for n in root.iter() if n.get("resource-id") == P + ":id/" + role)


def xml(number):
    return DATA["cases"][str(number)]["xml"]


def edit(value, change):
    root = ET.fromstring(value)
    change(root)
    return ET.tostring(root, encoding="unicode")


def bubble(root):
    ml = node(root, "message_list")
    return next(n for n in ml.iter() if n.get("class") == "android.widget.TextView"
                and len(n.get("text", "")) == 810)


class ProofHarness:
    def setUp(self):
        self.clock = Clock()
        self.clock.now = 0.0
        self.logs = []
        for name, value in [("time", self.clock), ("log", lambda *a, **k: self.logs.append((a, k)))]:
            p = patch.object(nav, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(nav.config, "INSTAGRAM_PACKAGE", P)
        p.start()
        self.addCleanup(p.stop)

    def signature(self, value, expected=EXPECTED):
        return nav._dm_thread_message_signature(Device(value), expected, hierarchy_xml=value)

    def verify(self, value, *, pre=None, expected=EXPECTED, target=TARGET):
        self.clock.now = 0.0
        return nav._dm_verify_outbound_message_after_send(
            Device(value, clock=self.clock), expected,
            pre_signature=self.signature(xml(50) if pre is None else pre, expected),
            expected_username=target)


class LongMessageFamilyTests(ProofHarness, unittest.TestCase):
    def test_fixture_provenance_and_full_unicode_lengths(self):
        self.assertEqual((len(EXPECTED), len(EXPECTED.encode("utf-16-le")) // 2, EXPECTED.count("\n")), (803, 810, 10))
        self.assertFalse(DATA["physical_certification_of_candidate"])
        for case in DATA["cases"].values():
            self.assertEqual(hashlib.sha256(case["xml"].encode()).hexdigest(), case["sha256"])
            self.assertTrue(case["anonymized"])
            self.assertRegex(case["native_source_sha256"], r"^[a-f0-9]{64}$")

    def test_each_prior_thread_is_positive_without_footer_or_localized_timestamp(self):
        for number in (41, 42, 47, 48):
            for label in ("YESTERDAY 14:12", "Hier", "13:00", "任意の表示", ""):
                value = edit(xml(number), lambda r: [
                    n.set("text", label) for n in node(r, "message_list")
                    if n.get("class") == "android.widget.TextView"])
                with self.subTest(xml=number, cosmetic_date=label):
                    state, proof = truth.classify_dm_history(value, P, TARGET)
                    self.assertEqual(state, "existing_thread")
                    self.assertEqual(proof["history_signal_type"], "compose_message_bubble")

    def test_descriptive_intro_is_independent_of_positive_message(self):
        def cosmetic(r):
            card = node(r, "message_list")[0][0]
            for n in card.iter():
                if n.get("class") == "android.widget.TextView":
                    n.set("text", "Présentation cosmétique " + n.get("text", ""))
            card[:] = [card[0], *reversed(list(card)[1:])]
        value = edit(xml(41), cosmetic)
        self.assertEqual(truth.classify_dm_history(value, P, TARGET)[0], "existing_thread")

    def test_history_requires_exact_identity_and_a_real_message_row(self):
        self.assertEqual(truth.classify_dm_history(xml(41), P, "another_target")[0], "unknown")
        def no_message(r):
            ml = node(r, "message_list")
            for n in list(ml)[1:]:
                ml.remove(n)
        self.assertEqual(truth.classify_dm_history(edit(xml(41), no_message), P, TARGET)[0], "unknown")
        def arbitrary(r):
            row = node(r, "message_list")[-1][0]
            row.remove(row[1])
        self.assertEqual(truth.classify_dm_history(edit(xml(41), arbitrary), P, TARGET)[0], "unknown")

    def test_functional_row_does_not_depend_on_cosmetic_long_clickable_state(self):
        def variant(r):
            row = node(r, "message_list")[-1]
            row.set("long-clickable", "false")
        self.assertEqual(truth.classify_dm_history(edit(xml(41), variant), P, TARGET)[0], "existing_thread")

    def test_native_normal_and_disabled_long_outgoing_forms(self):
        for number, pre in ((52, 50), (53, 50), (54, 50), (55, 50), (59, 57), (60, 57), (61, 57)):
            with self.subTest(xml=number):
                sig = self.signature(xml(number))
                self.assertEqual(sig["outbound_expected_text_match_mode"], "compose_outgoing_export_equivalent")
                self.assertEqual(sig["compose_expected_message_count"], 1)
                self.assertEqual(self.signature(xml(pre))["compose_expected_message_count"], 0)
                self.assertTrue(self.verify(xml(number), pre=xml(pre))[0])

    def test_full_text_not_prefix_case_whitespace_or_punctuation_approximation(self):
        original = bubble(ET.fromstring(xml(61))).get("text")
        variants = [original[:-1], original + "x", original.lower(), original.replace("\n", " "),
                    " " + original, original + " ", original.replace("!", "?"),
                    original.replace("..", ".", 1), original.replace("x", "y", 1)]
        for value in variants:
            candidate = edit(xml(61), lambda r: bubble(r).set("text", value))
            with self.subTest(length=len(value)):
                self.assertFalse(nav._dm_outbound_expected_message_match_mode(candidate, EXPECTED))
                self.assertFalse(self.verify(candidate)[0])

    def test_export_equivalence_is_not_a_symbol_or_punctuation_wildcard(self):
        match = nav._dm_full_exported_text_match_mode
        self.assertEqual(match("Exact literal content ..!\n", "Exact literal content 😀!\n"), "export_equivalent")
        self.assertEqual(match("Exact literal content ..!\n", "Exact literal content 𐐀!\n"), "export_equivalent")
        for actual, expected in [("....", "😀😀"), ("Exact literal content .!", "Exact literal content 😀!"),
                                 (".." * 12, "𐐀" * 12),
                                 ("Exact literal content ..?", "Exact literal content 😀!"),
                                 ("Exact literal content 🙂!", "Exact literal content 😀!")]:
            self.assertEqual(match(actual, expected), "")

    def test_incoming_geometry_cannot_certify_outgoing(self):
        def mirror(r):
            row = node(r, "message_list")[-1][0]
            for n in row:
                x1, y1, x2, y2 = truth._rect(n.get("bounds"))
                n.set("bounds", "[%d,%d][%d,%d]" % (1080-x2, y1, 1080-x1, y2))
        candidate = edit(xml(61), mirror)
        self.assertFalse(nav._dm_outbound_expected_message_match_mode(candidate, EXPECTED))
        self.assertFalse(self.verify(candidate, pre=xml(57))[0])
        self.assertEqual(truth.classify_dm_history(candidate, P, TARGET)[0], "existing_thread")

    def test_preexisting_exported_text_never_becomes_a_new_send_when_shape_changes(self):
        self.assertFalse(self.verify(xml(61), pre=xml(61))[0])
        unqualified = edit(xml(61), lambda r: bubble(r).set("clickable", "true"))
        before = self.signature(unqualified)
        self.assertEqual(before["compose_expected_message_count"], 1)
        self.assertFalse(before["outbound_expected_text_present"])
        self.assertFalse(self.verify(xml(61), pre=unqualified)[0])

    def test_wrong_or_missing_target_and_changed_thread_cannot_certify(self):
        for target in ("", "another_recipient"):
            self.assertFalse(self.verify(xml(61), pre=xml(57), target=target)[0])
        for role in ("header_title", "header_subtitle"):
            candidate = edit(xml(57), lambda r: (node(r, role).set("text", "other"),
                                                  node(r, role).set("content-desc", "other")))
            self.assertFalse(self.verify(xml(61), pre=candidate)[0])

    def test_geometry_clipping_requires_exact_visible_intersection_and_real_visibility(self):
        cases = [
            lambda r: bubble(r).set("bounds", "[334,0][1033,100]"),
            lambda r: node(r, "message_list")[-1].set("bounds", "[0,259][1080,2018]"),
            lambda r: bubble(r).set("visible-to-user", "false"),
            lambda r: bubble(r).set("package", "another.package"),
            lambda r: node(r, "message_list")[-1][0].append(ET.Element("node", dict(bubble(r).attrib))),
        ]
        for change in cases:
            candidate = edit(xml(61), change)
            self.assertFalse(nav._dm_outbound_expected_message_match_mode(candidate, EXPECTED))
            self.assertFalse(self.verify(candidate)[0])

    def test_overlay_or_unqualified_thread_does_not_get_history_or_send_authority(self):
        def overlay(r):
            host = node(r, "message_actions_fragment")
            ET.SubElement(host, "node", {"class":"android.widget.TextView", "package":P,
                "resource-id":"", "text":"overlay", "content-desc":"", "clickable":"false",
                "focusable":"false", "enabled":"true", "visible-to-user":"true",
                "bounds":"[0,258][1080,2000]"})
        candidate = edit(xml(61), overlay)
        self.assertFalse(nav._dm_outbound_expected_message_match_mode(candidate, EXPECTED))
        self.assertEqual(truth.classify_dm_history(candidate, P, TARGET)[0], "unknown")
        for role in ("header_subtitle", "message_list"):
            def duplicate(r, role=role):
                n=node(r, role)
                parents={c:p for p in r.iter() for c in p}
                parents[n].append(copy.deepcopy(n))
            self.assertFalse(nav._dm_outbound_expected_message_match_mode(edit(xml(61), duplicate), EXPECTED))

    def test_disabled_labels_alone_or_missing_functional_footer_are_insufficient(self):
        def remove(r):
            n=node(r, "thread_disabled_bottom_description")
            next(p for p in r.iter() if n in list(p)).remove(n)
        self.assertFalse(nav._dm_outbound_expected_message_match_mode(edit(xml(55), remove), EXPECTED))
        self.assertFalse(nav._dm_outbound_expected_message_match_mode(xml(55), EXPECTED[:30]))

    def test_diagnostic_signature_uses_xml_without_live_getter_or_new_device_reads(self):
        with patch.object(nav, "_dm_read_composer_text_len", side_effect=AssertionError("redundant remote read")) as getter:
            for number in (50, 55, 57, 61):
                d=Device(xml(number))
                sig=nav._dm_thread_message_signature(d, EXPECTED, hierarchy_xml=xml(number))
                self.assertEqual((d.reads, d.currents, d.dimensions), (0, 0, 0))
                self.assertIsNotNone(sig["composer_text_len"])
            self.assertTrue(self.verify(xml(61), pre=xml(57))[0])
            getter.assert_not_called()
        self.assertIsNone(self.signature("<hierarchy/>")["composer_text_len"])
        self.verify("<hierarchy/>")
        probes=[kw for args,kw in self.logs if len(args)>1 and args[1]=="welcome_outbound_probe"]
        self.assertIsNone(probes[-1]["composer_empty"])

    def test_exact_parent_signature_calls_one_diagnostic_getter_and_candidate_calls_zero(self):
        src=subprocess.check_output(["git","-C",str(ROOT),"show","6a89ac200155acf0bc11fe0aa8b30c2b569076f8:instagram_navigation.py"],text=True)
        tree=ast.parse(src)
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_dm_thread_message_signature")
        namespace=nav.__dict__.copy()
        calls=[]
        namespace["_dm_read_composer_text_len"]=lambda d:(calls.append("get_text") or 0)
        exec(compile("from __future__ import annotations\n"+ast.get_source_segment(src,function),
                     "<exact-parent-signature>", "exec"),namespace)
        namespace["_dm_thread_message_signature"](Device(xml(61)), EXPECTED, hierarchy_xml=xml(61))
        self.assertEqual(len(calls),1)
        with patch.object(nav,"_dm_read_composer_text_len",side_effect=AssertionError("unexpected live diagnostic")):
            self.signature(xml(61))

    def test_exact_draft_reader_is_still_mandatory_with_full_raw_text(self):
        class LiveSelector(Selector):
            def get_text(s):
                matches=s.all()
                if not matches:
                    return ""
                s.d.getter_calls+=1
                return s.d.raw_draft
        class LiveDevice(Device):
            getter_calls=0
            raw_draft=EXPECTED
            def __call__(s,**kw):
                return LiveSelector(s,kw)
        for actual,expected in [(EXPECTED,True),(EXPECTED[:-1],False),(EXPECTED+"x",False)]:
            d=LiveDevice(xml(57),clock=self.clock)
            d.raw_draft=actual
            self.clock.now=0.0
            self.assertEqual(nav.verify_dm_draft_text(d,EXPECTED),expected)
            self.assertGreater(d.getter_calls,0)


@unittest.skipUnless(os.environ.get("DM446_NATIVE_EVIDENCE_MANIFEST"), "private native corpus supplied only for scoped certification")
class PrivateNativeEvidenceTests(ProofHarness, unittest.TestCase):
    """Use original native bytes independently of portable transformations."""
    def test_private_originals_hash_identity_history_and_outgoing(self):
        path=Path(os.environ["DM446_NATIVE_EVIDENCE_MANIFEST"])
        data=json.loads(path.read_text())
        for number, item in data["cases"].items():
            raw=Path(item["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),item["sha256"])
            text=raw.decode()
            if int(number) in (41,42,47,48):
                self.assertEqual(truth.classify_dm_history(text,P,item["target"])[0],"existing_thread")
            if int(number) in (52,53,54,55,59,60,61):
                pre=data["cases"]["50" if int(number)<56 else "57"]
                before=Path(pre["path"]).read_text()
                self.assertTrue(self.verify(text,pre=before,expected=data["expected_text"],target=item["target"])[0])


if __name__ == "__main__":
    unittest.main()

