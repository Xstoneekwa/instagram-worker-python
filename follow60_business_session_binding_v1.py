"""Immutable run-scoped identity for Follow60 Mainline stages.

The business session id is created by the runner before any Follow stage.  This
module only freezes and validates that existing identity; it never invents a
replacement while a candidate stage is running.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


BUSINESS_SESSION_BINDING_VERSION = "BusinessSessionBindingV1"
MAINLINE_BINDING_KIND = "mainline"


@dataclass(frozen=True)
class BusinessSessionBindingV1:
    business_session_id: str
    account_id: str
    request_id: str
    run_id: str
    attempt_id: int
    worker_sha: str
    binding_kind: str
    created_at: str
    nonce: str
    version: str = BUSINESS_SESSION_BINDING_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def create_mainline_business_session_binding(
    *,
    business_session_id: str,
    account_id: str,
    request_id: str,
    run_id: str,
    attempt_id: int,
    worker_sha: str,
) -> BusinessSessionBindingV1:
    """Freeze the runner-owned identity once at the Mainline run boundary."""
    binding = BusinessSessionBindingV1(
        business_session_id=str(business_session_id or "").strip(),
        account_id=str(account_id or "").strip(),
        request_id=str(request_id or "").strip(),
        run_id=str(run_id or "").strip(),
        attempt_id=int(attempt_id or 0),
        worker_sha=str(worker_sha or "").strip().lower(),
        binding_kind=MAINLINE_BINDING_KIND,
        created_at=datetime.now(timezone.utc).isoformat(),
        nonce=str(uuid.uuid4()),
    )
    validated, reason = validate_business_session_binding(
        binding,
        account_id=binding.account_id,
        request_id=binding.request_id,
        run_id=binding.run_id,
        attempt_id=binding.attempt_id,
        worker_sha=binding.worker_sha,
        binding_kind=MAINLINE_BINDING_KIND,
        business_session_id=binding.business_session_id,
    )
    if validated is None:
        raise ValueError(reason)
    return binding


def validate_business_session_binding(
    binding: BusinessSessionBindingV1 | Mapping[str, Any] | None,
    *,
    account_id: str,
    request_id: str,
    run_id: str,
    attempt_id: int,
    worker_sha: str,
    binding_kind: str = MAINLINE_BINDING_KIND,
    business_session_id: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """Validate every immutable scope field and return a detached snapshot."""
    if binding is None:
        return None, "business_session_binding_missing"
    try:
        raw = (
            binding.to_dict()
            if isinstance(binding, BusinessSessionBindingV1)
            else dict(binding)
        )
    except (TypeError, ValueError):
        return None, "business_session_binding_missing"
    if str(raw.get("version") or "") != BUSINESS_SESSION_BINDING_VERSION:
        return None, "business_session_binding_missing"
    if not str(raw.get("business_session_id") or "").strip():
        return None, "business_session_id_missing"
    if str(raw.get("account_id") or "") != str(account_id or "").strip():
        return None, "business_session_account_mismatch"
    if str(raw.get("request_id") or "") != str(request_id or "").strip():
        return None, "business_session_request_mismatch"
    if str(raw.get("run_id") or "") != str(run_id or "").strip():
        return None, "business_session_run_mismatch"
    if int(raw.get("attempt_id") or 0) != int(attempt_id or 0):
        return None, "business_session_attempt_mismatch"
    expected_sha = str(worker_sha or "").strip().lower()
    if str(raw.get("worker_sha") or "").strip().lower() != expected_sha:
        return None, "business_session_worker_mismatch"
    if str(raw.get("binding_kind") or "") != str(binding_kind or ""):
        return None, "business_session_binding_kind_mismatch"
    expected_session = str(business_session_id or "").strip()
    if expected_session and str(raw.get("business_session_id") or "") != expected_session:
        return None, "business_session_id_mismatch"
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        return None, "business_session_worker_mismatch"
    if not str(raw.get("created_at") or "").strip() or not str(raw.get("nonce") or "").strip():
        return None, "business_session_binding_missing"
    return dict(raw), ""


def build_candidate_stage_binding(
    binding: BusinessSessionBindingV1 | Mapping[str, Any],
    *,
    action_id: str,
    candidate_username: str,
    source_target_id: str = "",
) -> dict[str, Any]:
    """Add candidate-local identity without mutating the session binding."""
    raw = binding.to_dict() if isinstance(binding, BusinessSessionBindingV1) else dict(binding)
    return {
        **raw,
        "action_id": str(action_id or "").strip(),
        "candidate_username": str(candidate_username or "").strip(),
        "source_target_id": str(source_target_id or "").strip(),
    }
