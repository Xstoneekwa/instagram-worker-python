"""Account-global Instagram human-confirmation surface classifier.

The classifier is pure: callers pass an already-fresh accessibility hierarchy
and the current package/activity proof.  It never acquires device evidence and
never taps the challenge CTA.
"""

from __future__ import annotations

import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from logs import log


STABLE_REASON = "instagram_human_confirmation_required"
CHALLENGE_FAMILY = "instagram_human_confirmation"
INCIDENT_TYPE = "instagram_human_confirmation_required"
EXIT_CODE = 79


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(str(value or "")))
    text = text.replace("’", "'").replace("‘", "'").casefold()
    text = re.sub(r"[^a-z0-9@._']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _surface_values(hierarchy_xml: str) -> tuple[list[str], list[dict[str, str]]]:
    raw = str(hierarchy_xml or "").strip()
    if not raw:
        return [], []
    try:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            root = ET.fromstring(f"<wrap>{raw}</wrap>")
    except Exception:
        return [], []
    values: list[str] = []
    nodes: list[dict[str, str]] = []
    for element in root.iter():
        attrs = {str(key): str(value or "") for key, value in element.attrib.items()}
        nodes.append(attrs)
        for key in ("text", "content-desc", "hint"):
            value = html.unescape(attrs.get(key, "")).strip()
            if value and value not in values:
                values.append(value)
    return values, nodes


@dataclass(frozen=True)
class HumanConfirmationClassification:
    detected: bool
    reason_code: str = ""
    challenge_family: str = ""
    title_raw: str = ""
    detail_raw: str = ""
    account_username_raw: str = ""
    cta_raw: str = ""
    semantic_signals: tuple[str, ...] = ()
    structural_signals: tuple[str, ...] = ()
    confidence: float = 0.0
    detection_method: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["semantic_signals"] = list(self.semantic_signals)
        payload["structural_signals"] = list(self.structural_signals)
        return payload


def classify_instagram_human_confirmation(
    hierarchy_xml: str,
    *,
    package_name: str = "",
    activity_name: str = "",
) -> HumanConfirmationClassification:
    """Require semantic title/body proof plus an Instagram structural signal."""

    values, nodes = _surface_values(hierarchy_xml)
    normalized = [(_normalize(value), value) for value in values]
    title = next(
        (raw for norm, raw in normalized if "confirm you're human" in norm),
        "",
    )
    detail = next(
        (raw for norm, raw in normalized if "to use your account" in norm),
        "",
    )
    cta = next((raw for norm, raw in normalized if norm == "continue"), "")
    account_username = ""
    non_account_labels = {
        "instagram",
        "continue",
        "get support",
        "takes about 30 seconds",
    }
    for norm, raw in normalized:
        if raw in {title, detail, cta}:
            continue
        if norm in non_account_labels:
            continue
        if re.fullmatch(r"[A-Za-z0-9._]{1,30}", raw.strip().lstrip("@")):
            account_username = raw.strip().lstrip("@")
            break

    package_blob = _normalize(
        " ".join(
            [package_name, activity_name]
            + [attrs.get("package", "") for attrs in nodes]
        )
    )
    instagram_surface = "instagram" in package_blob or any(
        norm == "instagram" for norm, _raw in normalized
    )
    challenge_activity = "challengeactivity" in _normalize(activity_name)
    continue_clickable = any(
        _normalize(attrs.get("text")) == "continue"
        and (
            attrs.get("clickable", "").casefold() == "true"
            or "button" in attrs.get("class", "").casefold()
        )
        for attrs in nodes
    )

    semantic: list[str] = []
    structural: list[str] = []
    if title:
        semantic.append("human_confirmation_title")
    if detail:
        semantic.append("account_use_detail")
    if cta:
        semantic.append("continue_cta")
    if instagram_surface:
        structural.append("instagram_surface")
    if challenge_activity:
        structural.append("challenge_activity")
    if continue_clickable:
        structural.append("clickable_continue")

    detected = bool(
        title
        and detail
        and cta
        and instagram_surface
        and (continue_clickable or challenge_activity)
    )
    confidence = 0.99 if detected and continue_clickable and challenge_activity else 0.97 if detected else 0.0
    return HumanConfirmationClassification(
        detected=detected,
        reason_code=STABLE_REASON if detected else "",
        challenge_family=CHALLENGE_FAMILY if detected else "",
        title_raw=title,
        detail_raw=detail,
        account_username_raw=account_username,
        cta_raw=cta,
        semantic_signals=tuple(semantic),
        structural_signals=tuple(structural),
        confidence=confidence,
        detection_method="fresh_xml_semantic_structural_v1" if detected else "",
    )


class InstagramHumanConfirmationRequired(RuntimeError):
    def __init__(self, *, device: Any, summary: dict[str, Any]):
        super().__init__(STABLE_REASON)
        self.device = device
        self.summary = dict(summary)


def guard_instagram_human_confirmation(
    d: Any,
    *,
    hierarchy_xml: str,
    package_name: str = "",
    activity_name: str = "",
    phase: str,
    preceding_action: str,
    runtime_context: Any = None,
) -> HumanConfirmationClassification:
    """Raise an account-global terminal without clicking the Continue CTA."""

    result = classify_instagram_human_confirmation(
        hierarchy_xml,
        package_name=package_name,
        activity_name=activity_name,
    )
    if not result.detected:
        return result

    ctx = runtime_context
    account_id = str(getattr(ctx, "account_id", "") or "")
    account_username = str(getattr(ctx, "account_username", "") or result.account_username_raw)
    run_id = str(getattr(ctx, "run_id", "") or "")
    request_id = str(getattr(ctx, "request_id", "") or "")
    detected_at = datetime.now(timezone.utc).isoformat()
    metadata = {
        **result.to_dict(),
        "source": "instagram_ui",
        "phase": phase,
        "preceding_action": preceding_action,
        "operator_action_required": True,
        "auto_restart_allowed": False,
        "account_pause_required": True,
        "safety_scope": "account_global",
        "fail_closed": True,
        "business_actions_allowed": False,
        "automatic_challenge_action_allowed": False,
        "fresh_identity_required_on_resume": True,
        "actions_completed": dict(getattr(ctx, "actions_completed", {}) or {}),
        "quotas_remaining": dict(getattr(ctx, "quotas_remaining", {}) or {}),
        "detected_at": detected_at,
    }
    incident_id = None
    dedupe_key = f"account:{account_id or 'unknown'}:instagram_human_confirmation"
    try:
        import runtime_incidents

        payload = runtime_incidents.build_instagram_human_confirmation_incident(
            account_id=account_id or None,
            account_username=account_username,
            run_id=run_id or None,
            metadata=metadata,
        )
        published = runtime_incidents.publish_account_incident(**payload)
        if isinstance(published, dict):
            incident_id = published.get("incident_id") or published.get("id")
    except Exception as exc:
        log(
            "warning",
            "instagram_human_confirmation_incident_publish_failed",
            account_id=account_id or None,
            run_id=run_id or None,
            error_type=type(exc).__name__,
        )

    summary = {
        "reason": STABLE_REASON,
        "reason_code": STABLE_REASON,
        "failure_reason": STABLE_REASON,
        "root_failure_code": STABLE_REASON,
        "incident_type": INCIDENT_TYPE,
        "account_id": account_id,
        "account_username": account_username,
        "run_id": run_id,
        "request_id": request_id,
        "phase": phase,
        "preceding_action": preceding_action,
        "incident_id": incident_id,
        "incident_dedupe_key": dedupe_key,
        **metadata,
    }
    log(
        "error",
        "instagram_human_confirmation_required_detected",
        account_id=account_id or None,
        account_username=account_username or None,
        run_id=run_id or None,
        phase=phase,
        preceding_action=preceding_action,
        reason=STABLE_REASON,
        auto_restart_allowed=False,
        continue_tapped=False,
        no_further_business_actions=True,
    )
    raise InstagramHumanConfirmationRequired(device=d, summary=summary)
