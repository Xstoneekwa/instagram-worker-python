"""Minimal Instagram login UI probe.

Entry 2E-5B observes already-available UI hierarchy only. It does not start the
app, tap, type credentials, read Vault secrets, or expose raw XML/screenshot
data in metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from instagram_login_status_classifier import LoginProbeOutcome, clean_login_probe_metadata
from logs import log


LOGIN_UI_PROBE_VERSION = "v1"

LOGIN_FAILED_PATTERNS = (
    "incorrect password",
    "wrong password",
    "couldn't log in",
    "could not log in",
    "invalid username",
    "your password was incorrect",
    "sorry, your password was incorrect",
)
NEEDS_2FA_PATTERNS = (
    "two-factor",
    "two factor",
    "2fa",
    "authentication code",
    "security code",
    "enter code",
    "confirmation code",
)
CHECKPOINT_PATTERNS = (
    "checkpoint",
    "challenge",
    "help us confirm",
    "confirm it's you",
    "confirm it’s you",
    "suspicious login attempt",
    "verify your account",
)
LOGGED_OUT_PATTERNS = (
    "log in to instagram",
    "username",
    "password",
    "forgot password",
    "continue as",
)
CONNECTED_PATTERNS = (
    "home",
    "search",
    "reels",
    "profile",
    "activity",
    "feed",
    "direct",
    "new post",
)
COMMON_NON_USERNAME_TEXTS = {
    "continue",
    "use",
    "another",
    "profile",
    "create",
    "new",
    "account",
    "instagram",
    "meta",
    "english",
    "us",
    "username",
    "email",
    "mobile",
    "number",
    "password",
    "log",
    "in",
    "forgot",
}


@dataclass(frozen=True)
class LoginUiProbeResult:
    outcome: LoginProbeOutcome
    ok: bool
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def detect_login_probe_outcome_from_hierarchy(hierarchy_xml: str | None) -> LoginProbeOutcome:
    return probe_login_ui_from_hierarchy(hierarchy_xml).outcome


def extract_login_screen_signals_from_hierarchy(hierarchy_xml: str | None) -> dict[str, Any]:
    text = _normalize_hierarchy_text(hierarchy_xml)
    has_continue_button = "continue" in text
    has_use_another_profile = "use another profile" in text
    has_username_field = "username, email or mobile number" in text or (
        "username" in text and ("email" in text or "mobile" in text)
    )
    has_password_field = "password" in text
    has_login_button = "log in" in text
    suggested_username = _extract_suggested_username(text)

    if has_continue_button and has_use_another_profile and suggested_username:
        screen_type = "continue_as_candidate"
    elif has_username_field and has_password_field and has_login_button:
        screen_type = "login_form_empty"
    else:
        screen_type = "unknown"

    return {
        "screen_type": screen_type,
        "suggested_username": suggested_username,
        "has_continue_button": has_continue_button,
        "has_use_another_profile": has_use_another_profile,
        "has_username_field": has_username_field,
        "has_password_field": has_password_field,
        "has_login_button": has_login_button,
    }


def probe_login_ui_from_hierarchy(
    hierarchy_xml: str | None,
    *,
    stage: str = "login_ui_probe",
) -> LoginUiProbeResult:
    text = _normalize_hierarchy_text(hierarchy_xml)
    metadata = _probe_metadata(stage=stage)

    if not text:
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.UNKNOWN,
            ok=False,
            reason="empty_hierarchy",
            metadata=metadata,
        )

    for outcome, reason, patterns in (
        (LoginProbeOutcome.LOGIN_FAILED, "login_failed_signal", LOGIN_FAILED_PATTERNS),
        (LoginProbeOutcome.NEEDS_2FA, "two_factor_signal", NEEDS_2FA_PATTERNS),
        (LoginProbeOutcome.CHECKPOINT, "checkpoint_signal", CHECKPOINT_PATTERNS),
    ):
        if _contains_any(text, patterns):
            return LoginUiProbeResult(
                outcome=outcome,
                ok=outcome == LoginProbeOutcome.CONNECTED,
                reason=reason,
                metadata={**metadata, "detection_reason": reason},
            )

    logged_out_hits = _count_pattern_hits(text, LOGGED_OUT_PATTERNS)
    connected_hits = _count_pattern_hits(text, CONNECTED_PATTERNS)

    if logged_out_hits >= 2 and connected_hits < 3:
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.LOGGED_OUT,
            ok=False,
            reason="login_screen_signal",
            metadata={**metadata, "detection_reason": "login_screen_signal"},
        )

    if connected_hits >= 3 and logged_out_hits == 0:
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.CONNECTED,
            ok=True,
            reason="connected_ui_signal",
            metadata={**metadata, "detection_reason": "connected_ui_signal"},
        )

    return LoginUiProbeResult(
        outcome=LoginProbeOutcome.UNKNOWN,
        ok=False,
        reason="ambiguous_or_unknown_ui",
        metadata=metadata,
    )


def probe_instagram_login_ui(
    d: Any,
    *,
    account_id: str | None = None,
    expected_username: str | None = None,
    stage: str = "login_ui_probe",
) -> LoginUiProbeResult:
    metadata = _probe_metadata(stage=stage)
    if expected_username:
        metadata["expected_username_present"] = True

    try:
        try:
            hierarchy_xml = d.dump_hierarchy(compressed=False)
        except TypeError:
            hierarchy_xml = d.dump_hierarchy()
    except Exception as exc:
        log(
            "warning",
            "instagram_login_ui_probe_dump_failed",
            account_id=account_id or "",
            reason="dump_hierarchy_failed",
            error_type=type(exc).__name__,
            stage=stage,
        )
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.UNKNOWN,
            ok=False,
            reason="dump_hierarchy_failed",
            metadata=metadata,
            error="dump_hierarchy_failed",
        )

    result = probe_login_ui_from_hierarchy(str(hierarchy_xml or ""), stage=stage)
    return LoginUiProbeResult(
        outcome=result.outcome,
        ok=result.ok,
        reason=result.reason,
        metadata=clean_login_probe_metadata({**metadata, **result.metadata}),
        error=result.error,
    )


def _probe_metadata(*, stage: str) -> dict[str, Any]:
    return clean_login_probe_metadata(
        {
            "source": "provisioner",
            "stage": str(stage or "login_ui_probe"),
            "probe_version": LOGIN_UI_PROBE_VERSION,
            "probe_type": "login_ui",
        }
    )


def _normalize_hierarchy_text(hierarchy_xml: str | None) -> str:
    raw = str(hierarchy_xml or "")
    if not raw.strip():
        return ""
    text = re.sub(r"[-]+", " ", raw)
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _count_pattern_hits(text: str, patterns: tuple[str, ...]) -> int:
    return sum(1 for pattern in patterns if pattern in text)


def _extract_suggested_username(text: str) -> str:
    continue_as_match = re.search(r"\bcontinue as\s+@?([a-z0-9._]{1,30})\b", text)
    if continue_as_match:
        return continue_as_match.group(1).lstrip("@")

    candidates = re.findall(r"@?[a-z0-9._]{1,30}", text)
    for raw in candidates:
        candidate = raw.lstrip("@").strip("._")
        if not candidate or candidate in COMMON_NON_USERNAME_TEXTS:
            continue
        if "_" in candidate or "." in candidate:
            return candidate
    return ""
