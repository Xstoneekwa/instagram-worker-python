"""Fail-closed Instagram action-rate-limit popup guard.

The classifier is deliberately pure and accepts both accessibility XML and
optional text extracted from a fresh screenshot. Runtime handling never taps
the popup: it captures redacted evidence, applies the canonical database hold,
then raises a terminal exception before another business gesture can run.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
import supabase_client
from logs import log


STABLE_REASON = "instagram_action_rate_limit"
INCIDENT_TYPE = "instagram_account_restriction"
RISK_CLASS = "human_review_required"
EXIT_CODE = 76

_EVIDENCE_ROOT = Path(__file__).resolve().parent / "logs" / "restriction-evidence"
_SAFE_FRAGMENT_MARKERS = (
    "try again later",
    "reessayer plus tard",
    "we limit how often",
    "nous limitons la frequence",
    "protect our community",
    "proteger notre communaute",
    "tell us if you think",
    "si vous pensez que nous avons fait erreur",
    "let us know",
    "contactez-nous",
    "contactez nous",
    "ok",
)


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("`", "'").lower()
    text = re.sub(r"[^a-z0-9']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _xml_text_and_structure(hierarchy_xml: str) -> tuple[str, dict[str, Any]]:
    raw = str(hierarchy_xml or "")
    texts: list[str] = []
    classes: list[str] = []
    packages: list[str] = []
    button_count = 0
    try:
        root = ET.fromstring(raw)
        for node in root.iter():
            attrs = node.attrib
            for key in ("text", "content-desc", "hint"):
                value = str(attrs.get(key) or "").strip()
                if value:
                    texts.append(value)
            cls = str(attrs.get("class") or "").strip()
            pkg = str(attrs.get("package") or "").strip()
            if cls:
                classes.append(cls)
            if pkg:
                packages.append(pkg)
            if "button" in cls.lower() or str(attrs.get("clickable") or "").lower() == "true":
                button_count += 1
    except Exception:
        for match in re.finditer(r'(?:text|content-desc|hint)="([^"]*)"', raw):
            if match.group(1):
                texts.append(match.group(1))
        packages.extend(re.findall(r'package="([^"]+)"', raw))
        classes.extend(re.findall(r'class="([^"]+)"', raw))
    modal = any(
        marker in _normalize(cls)
        for cls in classes
        for marker in ("dialog", "alert", "modal")
    )
    return " ".join(texts), {
        "modal_structure": modal or button_count >= 2,
        "button_count": button_count,
        "packages": sorted(set(packages))[:8],
        "node_text_count": len(texts),
    }


@dataclass(frozen=True)
class RestrictionClassification:
    detected: bool
    language: str
    confidence: float
    score: float
    signals: tuple[str, ...]
    normalized_fragments: tuple[str, ...]
    stable_reason: str = STABLE_REASON
    incident_type: str = INCIDENT_TYPE
    risk_class: str = RISK_CLASS
    false_positive_guard: str = "multi_signal_instagram_modal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "language": self.language,
            "confidence": self.confidence,
            "score": self.score,
            "signals": list(self.signals),
            "normalized_fragments": list(self.normalized_fragments),
            "stable_reason": self.stable_reason,
            "incident_type": self.incident_type,
            "risk_class": self.risk_class,
            "false_positive_guard": self.false_positive_guard,
        }


def classify_instagram_action_rate_limit(
    hierarchy_xml: str = "",
    *,
    visual_text: str = "",
    package_name: str = "",
    modal_structure: bool | None = None,
) -> RestrictionClassification:
    """Classify English/French rate-limit dialogs from independent signals."""

    xml_text, structure = _xml_text_and_structure(hierarchy_xml)
    normalized = _normalize(" ".join((xml_text, visual_text)))
    package_blob = _normalize(" ".join([package_name, *structure["packages"]]))
    expected_pkg = _normalize(getattr(config, "INSTAGRAM_PACKAGE", ""))
    instagram_surface = bool(
        "instagram" in package_blob
        or (expected_pkg and expected_pkg in package_blob)
        or "com instagram" in package_blob
    )
    modal = bool(structure["modal_structure"] if modal_structure is None else modal_structure)

    en = {
        "title": "try again later" in normalized,
        "body_primary": "we limit how often you can do certain things" in normalized,
        "body_community": "protect our community" in normalized,
        "body_mistake": "tell us if you think that we've made a mistake" in normalized
        or "tell us if you think that we ve made a mistake" in normalized,
        "contact": "let us know" in normalized,
    }
    fr = {
        "title": "reessayer plus tard" in normalized,
        "body_primary": "nous limitons la frequence de certaines actions" in normalized,
        "body_community": "proteger notre communaute" in normalized,
        "body_mistake": "si vous pensez que nous avons fait erreur" in normalized,
        "contact": "contactez nous" in normalized,
    }
    en_hits = sum(bool(v) for v in en.values())
    fr_hits = sum(bool(v) for v in fr.values())
    language = "en" if en_hits > fr_hits else "fr" if fr_hits > en_hits else "unknown"
    lang_signals = en if language == "en" else fr if language == "fr" else {
        key: bool(en[key] or fr[key]) for key in en
    }
    ok_button = bool(re.search(r"(?:^| )ok(?: |$)", normalized))

    score = 0.0
    signals: list[str] = []
    weights = {
        "title": 3.0,
        "body_primary": 3.0,
        "body_community": 1.0,
        "body_mistake": 1.0,
        "contact": 1.0,
    }
    for name, matched in lang_signals.items():
        if matched:
            score += weights[name]
            signals.append(name)
    if ok_button:
        score += 0.5
        signals.append("ok_button")
    if modal:
        score += 1.0
        signals.append("modal_structure")
    if instagram_surface:
        score += 1.0
        signals.append("instagram_surface")

    title_and_corroboration = bool(
        lang_signals["title"]
        and instagram_surface
        and (
            lang_signals["body_primary"]
            or (modal and (lang_signals["contact"] or ok_button))
        )
    )
    body_without_title = bool(
        lang_signals["body_primary"]
        and instagram_surface
        and modal
        and (lang_signals["contact"] or ok_button or lang_signals["body_community"])
    )
    detected = bool(language in {"en", "fr"} and score >= 6.0 and (title_and_corroboration or body_without_title))
    confidence = round(min(0.99, score / 10.0), 3) if detected else round(min(0.79, score / 10.0), 3)

    fragments = tuple(
        marker
        for marker in _SAFE_FRAGMENT_MARKERS
        if marker in normalized
    )
    return RestrictionClassification(
        detected=detected,
        language=language,
        confidence=confidence,
        score=round(score, 2),
        signals=tuple(signals),
        normalized_fragments=fragments,
    )


@dataclass
class RestrictionRuntimeContext:
    account_id: str = ""
    account_username: str = ""
    run_id: str = ""
    request_id: str = ""
    device_id: str = ""
    app_instance_id: str = ""
    clone: str = ""
    worker_sha: str = ""
    release: str = ""
    phase: str = "unknown"
    preceding_action: str = "unknown"
    actions_completed: dict[str, int] = field(default_factory=dict)
    quotas_remaining: dict[str, int] = field(default_factory=dict)


_RUNTIME_CONTEXT = RestrictionRuntimeContext()


def configure_restriction_runtime_context(**values: Any) -> None:
    for key, value in values.items():
        if hasattr(_RUNTIME_CONTEXT, key) and value is not None:
            setattr(_RUNTIME_CONTEXT, key, value)


def get_restriction_runtime_context() -> RestrictionRuntimeContext:
    """Return the shared account/run binding used by account-global guards."""
    return _RUNTIME_CONTEXT


def validate_restriction_preflight_policy(
    policy: dict[str, Any] | None,
) -> tuple[bool, str, str | None]:
    """Require an explicit zero-business-action restriction preflight policy."""
    value = policy if isinstance(policy, dict) else {}
    if value.get("restriction_preflight_only") is not True:
        return False, "restriction_preflight_only_not_authorized", None
    incident_id = str(value.get("incident_id") or "").strip()
    if not incident_id:
        return False, "restriction_preflight_incident_id_missing", None
    phases = value.get("phases_to_run")
    if not isinstance(phases, dict):
        return False, "restriction_preflight_business_phases_missing", incident_id
    expected = ("welcome", "follow", "unfollow")
    if any(phases.get(phase) is not False for phase in expected):
        return False, "restriction_preflight_business_phase_enabled", incident_id
    if any(enabled is True for enabled in phases.values()):
        return False, "restriction_preflight_business_phase_enabled", incident_id
    return True, "restriction_preflight_authorized", incident_id


def classify_restriction_preflight_request(
    policy: dict[str, object] | None,
) -> tuple[bool, str]:
    """Interpret the flag by boolean value; key presence alone is never enough."""

    payload = policy if isinstance(policy, dict) else {}
    if "restriction_preflight_only" not in payload:
        return False, "restriction_preflight_not_requested"
    value = payload.get("restriction_preflight_only")
    if value is True:
        return True, "restriction_preflight_requested"
    if value is False:
        return False, "restriction_preflight_not_requested"
    return False, "restriction_preflight_only_contract_invalid"


def _safe_xml_snapshot(hierarchy_xml: str) -> str:
    """Retain popup proof and structure, blanking unrelated UI/account text."""
    raw = str(hierarchy_xml or "")
    try:
        root = ET.fromstring(raw)
        for node in root.iter():
            for key in ("text", "content-desc", "hint"):
                value = str(node.attrib.get(key) or "")
                normalized = _normalize(value)
                if value and not any(marker in normalized for marker in _SAFE_FRAGMENT_MARKERS):
                    node.attrib[key] = "[redacted]"
            for key in tuple(node.attrib):
                if key.lower() in {"password", "token", "secret", "cookie", "authorization"}:
                    node.attrib[key] = "[redacted]"
        return ET.tostring(root, encoding="unicode")
    except Exception:
        return json.dumps({"xml_redacted": True, "raw_length": len(raw)})


def _capture_evidence(d: Any, hierarchy_xml: str, classification: RestrictionClassification) -> dict[str, Any]:
    _EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    stem = f"instagram_action_rate_limit_{stamp}"
    screenshot_path = _EVIDENCE_ROOT / f"{stem}.png"
    xml_path = _EVIDENCE_ROOT / f"{stem}.redacted.xml"
    screenshot_captured = False
    try:
        d.screenshot(str(screenshot_path))
        screenshot_captured = screenshot_path.is_file()
    except Exception as exc:
        log("warning", "restriction_evidence_screenshot_failed", error=type(exc).__name__)
    try:
        xml_path.write_text(_safe_xml_snapshot(hierarchy_xml), encoding="utf-8")
        xml_captured = True
    except Exception as exc:
        xml_captured = False
        log("warning", "restriction_evidence_xml_failed", error=type(exc).__name__)
    evidence = {
        "screenshot_captured": screenshot_captured,
        "screenshot_basename": screenshot_path.name if screenshot_captured else None,
        "xml_snapshot_captured": xml_captured,
        "xml_snapshot_basename": xml_path.name if xml_captured else None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "classification": classification.to_dict(),
    }
    log("info", "restriction_evidence_captured", **evidence)
    return evidence


class InstagramActionRestrictionDetected(RuntimeError):
    def __init__(self, *, device: Any, summary: dict[str, Any]):
        super().__init__(STABLE_REASON)
        self.device = device
        self.summary = dict(summary)
        self.run_id = str(summary.get("run_id") or "")


def _dump_hierarchy(d: Any) -> str:
    try:
        try:
            return str(d.dump_hierarchy(compressed=False) or "")
        except TypeError:
            return str(d.dump_hierarchy() or "")
    except Exception:
        return ""


def _current_package(d: Any) -> str:
    try:
        current = d.app_current() or {}
        return str(current.get("package") or "") if isinstance(current, dict) else ""
    except Exception:
        return ""


def guard_instagram_action_rate_limit(
    d: Any,
    *,
    phase: str,
    preceding_action: str,
    visual_text: str = "",
    hierarchy_xml: str | None = None,
) -> RestrictionClassification:
    """Raise after atomically pausing/publishing a confirmed restriction."""
    xml = _dump_hierarchy(d) if hierarchy_xml is None else str(hierarchy_xml or "")
    # Human confirmation is an account-global boundary.  Reuse this exact XML
    # and package observation; the classifier never clicks Continue.
    from instagram_human_confirmation_challenge import guard_instagram_human_confirmation

    current_package = _current_package(d)
    guard_instagram_human_confirmation(
        d,
        hierarchy_xml=xml,
        package_name=current_package,
        activity_name="",
        phase=phase,
        preceding_action=preceding_action,
        runtime_context=_RUNTIME_CONTEXT,
    )

    # Privacy consent is a global interaction boundary, not an action-rate
    # restriction.  Check it first so no caller can tap through the overlay or
    # count a business receipt.  The shared detector deliberately never clicks
    # the consent CTA.
    from instagram_ads_data_consent_popup import guard_instagram_ads_data_consent_popup

    # Both classifiers run synchronously inside this exact guard invocation.
    # Nothing between them navigates or mutates the device, so one fresh
    # package read is sufficient and remains bounded to this verification
    # boundary.  A later guard invocation always performs a new read.
    guard_instagram_ads_data_consent_popup(
        d,
        hierarchy_xml=xml,
        package_name=current_package,
        context=_RUNTIME_CONTEXT,
        phase=phase,
        preceding_action=preceding_action,
        visual_text=visual_text,
    )
    result = classify_instagram_action_rate_limit(
        xml,
        visual_text=visual_text,
        package_name=current_package,
    )
    if not result.detected:
        return result

    configure_restriction_runtime_context(phase=phase, preceding_action=preceding_action)
    ctx = _RUNTIME_CONTEXT
    detected_at = datetime.now(timezone.utc).isoformat()
    log(
        "error",
        "instagram_action_rate_limit_detected",
        stable_reason=STABLE_REASON,
        incident_type=INCIDENT_TYPE,
        phase=phase,
        preceding_action=preceding_action,
        language=result.language,
        confidence=result.confidence,
        auto_restart_allowed=False,
        account_pause_required=True,
    )
    log(
        "info",
        "restriction_popup_language_detected",
        language=result.language,
        confidence=result.confidence,
        signals=list(result.signals),
    )
    evidence = _capture_evidence(d, xml, result)
    safe_metadata = {
        "risk_class": RISK_CLASS,
        "blocking_campaign": True,
        "operator_review_required": True,
        "auto_restart_allowed": False,
        "account_pause_required": True,
        "phase": phase,
        "preceding_action": preceding_action,
        "language": result.language,
        "confidence": result.confidence,
        "classifier_score": result.score,
        "classifier_signals": list(result.signals),
        "normalized_fragments": list(result.normalized_fragments),
        "request_id": ctx.request_id or None,
        "device_id": ctx.device_id or None,
        "app_instance_id": ctx.app_instance_id or None,
        "clone": ctx.clone or None,
        "worker_sha": ctx.worker_sha or os.getenv("WORKER_GIT_SHA") or None,
        "release": ctx.release or os.getenv("WORKER_RELEASE") or None,
        "actions_completed": dict(ctx.actions_completed),
        "quotas_remaining": dict(ctx.quotas_remaining),
        "evidence": evidence,
        "detected_at": detected_at,
        "button_policy": "never_click_contact;ok_not_required_for_terminalization",
        "physical_preflight_required": True,
    }
    apply_result: dict[str, Any]
    try:
        apply_result = supabase_client.apply_instagram_action_restriction(
            {
                "account_id": ctx.account_id,
                "account_username": ctx.account_username,
                "run_id": ctx.run_id or None,
                "request_id": ctx.request_id or None,
                "stable_reason": STABLE_REASON,
                "incident_type": INCIDENT_TYPE,
                "metadata_safe": safe_metadata,
            }
        )
    except Exception as exc:
        apply_result = {"ok": False, "reason": "restriction_apply_failed", "error": type(exc).__name__}
        log("error", "restriction_account_pause_apply_failed", error=type(exc).__name__)
    incident_id = str(apply_result.get("incident_id") or "") or None
    log(
        "info" if apply_result.get("ok") else "error",
        "restriction_account_pause_applied",
        account_id=ctx.account_id or None,
        incident_id=incident_id,
        pause_status=apply_result.get("hold_status"),
        applied=bool(apply_result.get("ok")),
    )
    log("info", "restriction_incident_created", incident_id=incident_id, deduplicated=bool(apply_result.get("deduplicated")))
    log("info", "restriction_notifications_enqueued", incident_id=incident_id, channels=["slack", "discord"])
    log("error", "restriction_session_stopped", reason=STABLE_REASON, phase=phase, no_further_business_actions=True)

    summary = {
        "reason": STABLE_REASON,
        "reason_code": STABLE_REASON,
        "failure_reason": STABLE_REASON,
        "root_failure_code": STABLE_REASON,
        "incident_type": INCIDENT_TYPE,
        "risk_class": RISK_CLASS,
        "account_id": ctx.account_id,
        "account_username": ctx.account_username,
        "run_id": ctx.run_id,
        "request_id": ctx.request_id,
        "device_id": ctx.device_id,
        "app_instance_id": ctx.app_instance_id,
        "phase": phase,
        "preceding_action": preceding_action,
        "language": result.language,
        "confidence": result.confidence,
        "blocking_campaign": True,
        "operator_review_required": True,
        "auto_restart_allowed": False,
        "account_pause_required": True,
        "physical_preflight_required": True,
        "incident_id": incident_id,
        "incident_dedupe_key": str(apply_result.get("dedupe_key") or "") or None,
        "detected_at": detected_at,
    }
    raise InstagramActionRestrictionDetected(device=d, summary=summary)
