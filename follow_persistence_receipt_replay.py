"""DB-only replay for physically verified Follow receipts.

The receipt is written and fsync'd at the exact Following verification boundary.
Replay uses the existing idempotent Follow RPC and never reads or touches a
device.  Prepared-but-unverified intents are deliberately ignored here.
"""

from __future__ import annotations

import time
from typing import Any

import follow_persistence_intent
import supabase_client
from follow_persistence_rpc import action_id_hash, validate_rpc_response
from logs import log


def replay_verified_receipts(
    *,
    account_id: str | None = None,
    run_id: str | None = None,
    limit: int = 100,
    time_budget_seconds: float = 8.0,
) -> dict[str, Any]:
    started = time.monotonic()
    replayed = 0
    ignored_prepared = 0
    ignored_legacy = 0
    pending_verified = 0
    intents = follow_persistence_intent.load_all_nonterminal_intents(limit=limit)
    for intent in intents:
        if account_id and str(intent.get("account_id") or "") != str(account_id):
            continue
        if run_id and str(intent.get("run_id") or "") != str(run_id):
            continue
        if str(intent.get("receipt_schema") or "") != follow_persistence_intent.RECEIPT_SCHEMA:
            ignored_legacy += 1
            continue
        stage = str(intent.get("stage") or "")
        if stage == "prepared_before_follow_tap":
            ignored_prepared += 1
            continue
        if stage != "follow_physically_verified":
            continue
        pending_verified += 1
        if time.monotonic() - started >= max(0.1, float(time_budget_seconds)):
            return {
                "ok": False,
                "reason": "candidate_receipt_replay_budget_exhausted",
                "replayed": replayed,
                "pending_verified": pending_verified,
                "ignored_prepared": ignored_prepared,
                "ignored_legacy": ignored_legacy,
            }
        action_id = str(intent.get("action_id") or "")
        try:
            result = supabase_client.persist_verified_follow_success_rpc(
                action_id=action_id,
                account_id=str(intent.get("account_id") or ""),
                run_id=str(intent.get("run_id") or ""),
                request_id=str(intent.get("request_id") or ""),
                candidate_username=str(intent.get("candidate_username") or ""),
                source_target_id=str(intent.get("source_target_id") or "") or None,
                source_ct_username=str(intent.get("source_ct_username") or ""),
                followed_at=str(intent.get("followed_at") or ""),
                follow_state_after="following",
                settings_revision_expected=str(intent.get("settings_revision") or ""),
                verification_method="worker_exact_following_state_durable_receipt",
                metadata_safe={
                    "source": "candidate_local_receipt_replay",
                    "receipt_metadata_safe": dict(
                        intent.get("receipt_metadata_safe") or {}
                    ),
                },
            )
            valid, reason = validate_rpc_response(result, expected_action_id=action_id)
            if not valid:
                return {
                    "ok": False,
                    "reason": reason,
                    "replayed": replayed,
                    "pending_verified": pending_verified,
                    "ignored_prepared": ignored_prepared,
                    "ignored_legacy": ignored_legacy,
                }
            follow_persistence_intent.update_intent_stage(
                run_id=str(intent.get("run_id") or ""),
                action_id=action_id,
                stage="persisted",
                metadata_safe={"replayed_db_only": True},
            )
        except Exception as exc:
            log(
                "error",
                "candidate_local_follow_receipt_replay_failed",
                account_id=intent.get("account_id"),
                run_id=intent.get("run_id"),
                request_id=intent.get("request_id"),
                action_id_hash=action_id_hash(action_id),
                candidate_username=intent.get("candidate_username"),
                error_type=type(exc).__name__,
                device_actions_started=False,
            )
            return {
                "ok": False,
                "reason": type(exc).__name__,
                "replayed": replayed,
                "pending_verified": pending_verified,
                "ignored_prepared": ignored_prepared,
                "ignored_legacy": ignored_legacy,
            }
        replayed += 1
        log(
            "info",
            "candidate_local_follow_receipt_replayed",
            account_id=intent.get("account_id"),
            run_id=intent.get("run_id"),
            request_id=intent.get("request_id"),
            action_id_hash=action_id_hash(action_id),
            candidate_username=intent.get("candidate_username"),
            device_actions_started=False,
            idempotent_replay=str(result.get("status") or "") == "idempotent_replay",
        )
    return {
        "ok": True,
        "replayed": replayed,
        "pending_verified": 0,
        "ignored_prepared": ignored_prepared,
        "ignored_legacy": ignored_legacy,
        "duration_ms": round((time.monotonic() - started) * 1000.0, 2),
    }
