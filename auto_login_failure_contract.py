"""Two-level Auto Login failure contract.

Internal worker reasons may contain vocabulary that is deliberately forbidden
by the public persistence constraints.  This module is the single boundary
that maps those reasons to a canonical, projectable error code while retaining
the exact internal reason for service-role-only diagnostics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


FORBIDDEN_PERSISTED_CODE_PATTERN = re.compile(
    r"(token|secret|authorization|cookie|service_role|vault|password)",
    re.IGNORECASE,
)
CANONICAL_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,119}$")
FALLBACK_ERROR_CODE = "unclassified_auto_login_failure"
CLIENT_SAFE_MESSAGE = (
    "La connexion Instagram n’a pas pu être finalisée. "
    "Notre équipe technique a été informée."
)


AUTO_LOGIN_REASON_PHASES = {
    "instagram_wrong_password": "submit_credentials",
    "auto_login_not_ready": "request",
    "active_request_exists": "request",
    "credentials_missing": "login_form",
    "credentials_fetch_failed": "login_form",
    "credentials_invalid": "login_form",
    "invalid_credentials": "submit_credentials",
    "credentials_not_found": "login_form",
    "credentials_username_missing": "login_form",
    "assignment_missing": "request",
    "assignment_not_found": "request",
    "assignment_device_missing_adb_serial": "request",
    "auto_login_app_instance_binding_missing": "request_binding",
    "assigned_instagram_app_instance_mismatch": "open_instagram",
    "device_busy": "device_lock",
    "device_lock_failed": "device_lock",
    "device_lock_held": "device_lock",
    "device_lock_release_failed": "cleanup",
    "request_expired": "request",
    "request_canceled": "request",
    "dispatcher_claim_timeout": "dispatcher_claim",
    "dispatcher_unavailable": "dispatcher_claim",
    "unsupported_login_run_type": "dispatcher_claim",
    "worker_start_failed": "open_instagram",
    "login_device_serial_required": "open_instagram",
    "app_start_failed": "open_instagram",
    "app_start_failed_after_retry": "open_instagram",
    "suggested_account_surface_unusable": "route_suggested_account",
    "use_another_profile_not_available": "route_suggested_account",
    "wrong_suggested_account_requires_admin_review": "route_suggested_account",
    "wrong_active_account_requires_admin_review": "identity_verification",
    "login_form_not_reached": "login_form",
    "login_form_not_validated": "login_form",
    "account_picker_expected_account_missing": "login_form",
    "expected_account_not_listed": "login_form",
    "instagram_surface_ambiguous": "detect_surface",
    "ambiguous_login_form": "login_form",
    "username_field_not_found": "login_form",
    "username_prefilled_not_editable": "login_form",
    "username_input_failed": "login_form",
    "login_button_not_found": "login_form",
    "login_submit_failed": "submit_credentials",
    "submit_failed": "submit_credentials",
    "network_login_failure": "submit_credentials",
    "email_challenge_detected": "email_challenge",
    "verification_code_required": "email_challenge",
    "verification_code_missing": "email_code_resume",
    "verification_code_expired": "email_code_resume",
    "verification_code_rejected": "email_code_resume",
    "verification_code_input_failed": "email_code_resume",
    "verification_resume_failed": "email_code_resume",
    "active_instagram_account_mismatch": "identity_verification",
    "expected_identity_not_proven": "identity_verification",
    "identity_guard_failed": "identity_verification",
    "actual_logged_in_username_not_detected": "identity_verification",
    "own_profile_open_failed": "identity_verification",
    "expected_account_username_missing": "identity_verification",
    "login_cleanup_failed": "cleanup",
    "subprocess_timeout": "cleanup",
    "post_submit_dump_failed": "submit_credentials",
    "post_submit_loading_timeout": "submit_credentials",
    "unknown_post_submit_outcome": "submit_credentials",
}

# Exact internal vocabulary that must never cross the public persistence or
# notification boundary.  Synonyms intentionally collapse to one canonical
# code per operator condition.
SENSITIVE_REASON_OVERRIDES = {
    # The internal detector may name the rejected input surface.  Persist a
    # precise but secret-safe authentication result that satisfies the DB
    # constraint forbidding credential-material vocabulary.
    "instagram_wrong_password": "instagram_credentials_rejected",
    "password_field_not_found": "credential_input_field_unavailable",
    "password_input_missing_or_not_accepted": "credential_input_not_confirmed",
    "password_input_not_confirmed": "credential_input_not_confirmed",
    "password_input_failed": "credential_input_failed",
    "fast_ime_password_input_failed": "credential_input_failed",
    "set_text_password_input_failed": "credential_input_failed",
    "password_input_unavailable": "credential_input_unavailable",
    "password_secret_missing": "credential_material_unavailable",
    "password_secret_invalid": "credential_material_invalid",
    "vault_secret_payload_missing_password": "credential_material_unavailable",
    "vault_secret_password_invalid": "credential_material_invalid",
    "vault_read_failed": "credential_lookup_failed",
    "secret_reader_failed": "credential_lookup_failed",
    "blocked_secret_payload_shape": "credential_payload_unsupported",
    "save_password_prompt_blocking": "credential_save_prompt_blocking",
    "save_password_prompt_dismiss_failed": "credential_save_prompt_dismiss_failed",
    "save_password_prompt_not_dismissed_after_2_attempts": "credential_save_prompt_dismiss_failed",
    "google_password_manager_save_prompt": "credential_save_prompt_blocking",
    "samsung_pass_save_password_prompt": "credential_save_prompt_blocking",
    "password_required_dialog": "credential_required_dialog",
    "password_required_dialog_reappeared": "credential_required_dialog",
    "password_required_ok_not_found": "credential_required_dialog_recovery_failed",
    "password_required_retry_failed": "credential_required_dialog_recovery_failed",
    "post_code_password_required": "credential_required_after_verification",
    "verification_code_secret_invalid": "verification_code_invalid",
    "publisher_token_missing": "status_publisher_unavailable",
    "supabase_service_role_missing": "persistence_configuration_unavailable",
}

PERSISTED_PHASES = {
    **AUTO_LOGIN_REASON_PHASES,
    "credential_input_field_unavailable": "login_form",
    "instagram_credentials_rejected": "submit_credentials",
    "credential_input_not_confirmed": "submit_credentials",
    "credential_input_failed": "submit_credentials",
    "credential_input_unavailable": "login_form",
    "credential_material_unavailable": "login_form",
    "credential_material_invalid": "login_form",
    "credential_lookup_failed": "login_form",
    "credential_payload_unsupported": "login_form",
    "credential_save_prompt_blocking": "submit_credentials",
    "credential_save_prompt_dismiss_failed": "submit_credentials",
    "credential_required_dialog": "submit_credentials",
    "credential_required_dialog_recovery_failed": "submit_credentials",
    "credential_required_after_verification": "email_code_resume",
    "verification_code_invalid": "email_code_resume",
    "status_publisher_unavailable": "cleanup",
    "persistence_configuration_unavailable": "request",
    FALLBACK_ERROR_CODE: "unknown",
}


@dataclass(frozen=True)
class AutoLoginFailureContract:
    internal_worker_reason: str
    persisted_error_code: str
    phase: str
    operator_message: str
    client_safe_message: str
    retryable: bool
    severity: str
    recommended_action: str

    def incident_metadata(self) -> dict[str, object]:
        """Return only projectable fields; never include the internal reason."""
        return {
            "reason_code": self.persisted_error_code,
            "phase": self.phase,
            "operator_message": self.operator_message,
            "client_safe_message": self.client_safe_message,
            "retryable": self.retryable,
            "severity": self.severity,
            "recommended_action": self.recommended_action,
        }


def persisted_error_code_is_safe(value: str) -> bool:
    code = str(value or "").strip()
    return bool(
        CANONICAL_CODE_PATTERN.fullmatch(code)
        and not FORBIDDEN_PERSISTED_CODE_PATTERN.search(code)
    )


def normalize_auto_login_failure(
    internal_reason: str | None,
    *,
    phase: str | None = None,
    correction_deployed: bool = False,
) -> AutoLoginFailureContract:
    raw_internal = str(internal_reason or "").strip().lower()
    internal = (
        raw_internal
        if CANONICAL_CODE_PATTERN.fullmatch(raw_internal)
        else FALLBACK_ERROR_CODE
    )
    mapped = SENSITIVE_REASON_OVERRIDES.get(raw_internal)
    if mapped is None and raw_internal in PERSISTED_PHASES:
        # Canonical persisted outputs are valid inputs too.  Incident and
        # notification consumers may receive a reason that already crossed
        # this boundary, so normalization must be stable when repeated.
        mapped = raw_internal
    if not persisted_error_code_is_safe(mapped or ""):
        mapped = FALLBACK_ERROR_CODE

    requested_phase = str(phase or "").strip().lower()
    resolved_phase = (
        requested_phase
        if CANONICAL_CODE_PATTERN.fullmatch(requested_phase)
        else ""
    )
    if not resolved_phase:
        resolved_phase = AUTO_LOGIN_REASON_PHASES.get(raw_internal) or PERSISTED_PHASES.get(mapped, "unknown")

    if mapped == "instagram_credentials_rejected":
        operator_message = (
            "Instagram rejected the active credential revision for this account."
        )
        recommended_action = (
            "Submit a corrected credential through the secure writer, then explicitly resume Auto Login."
        )
        retryable = False
    elif mapped == "assigned_instagram_app_instance_mismatch":
        operator_message = (
            "L’application Instagram assignée n’a pas été ouverte. "
            "Aucune donnée de connexion n’a été saisie."
        )
        recommended_action = "Verify the canonical assignment and Android package before retrying."
        retryable = False
    elif mapped == "auto_login_app_instance_binding_missing":
        operator_message = "Auto Login stopped because its immutable app-instance binding was missing."
        recommended_action = "Create a new request only after the canonical assignment is complete."
        retryable = False
    elif mapped == "credential_input_field_unavailable":
        operator_message = (
            "The secure credential input field could not be located on the "
            "Instagram login surface."
        )
        recommended_action = "Review the Instagram login form detector before retrying."
        retryable = bool(correction_deployed)
    elif mapped == FALLBACK_ERROR_CODE:
        operator_message = (
            "The Instagram login could not be completed automatically and "
            "requires human intervention."
        )
        recommended_action = "Review the server-only worker failure details before retrying."
        retryable = False
    else:
        operator_message = f"Auto Login stopped during {resolved_phase} ({mapped})."
        recommended_action = "Correct the reported Auto Login condition before retrying."
        retryable = mapped not in {
            "credentials_invalid",
            "credential_material_invalid",
            "active_instagram_account_mismatch",
            "expected_identity_not_proven",
            "identity_guard_failed",
            "verification_code_rejected",
        }

    return AutoLoginFailureContract(
        internal_worker_reason=internal or FALLBACK_ERROR_CODE,
        persisted_error_code=mapped,
        phase=resolved_phase,
        operator_message=operator_message,
        client_safe_message=CLIENT_SAFE_MESSAGE,
        retryable=retryable,
        severity="error",
        recommended_action=recommended_action,
    )
