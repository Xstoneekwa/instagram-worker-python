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
EMAIL_CODE_SENT_PATTERNS = (
    "enter the code we sent",
    "code we sent to",
    "code que nous avons envoyé",
    "code que nous avons envoye",
    "saisissez le code que nous",
    "entrez le code que nous",
)
EMAIL_CODE_HEADER_PATTERNS = (
    "check your email",
    "vérifiez votre e-mail",
    "vérifiez votre email",
    "verifiez votre e-mail",
    "verifiez votre email",
    "vérifiez vos e-mails",
    "verifiez vos e-mails",
)
EMAIL_CODE_ENTRY_PATTERNS = (
    "enter code",
    "saisir le code",
    "saisissez le code",
    "entrez le code",
    "entrer le code",
)
EMAIL_CODE_RESEND_PATTERNS = (
    "get a new code",
    "recevoir un nouveau code",
    "obtenir un nouveau code",
    "envoyer un nouveau code",
)
UNSUPPORTED_POST_SUBMIT_CHALLENGE_PATTERNS = (
    "try another way",
    "confirm your identity",
    "security check",
    "unusual login attempt",
    "approve this login",
    "approve login",
    "was this you",
    "verify it's you",
    "verify it’s you",
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
SAMSUNG_PASS_PATTERNS = (
    "samsung pass",
    "credential manager",
    "gestionnaire d'identifiants",
)
SAVE_PASSWORD_FOR_INSTAGRAM_PATTERNS = (
    "save password for instagram",
    "enregistrer le mot de passe pour instagram",
    "enregistrer votre mot de passe pour instagram",
)
JOIN_INSTAGRAM_LANDING_TITLE = "join instagram"
JOIN_INSTAGRAM_LANDING_SUBTITLE = "share what you're into with the people who get you"
JOIN_INSTAGRAM_EXISTING_PROFILE_BUTTON = "i already have a profile"
JOIN_INSTAGRAM_GET_STARTED_BUTTON = "get started"
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
POST_LOGIN_LOCATION_SERVICES_PROMPT_MARKERS = (
    "set up on new device",
    "to use location services",
    "allow instagram to access your location",
    "how you can use location services",
    "how we'll use this information",
    "how you can control this",
)
INSTAGRAM_TURN_ON_NOTIFICATIONS_TITLE = "turn on notifications"
INSTAGRAM_TURN_ON_NOTIFICATIONS_SUBTITLE_MARKERS = (
    "find out right away",
    "people follow you",
    "like and comment on your posts",
)
ANDROID_INSTAGRAM_NOTIFICATION_SETTINGS_MARKERS = (
    "allow notifications",
    "all notifications from this app are blocked",
    "notifications from this app are blocked",
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
    has_join_instagram_title = _has_phrase(text, JOIN_INSTAGRAM_LANDING_TITLE)
    has_join_instagram_subtitle = JOIN_INSTAGRAM_LANDING_SUBTITLE in text
    has_join_instagram_get_started = _has_phrase(text, JOIN_INSTAGRAM_GET_STARTED_BUTTON)
    has_join_instagram_existing_profile = _has_phrase(text, JOIN_INSTAGRAM_EXISTING_PROFILE_BUTTON)
    has_join_instagram_landing = (
        has_join_instagram_title
        and has_join_instagram_get_started
        and has_join_instagram_existing_profile
    )
    editable_fields = _extract_editable_field_signals(raw_hierarchy)
    has_username_field = bool(editable_fields["username_candidates"]) or "username, email or mobile number" in text or (
        "username" in text and ("email" in text or "mobile" in text)
    )
    # Do not treat the "Forgot password?" link as proof of a credential input.
    # A secret field must be backed by an editable node signal.
    has_password_field = bool(editable_fields["password_candidates"])
    has_login_button = "log in" in text
    has_ok_button = _has_phrase(text, "ok")
    has_password_required_dialog = _contains_any(text, PASSWORD_REQUIRED_DIALOG_PATTERNS) and has_ok_button
    has_google_password_manager = _contains_any(text, GOOGLE_PASSWORD_MANAGER_PATTERNS)
    has_samsung_pass = _contains_any(text, SAMSUNG_PASS_PATTERNS)
    has_save_password_for_instagram = _contains_any(text, SAVE_PASSWORD_FOR_INSTAGRAM_PATTERNS)
    has_google_save_password_prompt = has_google_password_manager and has_save_password_for_instagram and has_continue_button
    has_samsung_save_password_prompt = has_samsung_pass and has_save_password_for_instagram and has_cancel and has_save_button
    has_email_code_challenge = _is_email_code_challenge_text(text)
    has_post_login_location_services_prompt = _is_post_login_location_services_prompt_text(text)
    has_instagram_turn_on_notifications_prompt = _is_instagram_turn_on_notifications_prompt_text(text)
    has_android_instagram_notification_settings = _is_android_instagram_notification_settings_text(text)
    has_notifications_next_button = _has_phrase(text, "next")
    has_notifications_skip_button = _has_phrase(text, "skip")
    masked_email_present = _has_masked_email_signal(text)
    suggested_username = _extract_suggested_username(text)
    available_usernames = _extract_available_usernames(text)
    normalized_expected_username = _normalize_username_candidate(expected_username or "")
    expected_username_matches = [
        username for username in available_usernames if username == normalized_expected_username
    ]
    actual_logged_in_username = _extract_profile_username(raw_hierarchy, available_usernames)
    overlay_type = _password_overlay_type(text)
    overlay_present = bool(overlay_type)
    transition_loading = _has_phrase(text, "loading")
    edit_text_values = _extract_edit_text_values(raw_hierarchy)
    prefilled_username = _extract_prefilled_username(edit_text_values)
    username_prefilled_present = bool(prefilled_username)
    username_field_editable_present = has_username_field or bool(edit_text_values)

    if has_email_code_challenge:
        screen_type = "email_code_challenge"
    elif has_samsung_save_password_prompt:
        screen_type = "samsung_pass_save_password_prompt"
    elif has_google_save_password_prompt:
        screen_type = "google_password_manager_save_prompt"
    elif has_password_required_dialog:
        screen_type = "password_required_dialog"
    elif has_post_login_location_services_prompt:
        screen_type = "connected_post_login_location_services_prompt"
    elif has_android_instagram_notification_settings:
        screen_type = "android_instagram_notification_settings"
    elif has_instagram_turn_on_notifications_prompt:
        screen_type = "instagram_turn_on_notifications_prompt"
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
    elif has_join_instagram_landing:
        screen_type = "join_instagram_landing"
    elif len(available_usernames) >= 2 and has_use_another_profile and has_create_new_account:
        screen_type = "account_picker"
    elif username_prefilled_present and has_password_field and has_login_button:
        screen_type = "login_form_prefilled_username"
    elif suggested_username and has_password_field and has_login_button and not has_username_field:
        screen_type = "continue_password_only"
    elif has_username_field and has_password_field and has_login_button:
        screen_type = "login_form_empty"
    elif has_username_field and has_login_button and not has_password_field:
        screen_type = "login_form_username_step"
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
        "password_field_candidate_count": len(editable_fields["password_candidates"]),
        "password_field_proof": editable_fields["password_proof"],
        "has_login_button": has_login_button,
        "login_button_present": has_login_button,
        "has_ok_button": has_ok_button,
        "password_required_dialog_present": has_password_required_dialog,
        "save_password_prompt_present": has_google_save_password_prompt or has_samsung_save_password_prompt,
        "samsung_pass_save_password_prompt": screen_type == "samsung_pass_save_password_prompt",
        "samsung_pass_save_password_prompt_present": has_samsung_save_password_prompt,
        "google_password_manager_save_prompt": screen_type == "google_password_manager_save_prompt",
        "save_password_prompt": screen_type
        in {"google_password_manager_save_prompt", "samsung_pass_save_password_prompt"},
        "email_code_challenge_present": screen_type == "email_code_challenge",
        "challenge_type": "email" if screen_type == "email_code_challenge" else "",
        "masked_email_present": bool(masked_email_present) if screen_type == "email_code_challenge" else False,
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
        "post_login_location_services_prompt": screen_type == "connected_post_login_location_services_prompt",
        "connected_post_login_location_services_prompt": screen_type
        == "connected_post_login_location_services_prompt",
        "has_not_now_button": has_not_now,
        "has_save_button": has_save_button,
        "logout_confirmation_prompt": screen_type == "logout_confirmation_prompt",
        "has_cancel_button": has_cancel,
        "active_account_home": screen_type == "active_account_home",
        "active_account_profile": screen_type == "active_account_profile",
        "join_instagram_landing": screen_type == "join_instagram_landing",
        "join_instagram_landing_detected": screen_type == "join_instagram_landing",
        "has_join_instagram_title": has_join_instagram_title,
        "has_join_instagram_subtitle": has_join_instagram_subtitle,
        "has_get_started_button": has_join_instagram_get_started,
        "has_already_have_profile_button": has_join_instagram_existing_profile,
        "connected_post_login_setup": screen_type
        in {
            "connected_post_login_location_services_prompt",
            "instagram_turn_on_notifications_prompt",
            "android_instagram_notification_settings",
        },
        "instagram_turn_on_notifications_prompt": screen_type == "instagram_turn_on_notifications_prompt",
        "notifications_prompt_detected": screen_type == "instagram_turn_on_notifications_prompt",
        "has_notifications_next_button": has_notifications_next_button,
        "has_notifications_skip_button": has_notifications_skip_button,
        "android_instagram_notification_settings": screen_type == "android_instagram_notification_settings",
        "android_notification_settings_detected": screen_type == "android_instagram_notification_settings",
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
        "ready_for_username_step": screen_type == "login_form_username_step" and has_username_field and has_login_button,
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

    if _contains_any(text, SAMSUNG_PASS_PATTERNS) and _contains_any(text, SAVE_PASSWORD_FOR_INSTAGRAM_PATTERNS):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.UNKNOWN,
            ok=False,
            reason="samsung_pass_save_password_prompt",
            metadata={
                **metadata,
                "detection_reason": "samsung_pass_save_password_prompt",
                "screen_type": "samsung_pass_save_password_prompt",
                "save_password_prompt_present": True,
                "samsung_pass_save_password_prompt_present": True,
            },
        )

    if _contains_any(text, GOOGLE_PASSWORD_MANAGER_PATTERNS) and _contains_any(text, SAVE_PASSWORD_FOR_INSTAGRAM_PATTERNS):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.UNKNOWN,
            ok=False,
            reason="google_password_manager_save_prompt",
            metadata={
                **metadata,
                "detection_reason": "google_password_manager_save_prompt",
                "screen_type": "google_password_manager_save_prompt",
                "save_password_prompt_present": True,
            },
        )

    if _is_email_code_challenge_text(text):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.VERIFICATION_PENDING,
            ok=False,
            reason="email_verification_code_required",
            metadata={
                **metadata,
                "detection_reason": "email_verification_code_required",
                "screen_type": "email_code_challenge",
                "challenge_type": "email",
                "masked_email_present": _has_masked_email_signal(text),
            },
        )

    if _is_post_login_location_services_prompt_text(text):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.CONNECTED,
            ok=True,
            reason="connected_post_login_location_services_prompt",
            metadata={
                **metadata,
                "detection_reason": "connected_post_login_location_services_prompt",
                "screen_type": "connected_post_login_location_services_prompt",
                "post_login_location_services_prompt": True,
                "connected_post_login_setup": True,
            },
        )

    if _is_android_instagram_notification_settings_text(text):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.CONNECTED,
            ok=True,
            reason="android_instagram_notification_settings",
            metadata={
                **metadata,
                "detection_reason": "android_instagram_notification_settings",
                "screen_type": "android_instagram_notification_settings",
                "android_notification_settings_detected": True,
                "connected_post_login_setup": True,
            },
        )

    if _is_instagram_turn_on_notifications_prompt_text(text):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.CONNECTED,
            ok=True,
            reason="instagram_turn_on_notifications_prompt",
            metadata={
                **metadata,
                "detection_reason": "instagram_turn_on_notifications_prompt",
                "screen_type": "instagram_turn_on_notifications_prompt",
                "notifications_prompt_detected": True,
                "has_notifications_skip_button": _has_phrase(text, "skip"),
                "has_notifications_next_button": _has_phrase(text, "next"),
                "connected_post_login_setup": True,
            },
        )

    if (
        _has_phrase(text, JOIN_INSTAGRAM_LANDING_TITLE)
        and _has_phrase(text, JOIN_INSTAGRAM_GET_STARTED_BUTTON)
        and _has_phrase(text, JOIN_INSTAGRAM_EXISTING_PROFILE_BUTTON)
    ):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.UNKNOWN,
            ok=False,
            reason="join_instagram_landing_detected",
            metadata={
                **metadata,
                "detection_reason": "join_instagram_landing_detected",
                "screen_type": "join_instagram_landing",
                "join_instagram_landing_detected": True,
                "has_get_started_button": True,
                "has_already_have_profile_button": True,
            },
        )

    if _is_unsupported_post_submit_challenge_text(text):
        return LoginUiProbeResult(
            outcome=LoginProbeOutcome.UNSUPPORTED_POST_SUBMIT_CHALLENGE,
            ok=False,
            reason="unsupported_post_submit_challenge",
            metadata={
                **metadata,
                "detection_reason": "unsupported_post_submit_challenge",
                "screen_type": "unsupported_post_submit_challenge",
                "challenge_type": "unknown",
                "human_review_required": True,
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


def _extract_editable_field_signals(hierarchy_xml: str) -> dict[str, Any]:
    username_candidates: list[dict[str, str]] = []
    password_candidates: list[dict[str, str]] = []
    password_proof = ""
    for match in re.finditer(r"<node\b[^>]*>", str(hierarchy_xml or "")):
        node = match.group(0)
        attrs = {
            key: unescape(value).strip()
            for key, value in re.findall(r'([\w:-]+)="([^"]*)"', node)
        }
        normalized = {
            str(key).lower(): str(value).strip().lower()
            for key, value in attrs.items()
        }
        resource = normalized.get("resource-id") or normalized.get("resourcename") or ""
        description = normalized.get("content-desc") or normalized.get("contentdescription") or ""
        visible_labels = {
            normalized.get("text", ""),
            normalized.get("hint", ""),
            description,
        }
        localized_password_label = bool(
            visible_labels & {"password", "mot de passe"}
        )
        is_editable = "EditText" in node or normalized.get("editable") == "true"
        if not is_editable:
            if localized_password_label:
                password_candidates.append(attrs)
                if not password_proof:
                    password_proof = "localized_accessibility_label"
            continue
        label = " ".join(
            value
            for value in (
                normalized.get("text", ""),
                normalized.get("hint", ""),
                description,
                resource,
                normalized.get("input-type", ""),
                normalized.get("inputtype", ""),
            )
            if value
        )
        password_attr = normalized.get("password") == "true"
        input_type_password = "password" in normalized.get("input-type", "") or "password" in normalized.get("inputtype", "")
        resource_password = "password" in resource or "passcode" in resource
        label_password = localized_password_label
        masked_value = _looks_like_masked_editable_value(normalized.get("text", ""))
        if password_attr or input_type_password or resource_password or label_password or masked_value:
            password_candidates.append(attrs)
            if not password_proof:
                password_proof = (
                    "android_password_property"
                    if password_attr
                    else "android_input_type"
                    if input_type_password
                    else "resource_id"
                    if resource_password
                    else "localized_accessibility_label"
                    if label_password
                    else "masked_editable_value"
                )
            continue
        if (
            "username" in label
            or "user_name" in resource
            or "login" in resource
            or "email" in label
            or "mobile" in label
            or "nom d'utilisateur" in label
            or "nom d’utilisateur" in label
        ):
            username_candidates.append(attrs)
            continue
        # On username-only screens Instagram can expose a single unlabeled
        # EditText. It is safe to treat it as the username field only when no
        # credential-field proof exists on that node.
        username_candidates.append(attrs)
    return {
        "username_candidates": username_candidates,
        "password_candidates": password_candidates,
        "password_proof": password_proof,
    }


def _looks_like_masked_editable_value(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and re.fullmatch(r"[\u2022\u25cf\u25e6\u2219*]+", text))


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


def _is_email_code_challenge_text(text: str) -> bool:
    if _contains_any(text, EMAIL_CODE_SENT_PATTERNS):
        return True

    has_header = _contains_any(text, EMAIL_CODE_HEADER_PATTERNS)
    has_entry = _contains_any(text, EMAIL_CODE_ENTRY_PATTERNS)
    has_resend = _contains_any(text, EMAIL_CODE_RESEND_PATTERNS)
    has_masked_email = _has_masked_email_signal(text)

    if has_header and has_entry:
        return True
    if has_resend and (has_header or has_masked_email or has_entry):
        return True

    # Preserve the original strict English quartet for stable regression coverage.
    return (
        _has_phrase(text, "check your email")
        and _has_phrase(text, "enter the code we sent")
        and _has_phrase(text, "enter code")
        and _has_phrase(text, "try another way")
    )


def _is_unsupported_post_submit_challenge_text(text: str) -> bool:
    if _is_email_code_challenge_text(text):
        return False
    if _contains_any(text, NEEDS_2FA_PATTERNS) and _has_phrase(text, "authentication code"):
        return False
    if _contains_any(text, CHECKPOINT_PATTERNS):
        return False
    if _contains_any(text, LOGIN_FAILED_PATTERNS):
        return False
    if _contains_any(text, LOGGED_OUT_PATTERNS) and _has_phrase(text, "log in"):
        return False
    return _contains_any(text, UNSUPPORTED_POST_SUBMIT_CHALLENGE_PATTERNS)


def _is_post_login_location_services_prompt_text(text: str) -> bool:
    marker_hits = _count_pattern_hits(text, POST_LOGIN_LOCATION_SERVICES_PROMPT_MARKERS)
    return (
        marker_hits >= 3
        and _has_phrase(text, "set up on new device")
        and _has_phrase(text, "continue")
        and "location" in text
    )


def _is_instagram_turn_on_notifications_prompt_text(text: str) -> bool:
    if not _has_phrase(text, INSTAGRAM_TURN_ON_NOTIFICATIONS_TITLE):
        return False
    return _contains_any(text, INSTAGRAM_TURN_ON_NOTIFICATIONS_SUBTITLE_MARKERS)


def _is_android_instagram_notification_settings_text(text: str) -> bool:
    return _contains_any(text, ANDROID_INSTAGRAM_NOTIFICATION_SETTINGS_MARKERS)


def _has_masked_email_signal(text: str) -> bool:
    normalized = str(text or "").lower()
    if "@" not in normalized:
        return False
    return bool(re.search(r"[a-z0-9._%+-]*[*•]{2,}[a-z0-9._%+-]*@[a-z0-9.-]+\.[a-z]{2,}", normalized))


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


def _extract_profile_username(raw_hierarchy: str, available_usernames: list[str]) -> str:
    if not available_usernames:
        return ""
    text = _normalize_hierarchy_text(raw_hierarchy)
    if not (_has_phrase(text, "edit profile") and _has_phrase(text, "share profile")):
        return ""

    scored: list[tuple[int, int, str]] = []
    for order, node in enumerate(re.findall(r"<node\b[^>]*>", str(raw_hierarchy or ""), re.IGNORECASE)):
        text_match = re.search(r'\btext="([^"]*)"', node, re.IGNORECASE)
        raw_value = unescape(text_match.group(1)) if text_match else ""
        candidate = _normalize_username_candidate(raw_value)
        if not candidate or candidate not in available_usernames:
            continue
        resource_match = re.search(r'\bresource-id="([^"]*)"', node, re.IGNORECASE)
        resource_id = (resource_match.group(1) if resource_match else "").lower()
        bounds_match = re.search(
            r'\bbounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            node,
            re.IGNORECASE,
        )
        top = int(bounds_match.group(2)) if bounds_match else 10_000
        score = 0
        if "action_bar_title" in resource_id or "profile_username" in resource_id:
            score += 100
        if bounds_match and top < 450:
            score += 50
        if "suggest" in resource_id or (bounds_match and top > 800):
            score -= 100
        scored.append((score, -order, candidate))

    if scored:
        score, _order, candidate = max(scored)
        if score >= 0:
            return candidate
        return ""
    return available_usernames[0]


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
