"""Crash-safe local outbox for the account-scoped Follow 60 post-follow receipts.

The UI thread journals each physically verified stage locally.  A single
composite RPC flushes the candidate before any next-candidate UI action.  The
database contract is idempotent, so replay never touches the phone.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from logs import log


OUTBOX_SCHEMA = "FOLLOW_60S_POST_FOLLOW_OUTBOX_V2"
VALID_STAGES = (
    "mute_posts_verified",
    "mute_stories_verified",
    "like_verified",
    "return_ct_exact",
)
DEFAULT_PATH = Path(
    os.environ.get(
        "FOLLOW_60S_POST_FOLLOW_OUTBOX_PATH",
        "/Users/admin/phonefarm-worker-runtime/follow_60s_post_follow_outbox_v2.sqlite3",
    )
)


def action_id_hash(action_id: str) -> str:
    return hashlib.sha256(str(action_id or "").encode("utf-8")).hexdigest()


def _connect(path: Path = DEFAULT_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=3.0)
    conn.execute("pragma journal_mode=WAL")
    conn.execute("pragma synchronous=FULL")
    conn.execute("pragma foreign_keys=ON")
    conn.execute(
        """
        create table if not exists post_follow_stage_receipts (
          schema_version text not null,
          account_id text not null,
          original_run_id text not null,
          original_request_id text not null,
          action_id text not null,
          action_id_hash text not null,
          stage text not null,
          candidate_username text not null,
          source_profile text not null,
          attempt_id integer not null,
          business_session_id text not null,
          verified integer not null check (verified = 1),
          event_at text not null,
          proof_type_redacted text not null,
          idempotency_key text not null,
          cycle_complete integer not null check (cycle_complete in (0, 1)),
          delivery_status text not null check (delivery_status in ('pending', 'delivering')),
          attempt_count integer not null default 0,
          last_error_redacted text not null default '',
          payload_json text not null,
          created_at_monotonic real not null,
          primary key (account_id, original_run_id, action_id_hash, stage)
        )
        """
    )
    conn.commit()
    for local_file in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            if local_file.exists():
                os.chmod(local_file, 0o600)
        except OSError:
            pass
    return conn


def _clean_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    forbidden = {"xml", "hierarchy", "screenshot", "screenshot_path", "token", "secret"}
    return {
        str(key): value
        for key, value in raw.items()
        if str(key).lower() not in forbidden
        and not str(key).lower().endswith("_xml")
        and not str(key).lower().endswith("_screenshot_path")
    }


def _redact_error(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"(?i)bearer\s+\S+", "bearer [REDACTED]", text)
    text = re.sub(r"https?://\S+", "[REDACTED_URL]", text)
    text = re.sub(r"(?i)(token|secret|password|apikey|api_key)=\S+", r"\1=[REDACTED]", text)
    return text[:160]


def journal_stage(
    *,
    account_id: str,
    original_run_id: str,
    request_id: str,
    action_id: str,
    stage: str,
    candidate_username: str,
    source_profile: str,
    attempt_id: int,
    business_session_id: str,
    verified_at: str,
    payload: dict[str, Any] | None = None,
    path: Path = DEFAULT_PATH,
) -> dict[str, Any]:
    values = {
        "account_id": str(account_id or "").strip(),
        "original_run_id": str(original_run_id or "").strip(),
        "original_request_id": str(request_id or "").strip(),
        "action_id": str(action_id or "").strip(),
        "stage": str(stage or "").strip(),
        "candidate_username": str(candidate_username or "").strip().lstrip("@").lower(),
        "source_profile": str(source_profile or "").strip().lstrip("@").lower(),
        "business_session_id": str(business_session_id or "").strip(),
        "event_at": str(verified_at or "").strip(),
    }
    if (
        any(not values[key] for key in values)
        or values["stage"] not in VALID_STAGES
        or int(attempt_id or 0) < 1
    ):
        raise ValueError("follow60_stage_binding_missing_or_invalid")
    digest = action_id_hash(values["action_id"])
    idempotency_key = ":".join(
        (
            values["account_id"], values["original_run_id"], digest,
            values["stage"],
        )
    )
    cleaned_payload = _clean_payload(payload)
    proof_type = str(cleaned_payload.get("proof_type") or values["stage"])[:120]
    cycle_complete = values["stage"] == "return_ct_exact"
    payload_json = json.dumps(
        cleaned_payload, sort_keys=True, separators=(",", ":"), default=str
    )
    with _connect(path) as conn:
        cur = conn.execute(
            """
            insert or ignore into post_follow_stage_receipts (
              schema_version, account_id, original_run_id, original_request_id,
              action_id, action_id_hash,
              stage, candidate_username, source_profile, attempt_id,
              business_session_id, verified, event_at, proof_type_redacted,
              idempotency_key, cycle_complete, delivery_status, attempt_count,
              last_error_redacted, payload_json, created_at_monotonic
            ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                OUTBOX_SCHEMA, values["account_id"], values["original_run_id"],
                values["original_request_id"],
                values["action_id"], digest, values["stage"],
                values["candidate_username"], values["source_profile"], int(attempt_id),
                values["business_session_id"], 1, values["event_at"], proof_type,
                idempotency_key, int(cycle_complete), "pending", 0, "",
                payload_json, time.monotonic(),
            ),
        )
        conn.commit()
    return {
        "ok": True,
        "inserted": cur.rowcount == 1,
        "action_id_hash": digest,
        "idempotency_key": idempotency_key,
        "cycle_complete": cycle_complete,
    }


