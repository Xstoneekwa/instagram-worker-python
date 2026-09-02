import unittest

import instagram_navigation as nav


class _MissingSelector:
    def exists(self, timeout=0):
        return False


class _FakeDevice:
    def __init__(self, xml):
        self.xml = xml

    def __call__(self, **kwargs):
        return _MissingSelector()

    def dump_hierarchy(self, compressed=False):
        return self.xml


def _xml(value, *, field="text", resource_id="post_header", prefix=""):
    text = value if field == "text" else ""
    desc = value if field == "content-desc" else ""
    return (
        '<hierarchy><node text="Posts" content-desc="" resource-id="title" />'
        f'{prefix}'
        f'<node text="{text}" content-desc="{desc}" resource-id="{resource_id}" />'
        '</hierarchy>'
    )


class CollaborativePostIdentityTests(unittest.TestCase):
    def test_exact_owner_remains_valid(self):
        out = nav._post_open_hierarchy_identity_signals(
            _xml("lesslyrics_"), expected_username="lesslyrics_"
        )
        self.assertTrue(out["candidate_username_exact_in_snapshot"])
        self.assertEqual(out["candidate_username_method"], "snapshot_exact_text")

    def test_collaborative_owner_token_is_valid(self):
        out = nav._post_open_hierarchy_identity_signals(
            _xml("lesslyrics_ and ubunmuntu.dancefilms"),
            expected_username="lesslyrics_",
        )
        self.assertTrue(out["candidate_username_exact_in_snapshot"])
        self.assertEqual(
            out["candidate_username_method"],
            "snapshot_collaborative_text_owner_token",
        )

    def test_second_collaborator_is_exact_token(self):
        out = nav._post_open_hierarchy_identity_signals(
            _xml("lesslyrics_ et ubunmuntu.dancefilms", field="content-desc"),
            expected_username="ubunmuntu.dancefilms",
        )
        self.assertTrue(out["candidate_username_exact_in_snapshot"])
        self.assertEqual(
            out["candidate_username_method"],
            "snapshot_collaborative_content_desc_owner_token",
        )

    def test_three_collaborators_are_supported(self):
        handles = nav._collaborative_post_owner_handles(
            "first.owner and second_owner & third.owner"
        )
        self.assertEqual(handles, ("first.owner", "second_owner", "third.owner"))

    def test_field_examples_match_both_real_candidates(self):
        less = nav._post_open_hierarchy_identity_signals(
            _xml("lesslyrics_ and ubummuntu.dancefilms"),
            expected_username="lesslyrics_",
        )
        gilles = nav._post_open_hierarchy_identity_signals(
            _xml("thecatcher.film and gillesmosar"),
            expected_username="gillesmosar",
        )
        self.assertTrue(less["candidate_username_exact_in_snapshot"])
        self.assertTrue(gilles["candidate_username_exact_in_snapshot"])

    def test_normal_single_author_georgiosrietveld_unchanged(self):
        out = nav._post_open_hierarchy_identity_signals(
            _xml("georgiosrietveld"), expected_username="georgiosrietveld"
        )
        self.assertTrue(out["candidate_username_exact_in_snapshot"])
        self.assertEqual(out["candidate_username_method"], "snapshot_exact_text")

    def test_substring_collision_is_rejected(self):
        out = nav._post_open_hierarchy_identity_signals(
            _xml("mylesslyrics_ and ubunmuntu.dancefilms"),
            expected_username="lesslyrics_",
        )
        self.assertFalse(out["candidate_username_exact_in_snapshot"])

    def test_caption_mention_is_not_an_owner_label(self):
        out = nav._post_open_hierarchy_identity_signals(
            _xml("new work with lesslyrics_ and friends today"),
            expected_username="lesslyrics_",
        )
        self.assertFalse(out["candidate_username_exact_in_snapshot"])

    def test_exact_caption_value_is_not_owner_authority(self):
        out = nav._post_open_hierarchy_identity_signals(
            _xml("lesslyrics_", resource_id="post_caption"),
            expected_username="lesslyrics_",
        )
        self.assertFalse(out["candidate_username_exact_in_snapshot"])

    def test_reel_surface_rejects_owner_token(self):
        out = nav._post_open_hierarchy_identity_signals(
            _xml(
                "lesslyrics_ and ubummuntu.dancefilms",
                prefix='<node text="" content-desc="" resource-id="reel_viewer" />',
            ),
            expected_username="lesslyrics_",
        )
        self.assertTrue(out["story_or_highlight_detected"])
        self.assertFalse(out["candidate_username_exact_in_snapshot"])

    def test_story_highlight_surface_rejects_owner_token(self):
        out = nav._post_open_hierarchy_identity_signals(
            _xml(
                "lesslyrics_ and ubummuntu.dancefilms",
                prefix='<node text="Reply" content-desc="" resource-id="story_controls" />',
            ),
            expected_username="lesslyrics_",
        )
        self.assertTrue(out["story_or_highlight_detected"])
        self.assertFalse(out["candidate_username_exact_in_snapshot"])

    def test_ui_reader_uses_hierarchy_token_not_contains_selector(self):
        username, method = nav._visual_post_viewer_header_username_from_ui(
            _FakeDevice(_xml("lesslyrics_ and ubunmuntu.dancefilms")),
            expected_username="lesslyrics_",
        )
        self.assertEqual(username, "lesslyrics_")
        self.assertEqual(method, "snapshot_collaborative_text_owner_token")

    def test_fresh_hierarchy_reprobe_uses_same_owner_contract(self):
        xml = _xml("thecatcher.film and gillesmosar")
        parsed = nav._post_open_hierarchy_identity_signals(
            xml, expected_username="gillesmosar"
        )
        username, method = nav._visual_post_viewer_header_username_from_ui(
            _FakeDevice(xml), expected_username="gillesmosar"
        )
        self.assertTrue(parsed["candidate_username_exact_in_snapshot"])
        self.assertEqual(username, "gillesmosar")
        self.assertEqual(method, parsed["candidate_username_method"])


if __name__ == "__main__":
    unittest.main()
