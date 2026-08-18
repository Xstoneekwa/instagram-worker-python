from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable


PREPARED = "prepared"
PHYSICAL_ATTEMPT_STARTED = "physical_attempt_started"
VERIFIED = "verified"
PERSISTED = "persisted"
AMBIGUOUS = "ambiguous"
RECONCILED = "reconciled"
UNRESOLVED = "unresolved"
TERMINAL = "terminal"

MAX_FRESH_OBSERVATIONS = 2
MAX_PHYSICAL_RETRIES = 1


@dataclass(frozen=True)
class ReconciliationDecision:
    decision: str
    terminal: bool
    safe_to_retry: bool = False
    reason: str = ""


def deterministic_mutation_action_id(
    *, account_id: str, run_id: str, candidate_username: str, action_type: str
) -> str:
    username = str(candidate_username or "").strip().lstrip("@").lower()
    action = str(action_type or "").strip().lower()
    if action not in {"follow", "unfollow"}:
        raise ValueError("unsupported_mutation_action_type")
    if not all((str(account_id or "").strip(), str(run_id or "").strip(), username)):
        raise ValueError("mutation_identity_incomplete")
    material = f"{account_id}|{run_id}|{username}|{action}_verified:v1"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, material))


def decide_reconciliation(
    *,
    action_type: str,
    canonical_receipt_exists: bool,
    exact_identity: bool,
    fresh_states: Iterable[str],
    physical_retry_count: int = 0,
) -> ReconciliationDecision:
    """Pure, fail-closed policy for an ambiguous social mutation.

    Two concordant fresh observations are required when no canonical receipt
    exists.  This policy never claims physical exactly-once; it only decides
    whether canonical persistence is safe or one bounded retry may be offered.
    """
    action = str(action_type or "").strip().lower()
    if action not in {"follow", "unfollow"}:
        return ReconciliationDecision(UNRESOLVED, False, reason="unsupported_action")
    if canonical_receipt_exists:
        return ReconciliationDecision(PERSISTED, True, reason="canonical_receipt_found")
    if not exact_identity:
        return ReconciliationDecision(UNRESOLVED, False, reason="identity_not_exact")

    states = [str(value or "").strip().lower() for value in fresh_states]
    if len(states) < MAX_FRESH_OBSERVATIONS or len(set(states[-2:])) != 1:
        return ReconciliationDecision(UNRESOLVED, False, reason="fresh_state_not_concordant")
    state = states[-1]
    success_state = "following" if action == "follow" else "not_following"
    pre_action_state = "follow" if action == "follow" else "following"
    if state == success_state:
        return ReconciliationDecision(RECONCILED, True, reason="fresh_state_proves_success")
    if state == pre_action_state and int(physical_retry_count or 0) < MAX_PHYSICAL_RETRIES:
        return ReconciliationDecision(
            AMBIGUOUS,
            False,
            safe_to_retry=True,
            reason="fresh_state_proves_not_applied",
        )
    return ReconciliationDecision(UNRESOLVED, False, reason="state_does_not_prove_outcome")
