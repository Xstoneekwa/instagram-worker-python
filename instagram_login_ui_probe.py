"""Minimal Instagram login UI probe.

Entry 2E-5B observes already-available UI hierarchy only. It does not start the
app, tap, type credentials, read Vault secrets, or expose raw XML/screenshot
data in metadata.
"""

from __future__ import annotations

import re
from html import unescape
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
PASSWORD_REQUIRED_DIALOG_PATTERNS = (
    "password required",
    "enter your password to continue",
    "mot de passe requis",
    "saisissez votre mot de passe pour continuer",
)
GOOGLE_PASSWORD_MANAGER_PATTERNS = (
    "google password manager",
    "gestionnaire de mots de passe google",
)
SAVE_PASSWORD_FOR_INSTAGRAM_PATTERNS = (
    "save password for instagram",
    "enregistrer le mot de passe pour instagram",
    "enregistrer votre mot de passe pour instagram",
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


def extract_login_screen_signals_from_hierarchy(
    hierarchy_xml: str | None,
    *,
    expected_username: str | None = None,
) -> dict[str, Any]:
    raw_hierarchy = str(hierarchy_xml or "")
    text = _normalize_hierarchy_text(hierarchy_xml)
    has_continue_button = _has_phrase(text, "continue")
    has_use_another_profile = _has_phrase(text, "use another profile")
    has_create_new_account = _has_phrase(text, "create new account")
    has_forgot_password = _has_phrase(text, "forgot password")
    has_meta = _has_phrase(text, "meta")
    has_edit_profile = _has_phrase(text, "edit profile")
    has_share_profile = _has_phrase(text, "share profile")
    has_profile_stats = all(_has_phrase(text, phrase) for phrase in ("posts", "followers", "following"))
    has_add_instagram_account = _contains_any(
        text,
        (
            "add instagram account",
            "add profile",
            "ajouter un compte instagram",
            "ajouter un profil",
        ),
    )
    has_accounts_center = _has_phrase(text, "go to accounts center")
    has_add_account_title = _has_phrase(text, "add account")
    has_log_into_existing_account = _contains_any(
        text,
        (
            "log into existing account",
            "se connecter à un compte existant",
            "se connecter a un compte existant",
            "ajouter un compte existant",
        ),
    )
    has_profile_menu = _has_profile_menu_signal(raw_hierarchy, text)
    has_settings_and_activity = _contains_any(
        text,
        (
            "settings and activity",
            "paramètres et activité",
            "parametres et activite",
        ),
    )
    has_accounts_center = _has_phrase(text, "accounts center")
    has_how_you_use_instagram = _has_phrase(text, "how you use instagram")
    has_settings_page_sections = _contains_any(
        text,
        (
            "how others can interact with you",
            "what you see",
            "follow and invite friends",
            "also from meta",
        ),
    )
    has_more_info_support = _has_phrase(text, "more info and support")
    has_login_section = _has_phrase(text, "login")
    has_add_account = _contains_any(text, ("add account", "ajouter un compte"))
    has_log_out = _contains_any(
        text,
        ("log out", "logout", "se déconnecter", "se deconnecter", "déconnexion", "deconnexion"),
    )
    has_save_login_info_prompt = _contains_any(
        text,
        (
            "save your login info",
            "enregistrer vos informations de connexion",
            "enregistrer les informations de connexion",
        ),
    )
    has_not_now = _contains_any(text, ("not now", "pas maintenant", "plus tard"))
    has_save_button = _contains_any(text, ("save", "enregistrer"))
    has_logout_confirmation_prompt = _contains_any(
        text,
        (
            "log out of your account",
            "se déconnecter de votre compte",
            "se deconnecter de votre compte",
        ),
    )
    has_cancel = _contains_any(text, ("cancel", "annuler"))
    has_home_feed_markers = (
        _has_phrase(text, "your story")
        or _has_phrase(text, "suggested for you")
        or ("instagram" in text and _has_phrase(text, "follow"))
    )
    has_username_field = "username, email or mobile number" in text or (
        "username" in text and ("email" in text or "mobile" in text)
    )
    has_password_field = "password" in text
    has_login_button = "log in" in text
    has_ok_button = _has_phrase(text, "ok")
    has_password_required_dialog = _contains_any(text, PASSWORD_REQUIRED_DIALOG_PATTERNS) and has_ok_button
    has_google_password_manager = _contains_any(text, GOOGLE_PASSWORD_MANAGER_PATTERNS)
    has_save_password_for_instagram = _contains_any(text, SAVE_PASSWORD_FOR_INSTAGRAM_PATTERNS)
    has_google_save_password_prompt = has_google_password_manager and has_save_password_for_instagram and has_continue_button
    suggested_username = _extract_suggested_username(text)
    available_usernames = _extract_available_usernames(text)
    normalized_expected_username = _normalize_username_candidate(expected_username or "")
    expected_username_matches = [
        username for username in available_usernames if username == normalized_expected_username
    ]
    actual_logged_in_username = _extract_profile_username(text, available_usernames)
    overlay_type = _password_overlay_type(text)
    overlay_present = bool(overlay_type)
    transition_loading = _has_phrase(text, "loading")
    edit_text_values = _extract_edit_text_values(raw_hierarchy)
    prefilled_username = _extract_prefilled_username(edit_text_values)
    username_prefilled_present = bool(prefilled_username)
    username_field_editable_present = has_username_field or bool(edit_text_values)

    if has_google_save_password_prompt:
        screen_type = "google_password_manager_save_prompt"
    elif has_password_required_dialog:
        screen_type = "password_required_dialog"
    elif has_logout_confirmation_prompt and has_log_out and has_cancel:
        screen_type = "logout_confirmation_prompt"
    elif has_save_login_info_prompt and has_not_now and has_save_button:
        screen_type = "save_login_info_prompt"
    elif has_settings_and_activity and (
        has_log_out
        or has_add_account
        or has_login_section
        or has_accounts_center
        or has_how_you_use_instagram
        or has_settings_page_sections
        or has_more_info_support
    ):
        screen_type = "settings_and_activity"
    elif has_settings_and_activity:
        screen_type = "profile_menu_sheet"
    elif has_add_account_title and has_log_into_existing_account and has_create_new_account:
        screen_type = "add_account_sheet"
    elif has_add_instagram_account and has_accounts_center:
        screen_type = "account_switcher_sheet"
    elif actual_logged_in_username and has_edit_profile and has_share_profile and has_profile_stats:
        screen_type = "active_account_profile"
    elif has_continue_button and has_use_another_profile and suggested_username:
        screen_type = "continue_as_candidate"
    elif has_home_feed_markers:
        screen_type = "active_account_home"
    elif len(available_usernames) >= 2 and has_use_another_profile and has_create_new_account:
        screen_type = "account_picker"
    elif username_prefilled_present and has_password_field and has_login_button:
        screen_type = "login_form_prefilled_username"
    elif suggested_username and has_password_field and has_login_button and not has_username_field:
        screen_type = "continue_password_only"
    elif has_username_field and has_password_field and has_login_button:
        screen_type = "login_form_empty"
    else:
        screen_type = "unknown"

    return {
        "screen_type": screen_type,
        "continue_as_candidate": screen_type == "continue_as_candidate",
        "suggested_username": suggested_username,
        "available_usernames": available_usernames,
        "actual_logged_in_username": actual_logged_in_username,
        "expected_username_present": bool(expected_username_matches) if expected_username else None,
        "expected_username_match_count": len(expected_username_matches) if expected_username else None,
        "account_picker": screen_type == "account_picker",
        "has_continue_button": has_continue_button,
        "has_use_another_profile": has_use_another_profile,
        "has_use_another_profile_button": has_use_another_profile,
        "has_create_new_account_button": has_create_new_account,
        "has_username_field": has_username_field or screen_type == "login_form_prefilled_username",
        "username_field_present": has_username_field or screen_type == "login_form_prefilled_username",
        "username_field_editable_present": username_field_editable_present,
        "username_prefilled_present": username_prefilled_present,
        "prefilled_username": prefilled_username,
        "has_password_field": has_password_field,
        "password_field_present": has_password_field,
        "has_login_button": has_login_button,
        "login_button_present": has_login_button,
        "has_ok_button": has_ok_button,
        "password_required_dialog_present": has_password_required_dialog,
        "save_password_prompt_present": has_google_save_password_prompt,
        "google_password_manager_save_prompt": screen_type == "google_password_manager_save_prompt",
        "save_password_prompt": screen_type == "google_password_manager_save_prompt",
        "username_editable_present": screen_type in {"login_form_empty", "login_form_prefilled_username"}
        and username_field_editable_present,
        "password_field_editable_present": screen_type in {
            "login_form_empty",
            "login_form_prefilled_username",
            "continue_password_only",
        }
        and has_password_field,
        "forgot_password_present": has_forgot_password,
        "meta_present": has_meta,
        "has_add_instagram_account_button": has_add_instagram_account,
        "has_accounts_center_button": has_accounts_center,
        "has_log_into_existing_account_button": has_log_into_existing_account,
        "profile_menu_ready": screen_type == "active_account_profile" and has_profile_menu,
        "profile_menu_missing_transient": screen_type == "active_account_profile" and not has_profile_menu,
        "settings_and_activity": screen_type == "settings_and_activity",
        "profile_menu_sheet": screen_type == "profile_menu_sheet",
        "has_settings_and_activity_button": has_settings_and_activity,
        "has_add_account_button": has_add_account,
        "has_log_out_button": has_log_out,
        "save_login_info_prompt": screen_type == "save_login_info_prompt",
        "has_not_now_button": has_not_now,
        "has_save_button": has_save_button,
        "logout_confirmation_prompt": screen_type == "logout_confirmation_prompt",
        "has_cancel_button": has_cancel,
        "active_account_home": screen_type == "active_account_home",
        "active_account_profile": screen_type == "active_account_profile",
        "account_switcher_sheet": screen_type == "account_switcher_sheet",
        "add_account_sheet": screen_type == "add_account_sheet",
        "continue_password_only": screen_type == "continue_password_only",
        "overlay_present": overlay_present,
        "overlay_type": overlay_type,
        "overlay_blocking_business": False,
        "password_required": screen_type in {
            "login_form_empty",
            "login_form_prefilled_username",
            "continue_password_only",
        },
        "ready_for_credentials_flow": screen_type in {"login_form_empty", "login_form_prefilled_username"}
        and (has_username_field or username_prefilled_present)
        and has_password_field
        and has_login_button,
        "ready_for_password_submit": screen_type == "continue_password_only" and has_password_field and has_login_button,
        "transition_loading": transition_loading,
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

    if _contains_any(text, PASSWORD_REQUIRED_DIALOG_PATTERNS):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.UNKNOWN,
            ok=False,
            reason="password_required_dialog",
            metadata={**metadata, "detection_reason": "password_required_dialog", "password_required_dialog_present": True},
        )

    if _contains_any(text, GOOGLE_PASSWORD_MANAGER_PATTERNS) and _contains_any(text, SAVE_PASSWORD_FOR_INSTAGRAM_PATTERNS):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.UNKNOWN,
            ok=False,
            reason="google_password_manager_save_prompt",
            metadata={
                **metadata,
                "detection_reason": "google_password_manager_save_prompt",
                "save_password_prompt_present": True,
            },
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
    visible_values = _extract_visible_text_values(raw)
    text = " ".join(visible_values) if visible_values else raw
    text = re.sub(r"[-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _extract_visible_text_values(hierarchy_xml: str) -> list[str]:
    values: list[str] = []
    for attr in ("text", "content-desc", "contentDescription"):
        for match in re.finditer(rf'{attr}="([^"]*)"', hierarchy_xml):
            value = unescape(match.group(1)).strip()
            if value:
                values.append(value)
    return values


def _extract_edit_text_values(hierarchy_xml: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(r"<node\b[^>]*>", str(hierarchy_xml or "")):
        node = match.group(0)
        if "EditText" not in node and 'editable="true"' not in node:
            continue
        text_match = re.search(r'text="([^"]*)"', node)
        value = unescape(text_match.group(1)).strip() if text_match else ""
        if value:
            values.append(value)
    return values


def _extract_prefilled_username(values: list[str]) -> str:
    for value in values:
        candidate = _normalize_username_candidate(value)
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered in {
            "username",
            "email",
            "mobile",
            "number",
            "password",
            "log",
            "login",
        }:
            continue
        if re.fullmatch(r"[a-z0-9._]{1,30}", candidate):
            return candidate
    return ""


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _has_phrase(text: str, phrase: str) -> bool:
    normalized = re.escape(phrase.lower()).replace(r"\ ", r"\s+")
    return bool(re.search(rf"\b{normalized}\b", text))


def _password_overlay_type(text: str) -> str:
    password_manager_phrases = (
        "suggest strong password",
        "save to your google account",
        "saved passwords",
        "password manager",
        "generate password",
        "mots de passe enregistr",
        "suggérer un mot de passe",
        "suggerer un mot de passe",
        "gestionnaire de mots de passe",
    )
    autofill_phrases = (
        "autofill",
        "saisie automatique",
    )
    if any(phrase in text for phrase in password_manager_phrases):
        return "password_manager_or_autofill"
    if any(phrase in text for phrase in autofill_phrases):
        return "password_manager_or_autofill"
    return ""


def _count_pattern_hits(text: str, patterns: tuple[str, ...]) -> int:
    return sum(1 for pattern in patterns if pattern in text)


def _extract_suggested_username(text: str) -> str:
    continue_as_match = re.search(r"\bcontinue as\s+@?([a-z0-9._]{1,30})\b", text)
    if continue_as_match:
        candidate = _normalize_username_candidate(continue_as_match.group(1))
        if candidate:
            return candidate

    candidates = re.findall(r"@?[a-z0-9._]{1,30}", text)
    for raw in candidates:
        candidate = _normalize_username_candidate(raw)
        if candidate and ("_" in candidate or "." in candidate):
            return candidate
    return ""


def _extract_available_usernames(text: str) -> list[str]:
    usernames: list[str] = []
    for raw in re.findall(r"@?[a-z0-9._]{1,30}", text):
        candidate = _normalize_username_candidate(raw)
        if candidate and ("_" in candidate or "." in candidate) and candidate not in usernames:
            usernames.append(candidate)
    return usernames


def _extract_profile_username(text: str, available_usernames: list[str]) -> str:
    if not available_usernames:
        return ""
    if _has_phrase(text, "edit profile") and _has_phrase(text, "share profile"):
        return available_usernames[0]
    return ""


def _has_profile_menu_signal(raw_hierarchy: str, text: str) -> bool:
    if _contains_any(
        text,
        (
            "options",
            "menu",
            "settings and activity",
            "paramètres et activité",
            "parametres et activite",
        ),
    ):
        return True
    lowered = str(raw_hierarchy or "").lower()
    return any(
        fragment in lowered
        for fragment in (
            "action_bar_button",
            "action_bar_action",
            "overflow",
            "hamburger",
            "profile_menu",
        )
    )


def _normalize_username_candidate(value: str) -> str:
    candidate = str(value or "").lstrip("@").strip("._").lower()
    if not candidate or candidate in COMMON_NON_USERNAME_TEXTS:
        return ""
    if not re.fullmatch(r"[a-z0-9._]{1,30}", candidate):
        return ""
    if not re.search(r"[a-z]", candidate):
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)+", candidate):
        return ""
    if "." in candidate and "_" not in candidate and candidate.count(".") >= 2:
        return ""
    return candidate
