"""Generation-scoped proof storage for Instagram Followers surfaces.

The registry deliberately stores evidence by runtime scope instead of exposing a
process-wide last XML value.  Navigation certification is fail-closed: callers
must provide fresh evidence after a screen-changing intent.
"""

from __future__ import annotations

from dataclasses import dataclass
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


_lock = threading.RLock()
_generations: dict[FollowersSurfaceScope, FollowersSurfaceGenerations] = {}
_proofs: dict[FollowersSurfaceScope, FollowersSurfaceProof] = {}
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
    del reason  # callers log the bounded reason; the registry retains no payloads.
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
        return after


def clear(scope: FollowersSurfaceScope | None = None) -> None:
    with _lock:
        if scope is None:
            _proofs.clear()
            _current_proof.set(None)
            return
        _proofs.pop(scope, None)
        current = _current_proof.get()
        if current is not None and current.scope == scope:
            _current_proof.set(None)


def clear_current() -> None:
    """Clear only the proof bound to the current execution context."""
    with _lock:
        current = _current_proof.get()
        if current is not None:
            _proofs.pop(current.scope, None)
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
        _current_proof.set(None)
