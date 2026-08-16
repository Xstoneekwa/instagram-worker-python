"""Generation-scoped proof storage for Instagram Followers surfaces.

The registry deliberately stores evidence by runtime scope instead of exposing a
process-wide last XML value.  Navigation certification is fail-closed: callers
must provide fresh evidence after a screen-changing intent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from contextvars import ContextVar
import hashlib
import threading
import time
from typing import Literal


ProofReadMode = Literal["same_surface_read", "certify_after_navigation"]
DEFAULT_SAME_SURFACE_MAX_AGE_MS = 15000.0


@dataclass(frozen=True)
class FollowersSurfaceScope:
    device_serial: str
    app_instance_id: str
    account_id: str
    target_id: str
    surface_id: str


@dataclass(frozen=True)
class FollowersSurfaceGenerations:
    navigation_generation: int
    scroll_generation: int


@dataclass(frozen=True)
class FollowersSurfaceProof:
    scope: FollowersSurfaceScope
    generations: FollowersSurfaceGenerations
    hierarchy_xml: str
    hierarchy_fingerprint: str
    xml_path: str
    captured_at_monotonic: float
    source: str


@dataclass(frozen=True)
class FollowersRowProof:
    """Exact row identity and geometry bound to one immutable viewport."""

    viewport_proof_id: str
    username: str
    normalized_username: str
    bounds: tuple[int, int, int, int]
    row_center: tuple[int, int]
    resource_identity: str
    row_fingerprint: str
    generations: FollowersSurfaceGenerations


@dataclass(frozen=True)
class FollowersViewportProof:
    """Actionable viewport evidence; old instances are comparison-only."""

    proof_id: str
    scope: FollowersSurfaceScope
    generations: FollowersSurfaceGenerations
    hierarchy_fingerprint: str
    package_name: str
    activity_name: str
    captured_at_monotonic: float
    source: str
    rows: tuple[FollowersRowProof, ...]
    actionable: bool = True
    comparison_only_reason: str = ""


@dataclass(frozen=True)
class RowActionToken:
    """Single-use permission for one exact username/row in one viewport."""

    token_id: str
    viewport_proof_id: str
    scope: FollowersSurfaceScope
    generations: FollowersSurfaceGenerations
    expected_username: str
    row_username: str
    row_bounds: tuple[int, int, int, int]
    resource_identity: str
    row_fingerprint: str
    row_center: tuple[int, int]
    issued_at_monotonic: float


_lock = threading.RLock()
_generations: dict[FollowersSurfaceScope, FollowersSurfaceGenerations] = {}
_proofs: dict[FollowersSurfaceScope, FollowersSurfaceProof] = {}
_viewports: dict[FollowersSurfaceScope, FollowersViewportProof] = {}
_comparison_viewports: dict[FollowersSurfaceScope, FollowersViewportProof] = {}
_row_action_tokens: dict[str, RowActionToken] = {}
_current_proof: ContextVar[FollowersSurfaceProof | None] = ContextVar(
    "followers_surface_current_proof",
    default=None,
)


def current_generations(scope: FollowersSurfaceScope) -> FollowersSurfaceGenerations:
    with _lock:
        return _generations.get(scope, FollowersSurfaceGenerations(0, 0))


def capture(
    scope: FollowersSurfaceScope,
    hierarchy_xml: str,
    *,
    xml_path: str = "",
    source: str = "fresh_xml",
    captured_at_monotonic: float | None = None,
) -> FollowersSurfaceProof | None:
    xml = str(hierarchy_xml or "").strip()
    if not xml:
        return None
    with _lock:
        proof = FollowersSurfaceProof(
            scope=scope,
            generations=current_generations(scope),
            hierarchy_xml=xml,
            hierarchy_fingerprint=hashlib.sha256(xml.encode("utf-8")).hexdigest(),
            xml_path=str(xml_path or ""),
            captured_at_monotonic=(
                time.monotonic()
                if captured_at_monotonic is None
                else float(captured_at_monotonic)
            ),
            source=str(source or "fresh_xml"),
        )
        _proofs[scope] = proof
        _current_proof.set(proof)
        return proof


def _normalized_username(value: str) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def _row_bounds(row: dict) -> tuple[int, int, int, int] | None:
    raw = row.get("bounds") or row.get("row_bounds") or {}
    try:
        left, top, right, bottom = (
            int(raw["left"]), int(raw["top"]), int(raw["right"]), int(raw["bottom"])
        )
    except (KeyError, TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def capture_viewport(
    scope: FollowersSurfaceScope,
    hierarchy_xml: str,
    rows: list[dict],
    *,
    package_name: str = "",
    activity_name: str = "",
    source: str = "fresh_xml",
    captured_at_monotonic: float | None = None,
) -> FollowersViewportProof | None:
    """Create the only actionable row geometry for the current generation."""

    xml = str(hierarchy_xml or "").strip()
    if not xml:
        return None
    captured_at = time.monotonic() if captured_at_monotonic is None else float(captured_at_monotonic)
    hierarchy_fingerprint = hashlib.sha256(xml.encode("utf-8")).hexdigest()
    generations = current_generations(scope)
    proof_id = hashlib.sha256(
        f"{scope!r}|{generations!r}|{hierarchy_fingerprint}|{captured_at:.9f}".encode("utf-8")
    ).hexdigest()
    row_proofs: list[FollowersRowProof] = []
    for row in rows:
        username = str(row.get("username") or "").strip().lstrip("@")
        normalized = _normalized_username(username)
        bounds = _row_bounds(row)
        if not normalized or bounds is None:
            continue
        center_raw = row.get("row_center") or ()
        try:
            center = (int(center_raw[0]), int(center_raw[1]))
        except (IndexError, TypeError, ValueError):
            center = ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)
        resource_identity = str(row.get("resource_id") or row.get("extraction_source") or "")
        row_fingerprint = hashlib.sha256(
            f"{proof_id}|{normalized}|{bounds!r}|{resource_identity}".encode("utf-8")
        ).hexdigest()
        row_proofs.append(
            FollowersRowProof(
                viewport_proof_id=proof_id,
                username=username,
                normalized_username=normalized,
                bounds=bounds,
                row_center=center,
                resource_identity=resource_identity,
                row_fingerprint=row_fingerprint,
                generations=generations,
            )
        )
    viewport = FollowersViewportProof(
        proof_id=proof_id,
        scope=scope,
        generations=generations,
        hierarchy_fingerprint=hierarchy_fingerprint,
        package_name=str(package_name or ""),
        activity_name=str(activity_name or ""),
        captured_at_monotonic=captured_at,
        source=str(source or "fresh_xml"),
        rows=tuple(row_proofs),
    )
    with _lock:
        previous = _viewports.get(scope)
        if previous is not None:
            _comparison_viewports[scope] = replace(
                previous, actionable=False, comparison_only_reason="superseded_by_fresh_viewport"
            )
        _viewports[scope] = viewport
        return viewport


def issue_row_action_token(
    scope: FollowersSurfaceScope,
    expected_username: str,
    *,
    max_age_ms: float = 1500.0,
    now_monotonic: float | None = None,
) -> tuple[RowActionToken | None, str]:
    """Resolve the expected username just-in-time from current actionable proof."""

    expected = _normalized_username(expected_username)
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    with _lock:
        viewport = _viewports.get(scope)
        if viewport is None:
            return None, "viewport_proof_missing"
        if not viewport.actionable:
            return None, "viewport_comparison_only"
        if viewport.generations != current_generations(scope):
            return None, "viewport_generation_mismatch"
        age_ms = max(0.0, (now - viewport.captured_at_monotonic) * 1000.0)
        if age_ms > float(max_age_ms):
            return None, "viewport_proof_stale"
        matches = [row for row in viewport.rows if row.normalized_username == expected]
        if len(matches) != 1:
            return None, "expected_row_missing" if not matches else "expected_row_ambiguous"
        row = matches[0]
        token_id = hashlib.sha256(
            f"{viewport.proof_id}|{row.row_fingerprint}|{expected}|{time.monotonic_ns()}".encode("utf-8")
        ).hexdigest()
        token = RowActionToken(
            token_id=token_id,
            viewport_proof_id=viewport.proof_id,
            scope=scope,
            generations=viewport.generations,
            expected_username=expected,
            row_username=row.username,
            row_bounds=row.bounds,
            resource_identity=row.resource_identity,
            row_fingerprint=row.row_fingerprint,
            row_center=row.row_center,
            issued_at_monotonic=now,
        )
        _row_action_tokens[token_id] = token
        return token, "row_action_token_issued"


def consume_row_action_token(token: RowActionToken | None) -> tuple[bool, str]:
    """Consume once, immediately before invalidation and the physical tap."""

    if token is None:
        return False, "row_action_token_missing"
    with _lock:
        current = _row_action_tokens.pop(token.token_id, None)
        if current != token:
            return False, "row_action_token_missing_or_consumed"
        viewport = _viewports.get(token.scope)
        if (
            viewport is None
            or not viewport.actionable
            or viewport.proof_id != token.viewport_proof_id
            or viewport.generations != token.generations
            or current_generations(token.scope) != token.generations
        ):
            return False, "row_action_token_invalidated"
        return True, "row_action_token_consumed"


def validate(
    proof: FollowersSurfaceProof | None,
    scope: FollowersSurfaceScope,
    *,
    mode: ProofReadMode,
    max_age_ms: float = DEFAULT_SAME_SURFACE_MAX_AGE_MS,
    now_monotonic: float | None = None,
) -> tuple[bool, str]:
    if mode == "certify_after_navigation":
        return False, "cache_forbidden_after_navigation"
    if proof is None:
        return False, "proof_missing"
    if proof.scope != scope:
        return False, "scope_mismatch"
    if proof.generations != current_generations(scope):
        return False, "generation_mismatch"
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    age_ms = max(0.0, (now - proof.captured_at_monotonic) * 1000.0)
    if age_ms > float(max_age_ms):
        return False, "proof_stale"
    return True, "valid_same_surface_proof"


def get(
    scope: FollowersSurfaceScope,
    *,
    mode: ProofReadMode,
    max_age_ms: float = DEFAULT_SAME_SURFACE_MAX_AGE_MS,
    now_monotonic: float | None = None,
) -> tuple[FollowersSurfaceProof | None, str]:
    with _lock:
        proof = _proofs.get(scope)
        ok, reason = validate(
            proof,
            scope,
            mode=mode,
            max_age_ms=max_age_ms,
            now_monotonic=now_monotonic,
        )
        return (proof if ok else None), reason


def invalidate(
    scope: FollowersSurfaceScope,
    *,
    reason: str,
    navigation_changed: bool = False,
    scroll_changed: bool = False,
) -> FollowersSurfaceGenerations:
    with _lock:
        before = current_generations(scope)
        after = FollowersSurfaceGenerations(
            navigation_generation=(
                before.navigation_generation + (1 if navigation_changed else 0)
            ),
            scroll_generation=before.scroll_generation + (1 if scroll_changed else 0),
        )
        _generations[scope] = after
        _proofs.pop(scope, None)
        viewport = _viewports.pop(scope, None)
        if viewport is not None:
            _comparison_viewports[scope] = replace(
                viewport,
                actionable=False,
                comparison_only_reason=str(reason or "surface_mutated"),
            )
        for token_id, token in list(_row_action_tokens.items()):
            if token.scope == scope:
                _row_action_tokens.pop(token_id, None)
        return after


def clear(scope: FollowersSurfaceScope | None = None) -> None:
    with _lock:
        if scope is None:
            _proofs.clear()
            _viewports.clear()
            _comparison_viewports.clear()
            _row_action_tokens.clear()
            _current_proof.set(None)
            return
        _proofs.pop(scope, None)
        _viewports.pop(scope, None)
        _comparison_viewports.pop(scope, None)
        for token_id, token in list(_row_action_tokens.items()):
            if token.scope == scope:
                _row_action_tokens.pop(token_id, None)
        current = _current_proof.get()
        if current is not None and current.scope == scope:
            _current_proof.set(None)


def clear_current() -> None:
    """Clear only the proof bound to the current execution context."""
    with _lock:
        current = _current_proof.get()
        if current is not None:
            _proofs.pop(current.scope, None)
            _viewports.pop(current.scope, None)
            _comparison_viewports.pop(current.scope, None)
            for token_id, token in list(_row_action_tokens.items()):
                if token.scope == current.scope:
                    _row_action_tokens.pop(token_id, None)
        _current_proof.set(None)


def get_current(
    *,
    mode: ProofReadMode,
    max_age_ms: float = DEFAULT_SAME_SURFACE_MAX_AGE_MS,
    now_monotonic: float | None = None,
) -> tuple[FollowersSurfaceProof | None, str]:
    proof = _current_proof.get()
    if proof is None:
        return None, "proof_missing"
    ok, reason = validate(
        proof,
        proof.scope,
        mode=mode,
        max_age_ms=max_age_ms,
        now_monotonic=now_monotonic,
    )
    return (proof if ok else None), reason


def reset_for_tests() -> None:
    with _lock:
        _proofs.clear()
        _generations.clear()
        _viewports.clear()
        _comparison_viewports.clear()
        _row_action_tokens.clear()
        _current_proof.set(None)
