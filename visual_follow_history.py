"""Local JSON store for visual follower picker outcomes (skip duplicates across runs)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
import social_memory
from logs import log

# Sentinel returned by the visual picker when the followers engine should iterate again.
VISUAL_FOLLOW_HISTORY_CONTINUE = -15927

_HISTORY_PATH = Path(__file__).resolve().parent / "logs" / "visual_follow_targets_history.json"
_MAX_RECORDS = 5000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def history_file_path() -> Path:
    return _HISTORY_PATH


def _action_scope_key(source_account_context: str) -> str:
    act = str(getattr(config, "VISUAL_FOLLOWERS_ACTION_ACCOUNT_USERNAME", "") or "").strip()
    if act:
        return social_memory.normalize_social_username(act)
    return social_memory.normalize_social_username(source_account_context or "")


def _composite_key(
    source_profile_username: str, target_username: str, source_account_context: str
) -> str:
    sp = social_memory.normalize_social_username(source_profile_username)
    tg = social_memory.normalize_social_username(target_username)
    scope = _action_scope_key(source_account_context)
    return f"{sp}||{tg}||{scope}"


def _load_history() -> dict[str, Any]:
    path = _HISTORY_PATH
    if not path.exists():
        return {"version": 1, "by_key": {}, "records": []}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"version": 1, "by_key": {}, "records": []}
        data.setdefault("version", 1)
        data.setdefault("by_key", {})
        data.setdefault("records", [])
        if not isinstance(data["by_key"], dict):
            data["by_key"] = {}
        if not isinstance(data["records"], list):
            data["records"] = []
        return data
    except Exception as e:
        log("warning", "visual_follow_history_load_failed", error=str(e))
        return {"version": 1, "by_key": {}, "records": []}


def _save_history(data: dict[str, Any]) -> None:
    path = _HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def visual_follow_history_lookup_record(
    *,
    source_profile_username: str,
    target_username: str,
    source_account_context: str,
) -> dict[str, Any] | None:
    data = _load_history()
    key = _composite_key(source_profile_username, target_username, source_account_context)
    rec = data["by_key"].get(key)
    return rec if isinstance(rec, dict) else None


def visual_follow_history_skip_reason_if_any(
    *,
    source_profile_username: str,
    target_username_hint: str,
    source_account_context: str,
) -> str | None:
    hint = social_memory.normalize_social_username(target_username_hint)
    if not hint:
        return None
    rec = visual_follow_history_lookup_record(
        source_profile_username=source_profile_username,
        target_username=hint,
        source_account_context=source_account_context,
    )
    if not rec:
        return None
    st = str(rec.get("status") or "").strip().lower()
    if st in ("followed", "follow_requested", "already_following"):
        return "already_processed_follow_target"
    if st == "skipped_private":
        if bool(getattr(config, "ENABLE_PRIVATE_ACCOUNT_FILTER", False)) and not bool(
            getattr(config, "FOLLOW_PRIVATE_ACCOUNTS", False)
        ):
            return "private_account_already_skipped"
    return None


def mark_visual_follow_target_processed(
    *,
    source_profile_username: str,
    target_username: str,
    visual_candidate_id: str,
    status: str,
    follow_verified: bool,
    follow_request_pending: bool,
    run_id: str,
    source_account_context: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    log(
        "info",
        "visual_follow_target_record_started",
        source_profile_username=source_profile_username,
        target_username=target_username,
        visual_candidate_id=visual_candidate_id,
        status=status,
        follow_verified=follow_verified,
        follow_request_pending=follow_request_pending,
        run_id=run_id,
        source_account_context=source_account_context,
    )
    tgt = social_memory.normalize_social_username(target_username)
    if not tgt:
        log(
            "info",
            "visual_follow_target_record_skipped_missing_username",
            source_profile_username=source_profile_username,
            status=status,
            run_id=run_id,
        )
        return {"ok": False, "reason": "missing_username"}

    now = _now_iso()
    st = str(status or "").strip().lower()
    followed_at = None
    requested_at = None
    if st == "followed":
        followed_at = now
    elif st == "follow_requested":
        requested_at = now

    meta = dict(metadata) if metadata else {}
    rec = {
        "target_username": tgt,
        "source_profile_username": social_memory.normalize_social_username(
            source_profile_username
        ),
        "visual_candidate_id": str(visual_candidate_id or ""),
        "status": st,
        "followed_at": followed_at,
        "requested_at": requested_at,
        "run_id": str(run_id or ""),
        "source_account_context": str(source_account_context or ""),
        "follow_verified": bool(follow_verified),
        "follow_request_pending": bool(follow_request_pending),
        "last_interaction": now,
        "interaction_type": "visual_follow",
        "metadata": meta,
    }

    try:
        data = _load_history()
        key = _composite_key(source_profile_username, tgt, source_account_context)
        data["by_key"][key] = rec
        data["records"].append(rec)
        if len(data["records"]) > _MAX_RECORDS:
            data["records"] = data["records"][-_MAX_RECORDS:]
        _save_history(data)
        log(
            "info",
            "visual_follow_target_record_success",
            source_profile_username=source_profile_username,
            target_username=tgt,
            status=st,
            history_key=key,
            run_id=run_id,
        )
        return {"ok": True, "key": key}
    except Exception as e:
        log(
            "error",
            "visual_follow_target_record_failed",
            source_profile_username=source_profile_username,
            target_username=tgt,
            status=st,
            error=str(e),
            run_id=run_id,
        )
        return {"ok": False, "reason": str(e)}