def _load_groups(path: Path = DEFAULT_PATH) -> list[list[dict[str, Any]]]:
    if not path.exists():
        return []
    with _connect(path) as conn:
        rows = conn.execute(
            """
            select schema_version, account_id, original_run_id, original_request_id,
                   action_id,
                   action_id_hash, stage, candidate_username, source_profile,
                   attempt_id, business_session_id, verified, event_at,
                   proof_type_redacted, idempotency_key, cycle_complete,
                   delivery_status, attempt_count, last_error_redacted, payload_json
            from post_follow_stage_receipts
            order by created_at_monotonic, stage
            """
        ).fetchall()
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        record = {
            "schema_version": row[0], "account_id": row[1],
            "original_run_id": row[2], "original_request_id": row[3],
            "action_id": row[4], "action_id_hash": row[5], "stage": row[6],
            "candidate_username": row[7], "source_profile": row[8],
            "attempt_id": int(row[9]), "business_session_id": row[10],
            "verified": bool(row[11]), "event_at": row[12],
            "proof_type_redacted": row[13], "idempotency_key": row[14],
            "cycle_complete": bool(row[15]), "delivery_status": row[16],
            "attempt_count": int(row[17]), "last_error_redacted": row[18],
            "payload": json.loads(row[19] or "{}"),
        }
        grouped[(row[1], row[2], row[5])].append(record)
    return list(grouped.values())


def pending_count(path: Path = DEFAULT_PATH) -> int:
    if not path.exists():
        return 0
    with _connect(path) as conn:
        row = conn.execute("select count(*) from post_follow_stage_receipts").fetchone()
    return int((row or [0])[0] or 0)


def _delete_confirmed(group: list[dict[str, Any]], path: Path) -> None:
    first = group[0]
    with _connect(path) as conn:
        conn.execute(
            """
            delete from post_follow_stage_receipts
            where account_id=? and original_run_id=? and action_id_hash=?
            """,
            (first["account_id"], first["original_run_id"], first["action_id_hash"]),
        )
        conn.commit()


def _mark_delivery_attempt(group: list[dict[str, Any]], path: Path) -> None:
    first = group[0]
    with _connect(path) as conn:
        conn.execute(
            """
            update post_follow_stage_receipts
            set delivery_status='delivering', attempt_count=attempt_count+1,
                last_error_redacted=''
            where account_id=? and original_run_id=? and action_id_hash=?
            """,
            (first["account_id"], first["original_run_id"], first["action_id_hash"]),
        )
        conn.commit()


def _mark_delivery_error(
    group: list[dict[str, Any]], path: Path, reason: object,
) -> None:
    first = group[0]
    with _connect(path) as conn:
        conn.execute(
            """
            update post_follow_stage_receipts
            set delivery_status='pending', last_error_redacted=?
            where account_id=? and original_run_id=? and action_id_hash=?
            """,
            (
                _redact_error(reason), first["account_id"], first["original_run_id"],
                first["action_id_hash"],
            ),
        )
        conn.commit()


