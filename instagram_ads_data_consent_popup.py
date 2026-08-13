"""Shared fail-closed guard for Instagram's ads-data consent modal.

The modal requires a human privacy choice.  Runtime code may detect and report
it, but must never choose ``Get started`` (or any later consent option) for the
operator.  Login identity verification may still use an exact username that is
readable behind the modal; business actions are always interrupted before a
gesture can be dispatched.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from logs import log
from runtime_incidents import publish_account_incident

POPUP_TYPE = "instagram_ads_data_consent_popup"
IDENTITY_PENDING_REASON = "instagram_ads_data_consent_popup_identity_pending"
BUSINESS_PAUSED_REASON = "instagram_ads_data_consent_popup_business_action_paused"
INCIDENT_TYPE = "instagram_ads_data_consent_popup_requires_operator"
EXIT_CODE = 77
TITLE = "Choose if we process your data for ads"
CTA = "Get started"


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("`", "'").lower()
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _xml_signals(hierarchy_xml: str) -> tuple[str, set[str], int]:
    texts: list[str] = []
    packages: set[str] = set()
    clickable = 0
    try:
        root = ET.fromstring(str(hierarchy_xml or ""))
        for node in root.iter():
            attrs = node.attrib
            for key in ("text", "content-desc", "hint"):
                if str(attrs.get(key) or "").strip():
                    texts.append(str(attrs[key]))
            package = str(attrs.get("package") or "").strip()
            if package:
                packages.add(package)
            if str(attrs.get("clickable") or "").lower() == "true" or "button" in str(
                attrs.get("class") or ""
            ).lower():
                clickable += 1
    except Exception:
        texts.extend(
            match.group(1)
            for match in re.finditer(r'(?:text|content-desc|hint)="([^"]*)"', str(hierarchy_xml or ""))
        )
        packages.update(re.findall(r'package="([^"]+)"', str(hierarchy_xml or "")))
    return _normalize(" ".join(texts)), packages, clickable


@dataclass(frozen=True)
class AdsDataConsentPopupClassification:
    detected: bool
    title_detected: bool
    body_detected: bool
    cta_detected: bool
    instagram_surface: bool
    modal_structure: bool
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_instagram_ads_data_consent_popup(
    hierarchy_xml: str,
    *,
    visual_text: str = "",
    package_name: str = "",
) -> AdsDataConsentPopupClassification:
    xml_text, packages, clickable = _xml_signals(hierarchy_xml)
    normalized = _normalize(f"{xml_text} {visual_text}")
    package_blob = " ".join(sorted(packages | {str(package_name or "")})).lower()
    title = "choose if we process your data for ads" in normalized
    body = (
        "as part of laws in your region" in normalized
        and "whether you consent" in normalized
        and "personal data" in normalized
    )
    cta = bool(re.search(r"(?:^| )get started(?: |$)", normalized))
    instagram = "instagram" in package_blob or "com.instagram" in package_blob
    modal = clickable >= 1 or (title and cta)
    # Keep this privacy gate deliberately high-confidence: the observed title,
    # explanatory body and CTA must agree.  A generic Instagram modal with the
    # same CTA must never inherit this operator-only handling by accident.
    detected = bool(instagram and title and body and cta and modal)
    score = sum((3 if title else 0, 2 if body else 0, 2 if cta else 0, 1 if instagram else 0, 1 if modal else 0))
    return AdsDataConsentPopupClassification(
        detected=detected,
        title_detected=title,
        body_detected=body,
        cta_detected=cta,
        instagram_surface=instagram,
        modal_structure=modal,
        confidence=round(min(0.99, score / 9.0), 3) if detected else round(min(0.79, score / 9.0), 3),
    )


def publish_ads_data_consent_operator_alert(
    *,
    account_id: str,
    account_username: str,
    run_id: str | None,
    request_id: str | None = None,
    device_id: str | None = None,
    app_instance_id: str | None = None,
    clone: str | None = None,
    phase: str,
    preceding_action: str,
    identity_pending: bool,
) -> dict[str, Any]:
    """Publish one stable operator incident and matching dashboard action."""

    aid = str(account_id or "").strip()
    detected_at = datetime.now(timezone.utc).isoformat()
    metadata = {
        "popup_type": POPUP_TYPE,
        "popup_title": TITLE,
        "popup_cta": CTA,
        "detected_at": detected_at,
        "device_id": str(device_id or "").strip() or None,
        "app_instance_id": str(app_instance_id or "").strip() or None,
        "clone": str(clone or "").strip() or None,
        "request_id": str(request_id or "").strip() or None,
        "phase": str(phase or "unknown"),
        "preceding_action": str(preceding_action or "unknown"),
        "identity_pending_popup": bool(identity_pending),
        "session_authenticated": True,
        "operator_action_required": True,
        "automatic_cta_click_allowed": False,
        "recommended_action": (
            "Open Phone View, handle the Instagram ads-data choice manually, "
            "then resume identity verification."
        ),
    }
    incident = publish_account_incident(
        incident_type=INCIDENT_TYPE,
        dedupe_key=f"account:{aid}:instagram_ads_data_consent_popup",
        severity="warning",
        status="open",
        account_id=aid or None,
        account_username=str(account_username or "").strip() or None,
        run_id=str(run_id or "").strip() or None,
        device_id=str(device_id or "").strip() or None,
        source="instagram_ads_data_consent_popup_guard",
        reason=IDENTITY_PENDING_REASON if identity_pending else BUSINESS_PAUSED_REASON,
        action_required="Open Phone View and handle the Instagram ads-data consent popup manually.",
        safe_client_message=(
            'Instagram affiche « Choose if we process your data for ads ». '
            "Une action opérateur est requise avant de poursuivre."
        ),
        admin_message=(
            'Instagram affiche la popup « Choose if we process your data for ads ». '
            'Ouvrir la Phone View, traiter manuellement « Get started », puis reprendre la vérification.'
        ),
        metadata=metadata,
    )
    action: dict[str, Any] = {"published": False, "reason": "not_attempted"}
    if aid:
        try:
            from login_challenge_runtime import sync_login_challenge_dashboard_action

            action = sync_login_challenge_dashboard_action(
                account_id=aid,
                dashboard_action_type="review_login_challenge",
                run_id=str(run_id or "").strip() or None,
                screen_type=POPUP_TYPE,
                stage=str(phase or "unknown"),
                human_review_required=True,
                metadata=metadata,
            )
        except Exception as exc:
            log("warning", "ads_data_consent_dashboard_action_failed", error_type=type(exc).__name__)
    try:
        from incident_notifications import dispatch_account_incident_notifications

        notification = dispatch_account_incident_notifications(min_severity="warning", max_per_run=5)
    except Exception as exc:
        log("warning", "ads_data_consent_notification_dispatch_failed", error_type=type(exc).__name__)
        notification = {"dispatched": False, "reason": "dispatch_failed"}
    log(
        "warning",
        "instagram_ads_data_consent_popup_detected",
        account_id=aid or None,
        run_id=run_id,
        popup_type=POPUP_TYPE,
        phase=phase,
        preceding_action=preceding_action,
        automatic_cta_click_allowed=False,
    )
    return {
        "incident_id": (
            incident.get("incident_id") or incident.get("id")
            if isinstance(incident, dict)
            else None
        ),
        "dashboard_action_id": action.get("dashboard_action_id") if isinstance(action, dict) else None,
        "detected_at": detected_at,
        "incident": incident,
        "dashboard_action": action,
        "notification": notification,
    }


class InstagramAdsDataConsentPopupDetected(RuntimeError):
    def __init__(self, *, device: Any, summary: dict[str, Any]):
        super().__init__(BUSINESS_PAUSED_REASON)
        self.device = device
        self.summary = dict(summary)


def guard_instagram_ads_data_consent_popup(
    d: Any,
    *,
    hierarchy_xml: str,
    package_name: str,
    context: Any,
    phase: str,
    preceding_action: str,
    visual_text: str = "",
) -> AdsDataConsentPopupClassification:
    result = classify_instagram_ads_data_consent_popup(
        hierarchy_xml,
        visual_text=visual_text,
        package_name=package_name,
    )
    if not result.detected:
        return result
    alert = publish_ads_data_consent_operator_alert(
        account_id=str(getattr(context, "account_id", "") or ""),
        account_username=str(getattr(context, "account_username", "") or ""),
        run_id=str(getattr(context, "run_id", "") or "") or None,
        request_id=str(getattr(context, "request_id", "") or "") or None,
        device_id=str(getattr(context, "device_id", "") or "") or None,
        app_instance_id=str(getattr(context, "app_instance_id", "") or "") or None,
        clone=str(getattr(context, "clone", "") or "") or None,
        phase=phase,
        preceding_action=preceding_action,
        identity_pending=False,
    )
    summary = {
        "reason": BUSINESS_PAUSED_REASON,
        "failure_reason": BUSINESS_PAUSED_REASON,
        "root_failure_code": BUSINESS_PAUSED_REASON,
        "popup_type": POPUP_TYPE,
        "phase_status": "partial_resumable",
        "resume_recommended": True,
        "safe_next_step": "schedule_resume",
        "operator_action_required": True,
        "automatic_cta_click_allowed": False,
        "account_id": str(getattr(context, "account_id", "") or ""),
        "account_username": str(getattr(context, "account_username", "") or ""),
        "run_id": str(getattr(context, "run_id", "") or ""),
        "request_id": str(getattr(context, "request_id", "") or ""),
        "device_id": str(getattr(context, "device_id", "") or ""),
        "app_instance_id": str(getattr(context, "app_instance_id", "") or ""),
        "clone": str(getattr(context, "clone", "") or ""),
        "phase": phase,
        "preceding_action": preceding_action,
        **alert,
    }
    raise InstagramAdsDataConsentPopupDetected(device=d, summary=summary)
