"""Immutable one-shot authorization between final grid proof and post tap."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import secrets
import time
from typing import Any


VERSION = "PostOpenIntentV2"
_CONSUMED_NONCES: set[str] = set()


def _norm(value: object) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _safe_bounds(bounds: dict[str, int], viewport: dict[str, int]) -> bool:
    try:
        left, top, right, bottom = (
            int(bounds[key]) for key in ("left", "top", "right", "bottom")
        )
        width = int(viewport["width"])
        height = int(viewport["height"])
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        width > 0
        and height > 0
        and 0 <= left < right <= width
        and 0 <= top < bottom <= height
    )


@dataclass(frozen=True)
class PostOpenIntentV2:
    version: str
    account_id: str
    run_id: str
    request_id: str
    action_id: str
    attempt_id: int
    business_session_id: str
    control_id: str
    worker_sha: str
    candidate_username: str
    target_id: str
    target_username: str
    package: str
    activity: str
    source_branch: str
    absolute_row: int
    absolute_column: int
    bounds: dict[str, int]
    coordinate_frame: dict[str, Any]
    viewport: dict[str, int]
    insets: dict[str, int]
    navigation_generation: int
    scroll_generation: int
    ui_generation: int
    xml_hash: str
    fingerprint: str
    post_grid_classification: str
    candidate_bound_provenance: str
    created_at_monotonic: float
    expires_at_monotonic: float
    ttl_ms: float
    one_shot_nonce: str
    consumed: bool = False
    invalidated: bool = False
    invalidation_reason: str = ""

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def create_post_open_intent_v2(
    *,
    binding: dict[str, Any],
    candidate_username: str,
    target_id: str,
    target_username: str,
    package: str,
    activity: str,
    source_branch: str,
    absolute_row: int,
    absolute_column: int,
    bounds: dict[str, int],
    coordinate_frame: dict[str, Any],
    viewport: dict[str, int],
    insets: dict[str, int],
    navigation_generation: int,
    scroll_generation: int,
    ui_generation: int,
    xml_hash: str,
    fingerprint: str,
    post_grid_classification: str,
    candidate_bound_provenance: str,
    ttl_ms: float = 1250.0,
    created_at_monotonic: float | None = None,
) -> PostOpenIntentV2 | None:
    branch = str(source_branch or "").upper()
    worker_sha = str(binding.get("worker_sha") or "").strip().lower()
    required = (
        binding.get("account_id"), binding.get("run_id"), binding.get("request_id"),
        binding.get("action_id"), binding.get("business_session_id"),
        binding.get("control_id"), worker_sha, candidate_username, package,
        activity, xml_hash, fingerprint, candidate_bound_provenance,
    )
    if (
        branch not in {"SAFE", "GOLDEN"}
        or not all(str(value or "").strip() for value in required)
        or len(worker_sha) != 40
        or not _safe_bounds(bounds, viewport)
        or int(absolute_row) < 0
        or int(absolute_column) < 0
    ):
        return None
    created = (
        float(created_at_monotonic)
        if created_at_monotonic is not None
        else time.monotonic()
    )
    ttl = max(1.0, float(ttl_ms))
    return PostOpenIntentV2(
        version=VERSION,
        account_id=str(binding.get("account_id") or ""),
        run_id=str(binding.get("run_id") or ""),
        request_id=str(binding.get("request_id") or ""),
        action_id=str(binding.get("action_id") or ""),
        attempt_id=int(binding.get("attempt_id") or 0),
        business_session_id=str(binding.get("business_session_id") or ""),
        control_id=str(binding.get("control_id") or ""),
        worker_sha=worker_sha,
        candidate_username=_norm(candidate_username),
        target_id=str(target_id or ""),
        target_username=_norm(target_username),
        package=str(package or ""),
        activity=str(activity or ""),
        source_branch=branch,
        absolute_row=int(absolute_row),
        absolute_column=int(absolute_column),
        bounds={key: int(bounds[key]) for key in ("left", "top", "right", "bottom")},
        coordinate_frame=dict(coordinate_frame or {}),
        viewport={"width": int(viewport["width"]), "height": int(viewport["height"])},
        insets={key: int(value or 0) for key, value in dict(insets or {}).items()},
        navigation_generation=int(navigation_generation),
        scroll_generation=int(scroll_generation),
        ui_generation=int(ui_generation),
        xml_hash=str(xml_hash),
        fingerprint=str(fingerprint),
        post_grid_classification=str(post_grid_classification or ""),
        candidate_bound_provenance=str(candidate_bound_provenance or ""),
        created_at_monotonic=created,
        expires_at_monotonic=created + (ttl / 1000.0),
        ttl_ms=ttl,
        one_shot_nonce=secrets.token_hex(16),
    )


def consume_post_open_intent_v2(
    intent: PostOpenIntentV2 | None,
    *,
    binding: dict[str, Any],
    candidate_username: str,
    package: str,
    activity: str,
    viewport: dict[str, int],
    navigation_generation: int,
    scroll_generation: int,
    ui_generation: int,
    now_monotonic: float | None = None,
) -> tuple[PostOpenIntentV2 | None, float, str]:
    if intent is None or intent.version != VERSION:
        return None, 0.0, "post_open_intent_missing_or_version_invalid"
    now = float(now_monotonic) if now_monotonic is not None else time.monotonic()
    age_ms = max(0.0, (now - intent.created_at_monotonic) * 1000.0)
    reason = ""
    if intent.one_shot_nonce in _CONSUMED_NONCES or intent.consumed:
        reason = "post_open_intent_already_consumed"
    elif intent.invalidated:
        reason = intent.invalidation_reason or "post_open_intent_invalidated"
    elif now > intent.expires_at_monotonic:
        reason = "post_open_intent_expired"
    else:
        exact_fields = (
            (intent.account_id, binding.get("account_id"), "account"),
            (intent.run_id, binding.get("run_id"), "run"),
            (intent.request_id, binding.get("request_id"), "request"),
            (intent.action_id, binding.get("action_id"), "action"),
            (intent.attempt_id, int(binding.get("attempt_id") or 0), "attempt"),
            (intent.business_session_id, binding.get("business_session_id"), "business_session"),
            (intent.control_id, binding.get("control_id"), "control"),
            (intent.worker_sha, str(binding.get("worker_sha") or "").lower(), "worker_sha"),
            (intent.candidate_username, _norm(candidate_username), "candidate"),
            (intent.package, str(package or ""), "package"),
            (intent.activity, str(activity or ""), "activity"),
            (intent.navigation_generation, int(navigation_generation), "navigation_generation"),
            (intent.scroll_generation, int(scroll_generation), "scroll_generation"),
            (intent.ui_generation, int(ui_generation), "ui_generation"),
        )
        for got, expected, label in exact_fields:
            if got != expected:
                reason = f"post_open_intent_{label}_mismatch"
                break
    if not reason and (
        intent.viewport != {
            "width": int(viewport.get("width") or 0),
            "height": int(viewport.get("height") or 0),
        }
        or not _safe_bounds(intent.bounds, viewport)
    ):
        reason = "post_open_intent_viewport_or_bounds_stale"
    if reason:
        return None, age_ms, reason
    _CONSUMED_NONCES.add(intent.one_shot_nonce)
    return intent, age_ms, ""


def _reset_consumed_nonces_for_tests() -> None:
    _CONSUMED_NONCES.clear()