def flush_pending(*, path: Path = DEFAULT_PATH) -> dict[str, Any]:
    groups = _load_groups(path)
    flushed = 0
    ledger_acks: list[dict[str, Any]] = []
    for group in groups:
        first = group[0]
        binding_fields = (
            "account_id", "original_run_id", "original_request_id", "action_id",
            "action_id_hash", "candidate_username", "source_profile", "attempt_id",
            "business_session_id",
        )
        if any(any(row[field] != first[field] for row in group) for field in binding_fields):
            return {"ok": False, "reason": "follow60_stage_binding_missing_or_invalid", "flushed": flushed}
        _mark_delivery_attempt(group, path)
        try:
            import supabase_client

            out = supabase_client.persist_follow_60s_post_follow_v2(
                account_id=first["account_id"],
                run_id=first["original_run_id"],
                request_id=first["original_request_id"],
                action_id=first["action_id"],
                action_id_hash_value=first["action_id_hash"],
                username=first["candidate_username"],
                source_profile=first["source_profile"],
                attempt_id=first["attempt_id"],
                business_session_id=first["business_session_id"],
                cycle_complete=any(bool(row["cycle_complete"]) for row in group),
                stages=[
                    {
                        "stage": row["stage"],
                        "event_at": row["event_at"],
                        "payload": row["payload"],
                    }
                    for row in group
                ],
            )
        except Exception as exc:
            _mark_delivery_error(group, path, type(exc).__name__)
            return {"ok": False, "reason": str(exc)[:240], "flushed": flushed}
        if not bool(out.get("ok")) or out.get("binding_valid") is not True:
            _mark_delivery_error(group, path, out.get("reason") or "rpc_not_confirmed")
            return {"ok": False, "reason": str(out.get("reason") or "rpc_not_confirmed"), "flushed": flushed}
        expected_stages = {str(row["stage"]) for row in group}
        confirmed_stages = {
            str(stage)
            for stage in (
                list(out.get("inserted_stages") or [])
                + list(out.get("duplicate_stages") or [])
            )
        }
        if not expected_stages.issubset(confirmed_stages):
            _mark_delivery_error(group, path, "rpc_stage_confirmation_incomplete")
            return {
                "ok": False,
                "reason": "rpc_stage_confirmation_incomplete",
                "flushed": flushed,
            }
        if any(bool(row["cycle_complete"]) for row in group):
            return_row = next(
                (row for row in group if row["stage"] == "return_ct_exact"), None
            )
            return_payload = dict((return_row or {}).get("payload") or {})
            like_verified = "like_verified" in expected_stages
            like_terminal_status = (
                "verified" if like_verified
                else str(return_payload.get("like_terminal_status") or "")
            )
            like_terminal_reason = (
                "like_verified" if like_verified
                else str(return_payload.get("like_terminal_reason") or "")
            )
            control_id = str(return_payload.get("control_id") or "").strip()
            worker_sha = str(return_payload.get("worker_sha") or "").strip().lower()
            if (
                return_row is None
                or like_terminal_status not in {"verified", "safe_skip"}
                or not like_terminal_reason
                or not control_id
                or not re.fullmatch(r"[0-9a-f]{40}", worker_sha)
            ):
                _mark_delivery_error(group, path, "cycle_ledger_binding_incomplete")
                return {
                    "ok": False,
                    "reason": "cycle_ledger_binding_incomplete",
                    "flushed": flushed,
                }
            try:
                ledger_out = supabase_client.ack_follow_60s_completed_cycle_v1(
                    control_id=control_id,
                    account_id=first["account_id"],
                    run_id=first["original_run_id"],
                    request_id=first["original_request_id"],
                    action_id=first["action_id"],
                    action_id_hash_value=first["action_id_hash"],
                    attempt_id=first["attempt_id"],
                    business_session_id=first["business_session_id"],
                    candidate_username=first["candidate_username"],
                    source_profile=first["source_profile"],
                    worker_sha=worker_sha,
                    like_terminal_status=like_terminal_status,
                    like_terminal_reason=like_terminal_reason,
                )
            except Exception as exc:
                _mark_delivery_error(group, path, type(exc).__name__)
                return {"ok": False, "reason": str(exc)[:240], "flushed": flushed}
            if not bool(ledger_out.get("ok")):
                _mark_delivery_error(
                    group, path, ledger_out.get("reason") or "cycle_ledger_not_confirmed"
                )
                return {
                    "ok": False,
                    "reason": str(
                        ledger_out.get("reason") or "cycle_ledger_not_confirmed"
                    ),
                    "flushed": flushed,
                }
            ledger_acks.append(dict(ledger_out))
        _delete_confirmed(group, path)
        flushed += 1
        log(
            "info", "follow_60s_post_follow_composite_persisted_v2",
            account_id=first["account_id"], run_id=first["original_run_id"],
            request_id=first["original_request_id"], action_id_hash=first["action_id_hash"],
            candidate_username=first["candidate_username"],
            inserted_stages=out.get("inserted_stages"), duplicate_stages=out.get("duplicate_stages"),
        )
    return {
        "ok": True,
        "flushed": flushed,
        "pending": pending_count(path),
        "ledger_acks": ledger_acks,
        "latest_ledger_ack": ledger_acks[-1] if ledger_acks else {},
    }


def flush_pending_bounded(*, budget_s: float = 0.55, path: Path = DEFAULT_PATH) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "reason": "bounded_flush_timeout"}

    def _run() -> None:
        result.clear()
        result.update(flush_pending(path=path))

    thread = threading.Thread(target=_run, name="follow60-post-follow-flush", daemon=True)
    thread.start()
    thread.join(max(0.05, float(budget_s)))
    if thread.is_alive():
        return {"ok": False, "reason": "bounded_flush_timeout", "pending": pending_count(path)}
    return dict(result)
