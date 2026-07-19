from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

import instagram_navigation as nav


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "post_mute_ff99db6"


def _visual_blank(file_name: str) -> bool:
    with Image.open(FIXTURE_DIR / file_name) as image:
        rgb = image.convert("RGB")
        detected, _confidence = nav._visual_profile_lower_grid_mostly_blank(
            rgb, *rgb.size
        )
    return bool(detected)


def _complete_hybrid_evidence(*, empty_marker_vision: bool) -> dict[str, object]:
    return {
        "profile_identity_confirmed": True,
        "instagram_package_confirmed": True,
        "navigation_profile_confirmed": True,
        "post_mute_profile_stable": True,
        "grid_tab_confirmed": True,
        "tappable_post_count": 0,
        "empty_marker_xml": True,
        "empty_marker_vision": empty_marker_vision,
        "fingerprint_stable": True,
        "context_age_ms": 250.0,
        "private_ambiguous": False,
        "reels_or_tagged_selected": False,
        "grid_loading": False,
        "stale_coordinates_reused": False,
        "ambiguous_surface": False,
    }


def test_four_physical_no_post_profiles_supply_the_independent_visual_signal() -> None:
    manifest = json.loads((FIXTURE_DIR / "provenance.json").read_text())
    files = [
        item["file"]
        for item in manifest["artifacts"]
        if item["classification"] == "physical_no_posts_after_reveal"
    ]

    assert len(files) == 4
    for file_name in files:
        assert _visual_blank(file_name) is True
        decision = nav._evaluate_post_follow_fast_no_posts_evidence(
            _complete_hybrid_evidence(empty_marker_vision=True)
        )
        assert decision["decision"] == "skip_no_posts"


def test_physical_public_posts_never_fast_skip_from_visual_signal_alone() -> None:
    assert _visual_blank("azdin-wear-public-posts.png") is False

    evidence = _complete_hybrid_evidence(empty_marker_vision=False)
    evidence["empty_marker_xml"] = False
    evidence["tappable_post_count"] = 3
    decision = nav._evaluate_post_follow_fast_no_posts_evidence(evidence)

    assert decision["decision"] == "fallback"
    assert decision["failure_reason"] == "no_tappable_post"


def test_physical_ravinder_blank_lower_region_cannot_override_fresh_post_tiles() -> None:
    # This physical posts viewport is intentionally a visual false positive for
    # the lower-grid blank heuristic. Hybrid evidence must still reject it.
    assert _visual_blank("ravinderbathua-second-fallback.png") is True

    evidence = _complete_hybrid_evidence(empty_marker_vision=True)
    evidence["empty_marker_xml"] = False
    evidence["tappable_post_count"] = 4
    decision = nav._evaluate_post_follow_fast_no_posts_evidence(evidence)

    assert decision["decision"] == "fallback"
    assert decision["failure_reason"] == "no_tappable_post"


def test_physical_fallback_images_remain_available_for_viewer_replay() -> None:
    for file_name in (
        "mbambu-gedeon-first-fallback.png",
        "mbambu-gedeon-second-fallback.png",
        "ravinderbathua-first-fallback.png",
        "ravinderbathua-second-fallback.png",
    ):
        assert (FIXTURE_DIR / file_name).is_file()
