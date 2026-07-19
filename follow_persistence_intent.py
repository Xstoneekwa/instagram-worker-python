from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STAGES = {"persisted", "abandoned_before_verified_follow", "review_required"}


def _root() -> Path:
    runtime_home = Path(os.getenv("PHONEFARM_RUNTIME_HOME", "/Users/admin/phonefarm-runtime"))
    return Path(
        os.getenv(
            "FOLLOW_PERSISTENCE_INTENT_ROOT",
            str(runtime_home / "run" / "follow-persistence-intents"),
        )
    )


def _intent_path(run_id: str, action_id: str) -> Path:
    return _root() / str(run_id) / f"{action_id}.json"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def create_prepared_intent(
    *,
    action_id: str,
    account_id: str,
    run_id: str,
    request_id: str,
    candidate_username: str,
    source_target_id: str | None,
    source_ct_username: str | None,
    settings_revision: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "version": 1,
        "action_id": str(action_id),
        "account_id": str(account_id),
        "run_id": str(run_id),
        "request_id": str(request_id),
        "candidate_username": str(candidate_username).strip().lstrip("@").lower(),
        "source_target_id": str(source_target_id or "") or None,
        "source_ct_username": str(source_ct_username or "").strip().lstrip("@").lower() or None,
        "settings_revision": str(settings_revision),
        "stage": "prepared_before_follow_tap",
        "followed_at": None,
        "created_at": now,
        "updated_at": now,
    }
    _atomic_write(_intent_path(run_id, action_id), payload)
    return payload


def update_intent_stage(
    *, run_id: str, action_id: str, stage: str, followed_at: str | None = None
) -> dict[str, Any]:
    path = _intent_path(run_id, action_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["stage"] = str(stage)
    if followed_at is not None:
        payload["followed_at"] = str(followed_at)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(path, payload)
    return payload


def load_nonterminal_intents(*, account_id: str, run_id: str) -> list[dict[str, Any]]:
    run_dir = _root() / str(run_id)
    if not run_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"follow_persistence_intent_unreadable:{path.name}") from exc
        if str(payload.get("account_id") or "") != str(account_id):
            raise RuntimeError(f"follow_persistence_intent_account_mismatch:{path.name}")
        if str(payload.get("run_id") or "") != str(run_id):
            raise RuntimeError(f"follow_persistence_intent_run_mismatch:{path.name}")
        if str(payload.get("stage") or "") not in TERMINAL_STAGES:
            out.append(payload)
    return out
