"""Global account identity preflight for account-bound Instagram flows.

Before a Supabase account run performs business actions, the logged-in Instagram
account in the active clone must match the expected account username.

Roadmap note: today this is a username-only guard, so any mismatch safe-stops.
When a stable Instagram account identifier is available, the structured result is
ready to distinguish a wrong active account from a possible username rename.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any

import uiautomator2 as u2

from logs import log
from own_profile_navigation import open_own_profile_from_bottom_nav

ACCOUNT_IDENTITY_MISMATCH_REASON = "active_instagram_account_mismatch"
POSSIBLE_USERNAME_RENAME_REASON = "possible_username_rename_detected"

_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


@dataclass(frozen=True)
class AccountIdentityCheckResult:
    ok: bool
    expected_account_username: str
    actual_logged_in_username: str = ""
    expected_instagram_user_id: str = ""
    actual_instagram_user_id: str = ""
    failure_reason: str = ""
    identity_evidence: str = "username_only"
    rename_disambiguation_status: str = "stable_instagram_user_id_not_available"
    verification_method: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_account_username(username: str) -> str:
    return str(username or "").strip().lstrip("@").lower()


def _dump_hierarchy(d: u2.Device) -> str:
    try:
        try:
            return str(d.dump_hierarchy(compressed=False) or "")
        except TypeError:
            return str(d.dump_hierarchy() or "")
    except Exception:
        return ""


def _parse_xml_root(hierarchy_xml: str) -> ET.Element | None:
    text = str(hierarchy_xml or "").strip()
    if not text:
        return None
    try:
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            return ET.fromstring(f"<wrap>{text}</wrap>")
    except Exception:
        return None


def _looks_like_handle(raw: str) -> bool:
    value = str(raw or "").strip().lstrip("@")
    return bool(value and _HANDLE_RE.match(value))


def _extract_own_profile_username_from_hierarchy(hierarchy_xml: str) -> tuple[str, str, dict[str, Any]]:
    root = _parse_xml_root(hierarchy_xml)
    meta: dict[str, Any] = {
        "action_bar_title": "",
        "candidate_texts": [],
        "hierarchy_xml_len": len(str(hierarchy_xml or "")),
    }
    if root is None:
        return "", "hierarchy_xml_parse_failed", meta

    candidates: list[tuple[int, str, str]] = []
    for el in root.iter():
        rid = str(el.get("resource-id") or "")
        rid_l = rid.lower()
        text = str(el.get("text") or el.get("content-desc") or "").strip().lstrip("@")
        if not text or not _looks_like_handle(text):
            continue
        if "action_bar_title" in rid_l:
            meta["action_bar_title"] = text
            candidates.append((0, text, "action_bar_title"))
        elif "profile_header" in rid_l and "username" in rid_l:
            candidates.append((1, text, "profile_header_username"))
        elif "username" in rid_l and "row_search" not in rid_l:
            candidates.append((2, text, "username_resource_id"))
        elif "title" in rid_l:
            candidates.append((3, text, "title_resource_id"))
    if candidates:
        candidates.sort(key=lambda item: item[0])
        meta["candidate_texts"] = [
            {"username": value, "method": method, "rank": rank}
            for rank, value, method in candidates[:8]
        ]
        _, username, method = candidates[0]
        return username, method, meta
    return "", "own_profile_username_not_found", meta


def _publish_identity_mismatch_incident(
    result: AccountIdentityCheckResult,
    *,
    account_id: str | None,
    run_type: str | None,
    run_id: str | None,
    stage: str,
) -> None:
    """Persist identity mismatch as an observation only; never affect safe-stop."""
    try:
        import runtime_incidents

        safe_meta = {
            "action_bar_title": result.meta.get("action_bar_title"),
            "candidate_texts": result.meta.get("candidate_texts"),
            "hierarchy_xml_len": result.meta.get("hierarchy_xml_len"),
        }
        payload = runtime_incidents.build_identity_mismatch_incident(
            account_id=account_id,
            expected_username=result.expected_account_username,
            actual_username=result.actual_logged_in_username,
            run_id=run_id,
            run_type=run_type,
            stage=stage,
            expected_instagram_user_id=result.expected_instagram_user_id,
            actual_instagram_user_id=result.actual_instagram_user_id,
            verification_method=result.verification_method,
            identity_evidence=result.identity_evidence,
            rename_disambiguation_status=result.rename_disambiguation_status,
            future_possible_failure_reason=POSSIBLE_USERNAME_RENAME_REASON,
            metadata=safe_meta,
        )
        runtime_incidents.publish_account_incident(**payload)
    except Exception as exc:
        log(
            "warning",
            "identity_mismatch_incident_publish_failed",
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
            error=str(exc)[:500],
        )


def verify_active_instagram_account_matches_expected(
    d: u2.Device,
    *,
    expected_account_username: str,
    expected_instagram_user_id: str | None = None,
    account_id: str | None = None,
    run_type: str | None = None,
    run_id: str | None = None,
    stage: str = "account_identity_preflight",
) -> AccountIdentityCheckResult:
    """Open/inspect own profile and require an exact handle match."""
    expected_raw = str(expected_account_username or "").strip()
    expected = normalize_account_username(expected_raw)
    expected_stable_id = str(expected_instagram_user_id or "").strip()
    log(
        "info",
        "account_identity_check_started",
        account_id=account_id,
        expected_account_username=expected_raw,
        expected_instagram_user_id=expected_stable_id,
        run_type=run_type,
        run_id=run_id,
        stage=stage,
    )

    if not expected:
        result = AccountIdentityCheckResult(
            ok=False,
            expected_account_username=expected_raw,
            expected_instagram_user_id=expected_stable_id,
            failure_reason="expected_account_username_missing",
            verification_method="preflight_input_validation",
        )
        log(
            "error",
            "active_instagram_account_mismatch",
            account_id=account_id,
            expected_account_username=expected_raw,
            expected_instagram_user_id=expected_stable_id,
            actual_logged_in_username="",
            actual_instagram_user_id="",
            run_type=run_type,
            run_id=run_id,
            stage=stage,
            reason=result.failure_reason,
            verification_method=result.verification_method,
        )
        return result

    if not open_own_profile_from_bottom_nav(d):
        result = AccountIdentityCheckResult(
            ok=False,
            expected_account_username=expected_raw,
            expected_instagram_user_id=expected_stable_id,
            failure_reason="own_profile_open_failed",
            verification_method="open_own_profile_from_bottom_nav",
        )
        log(
            "error",
            "active_instagram_account_mismatch",
            account_id=account_id,
            expected_account_username=expected_raw,
            expected_instagram_user_id=expected_stable_id,
            actual_logged_in_username="",
            actual_instagram_user_id="",
            run_type=run_type,
            run_id=run_id,
            stage=stage,
            reason=result.failure_reason,
            verification_method=result.verification_method,
        )
        return result

    hierarchy = _dump_hierarchy(d)
    actual_raw, method, meta = _extract_own_profile_username_from_hierarchy(hierarchy)
    actual = normalize_account_username(actual_raw)
    if actual and actual == expected:
        result = AccountIdentityCheckResult(
            ok=True,
            expected_account_username=expected_raw,
            actual_logged_in_username=actual_raw,
            expected_instagram_user_id=expected_stable_id,
            identity_evidence="username_exact_match",
            rename_disambiguation_status=(
                "not_needed_username_matched"
                if not expected_stable_id
                else "stable_id_not_checked_username_matched"
            ),
            verification_method=f"own_profile_username_exact:{method}",
            meta=meta,
        )
        log(
            "info",
            "account_identity_check_ok",
            account_id=account_id,
            expected_account_username=expected_raw,
            expected_instagram_user_id=expected_stable_id,
            actual_logged_in_username=actual_raw,
            actual_instagram_user_id="",
            run_type=run_type,
            run_id=run_id,
            stage=stage,
            verification_method=result.verification_method,
        )
        return result

    reason = ACCOUNT_IDENTITY_MISMATCH_REASON if actual else "actual_logged_in_username_not_detected"
    result = AccountIdentityCheckResult(
        ok=False,
        expected_account_username=expected_raw,
        actual_logged_in_username=actual_raw,
        expected_instagram_user_id=expected_stable_id,
        failure_reason=reason,
        identity_evidence="username_mismatch_stable_id_unavailable",
        rename_disambiguation_status=(
            "stable_instagram_user_id_not_available"
            if not expected_stable_id
            else "expected_stable_id_available_but_actual_stable_id_not_detected"
        ),
        verification_method=f"own_profile_username_exact_mismatch:{method}",
        meta=meta,
    )
    log(
        "error",
        "active_instagram_account_mismatch",
        account_id=account_id,
        expected_account_username=expected_raw,
        expected_instagram_user_id=expected_stable_id,
        actual_logged_in_username=actual_raw,
        actual_instagram_user_id="",
        run_type=run_type,
        run_id=run_id,
        stage=stage,
        reason=ACCOUNT_IDENTITY_MISMATCH_REASON,
        failure_reason=result.failure_reason,
        identity_evidence=result.identity_evidence,
        rename_disambiguation_status=result.rename_disambiguation_status,
        future_possible_failure_reason=POSSIBLE_USERNAME_RENAME_REASON,
        verification_method=result.verification_method,
        meta=meta,
    )
    if result.failure_reason == ACCOUNT_IDENTITY_MISMATCH_REASON:
        _publish_identity_mismatch_incident(
            result,
            account_id=account_id,
            run_type=run_type,
            run_id=run_id,
            stage=stage,
        )
    return result
