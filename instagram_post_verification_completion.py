"""Channel-neutral completion gate between verification and identity proof.

Instagram may accept a verification code and then expose one or more onboarding
surfaces before its bottom navigation is usable.  This module normalizes those
known surfaces with bounded Back recovery.  It never treats code acceptance as
proof that the expected account is connected; the account identity guard remains
the authoritative next step.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from instagram_login_ui_probe import extract_login_screen_signals_from_hierarchy
from logs import log


POST_VERIFICATION_MAX_RECOVERIES = 3
POST_VERIFICATION_RECOVERY_WAIT_SECONDS = 0.65

_BACK_RECOVERABLE_SCREEN_TYPES = frozenset(
    {
        "connected_post_login_location_services_prompt",
        "instagram_turn_on_notifications_prompt",
        "android_instagram_notification_settings",
        "save_login_info_prompt",
        "google_password_manager_save_prompt",
        "samsung_pass_save_password_prompt",
        "post_login_sync_contacts_prompt",
        "post_login_discover_people_prompt",
    }
)


@dataclass(frozen=True)
class PostVerificationCompletionResult:
    safe_for_identity_guard: bool
    screen_type: str
    recovery_count: int = 0
    recovered_screen_types: tuple[str, ...] = ()
    failure_reason: str = ""
    fingerprint_changed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def prepare_post_verification_identity_surface(
    d: Any,
    *,
    expected_package_name: str = "",
    max_recoveries: int = POST_VERIFICATION_MAX_RECOVERIES,
    sleeper: Callable[[float], None] = time.sleep,
) -> PostVerificationCompletionResult:
    """Reach a stable Instagram home/profile surface or fail closed.

    Recovery is bounded by both a total attempt cap and a per-fingerprint cap.
    Every Back is followed by a fresh hierarchy and must change the fingerprint;
    the same screen can therefore never receive a second blind Back.
    """

    seen_fingerprints: set[str] = set()
    recovered: list[str] = []
    last_changed = False

    for _ in range(max(0, int(max_recoveries)) + 1):
        hierarchy = _dump_hierarchy(d)
        screen_type, signals = classify_post_verification_surface(
            hierarchy,
            expected_package_name=expected_package_name,
        )
        fingerprint = _fingerprint(hierarchy)

        if screen_type in {"active_account_home", "active_account_profile"}:
            return PostVerificationCompletionResult(
                safe_for_identity_guard=True,
                screen_type=screen_type,
                recovery_count=len(recovered),
                recovered_screen_types=tuple(recovered),
                fingerprint_changed=last_changed,
                metadata={"connected_surface_proof": signals.get("connected_surface_proof", "")},
            )

        if screen_type not in _BACK_RECOVERABLE_SCREEN_TYPES:
            return PostVerificationCompletionResult(
                safe_for_identity_guard=False,
                screen_type=screen_type or "unknown",
                recovery_count=len(recovered),
                recovered_screen_types=tuple(recovered),
                failure_reason="post_verification_human_assistance_required",
                fingerprint_changed=last_changed,
                metadata={"unknown_surface": True},
            )

        if len(recovered) >= max(0, int(max_recoveries)) or fingerprint in seen_fingerprints:
            return PostVerificationCompletionResult(
                safe_for_identity_guard=False,
                screen_type=screen_type,
                recovery_count=len(recovered),
                recovered_screen_types=tuple(recovered),
                failure_reason="post_verification_recovery_exhausted",
                fingerprint_changed=last_changed,
                metadata={"anti_loop_blocked": True},
            )

        seen_fingerprints.add(fingerprint)
        press = getattr(d, "press", None)
        if not callable(press):
            return PostVerificationCompletionResult(
                safe_for_identity_guard=False,
                screen_type=screen_type,
                recovery_count=len(recovered),
                recovered_screen_types=tuple(recovered),
                failure_reason="post_verification_back_unavailable",
            )
        try:
            press("back")
        except Exception:
            return PostVerificationCompletionResult(
                safe_for_identity_guard=False,
                screen_type=screen_type,
                recovery_count=len(recovered),
                recovered_screen_types=tuple(recovered),
                failure_reason="post_verification_back_failed",
            )

        recovered.append(screen_type)
        log(
            "info",
            "post_verification_known_surface_back_recovery",
            screen_type=screen_type,
            recovery_count=len(recovered),
        )
        sleeper(POST_VERIFICATION_RECOVERY_WAIT_SECONDS)
        fresh_hierarchy = _dump_hierarchy(d)
        last_changed = _fingerprint(fresh_hierarchy) != fingerprint
        if not last_changed:
            return PostVerificationCompletionResult(
                safe_for_identity_guard=False,
                screen_type=screen_type,
                recovery_count=len(recovered),
                recovered_screen_types=tuple(recovered),
                failure_reason="post_verification_surface_unchanged_after_back",
                fingerprint_changed=False,
                metadata={"anti_loop_blocked": True},
            )

    return PostVerificationCompletionResult(
        safe_for_identity_guard=False,
        screen_type="unknown",
        recovery_count=len(recovered),
        recovered_screen_types=tuple(recovered),
        failure_reason="post_verification_recovery_exhausted",
        fingerprint_changed=last_changed,
    )


def classify_post_verification_surface(
    hierarchy: str,
    *,
    expected_package_name: str = "",
) -> tuple[str, dict[str, Any]]:
    signals = extract_login_screen_signals_from_hierarchy(str(hierarchy or ""))
    screen_type = str(signals.get("screen_type") or "unknown")
    normalized = _normalized_hierarchy_text(hierarchy)

    if _hierarchy_proves_connected_instagram_surface(hierarchy, expected_package_name):
        if screen_type not in {"active_account_profile"}:
            screen_type = "active_account_home"
        return screen_type, {**signals, "connected_surface_proof": "instagram_bottom_navigation"}

    if _is_sync_contacts_prompt(normalized):
        return "post_login_sync_contacts_prompt", signals
    if _is_discover_people_prompt(normalized):
        return "post_login_discover_people_prompt", signals
    return screen_type, signals


def hierarchy_proves_expected_instagram_foreground(hierarchy: str, expected_package_name: str) -> bool:
    return _hierarchy_proves_connected_instagram_surface(hierarchy, expected_package_name)


def _hierarchy_proves_connected_instagram_surface(hierarchy: str, expected_package_name: str) -> bool:
    raw = str(hierarchy or "")
    package = str(expected_package_name or "").strip()
    if package and f'package="{package}"' not in raw:
        return False
    has_tab_bar = bool(re.search(r'resource-id="[^"]*:id/tab_bar"', raw))
    has_profile_tab = bool(
        re.search(r'resource-id="[^"]*:id/(?:profile_tab|tab_profile|bottom_bar_profile)"', raw)
        or re.search(r'content-desc="(?:Profile|Profil)"', raw, re.IGNORECASE)
    )
    return has_tab_bar and has_profile_tab


def _is_sync_contacts_prompt(text: str) -> bool:
    return (
        "sync contacts" in text
        and any(marker in text for marker in ("find people", "connect contacts", "continue", "not now"))
    )


def _is_discover_people_prompt(text: str) -> bool:
    return (
        "discover people" in text
        and any(marker in text for marker in ("find people to follow", "get started", "continue", "skip"))
    )


def _dump_hierarchy(d: Any) -> str:
    try:
        try:
            return str(d.dump_hierarchy(compressed=False) or "")
        except TypeError:
            return str(d.dump_hierarchy() or "")
    except Exception:
        return ""


def _normalized_hierarchy_text(hierarchy: str) -> str:
    return re.sub(r"\s+", " ", str(hierarchy or "").lower()).strip()


def _fingerprint(hierarchy: str) -> str:
    return hashlib.sha256(str(hierarchy or "").encode("utf-8", errors="replace")).hexdigest()
