"""Durable, bounded replay queue for non-critical Supabase projections.

Business actions and their critical persistence stay outside this queue.  It
only protects secondary projections (source attribution, Like/Mute summaries,
and detailed action logs) from a transient control-plane outage without
keeping an account session in ``running``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from logs import log
import supabase_client


OUTBOX_SCHEMA = "DEFERRED_PROJECTION_OUTBOX_V1"
DEFAULT_OUTBOX_PATH = "/Users/admin/phonefarm-worker-runtime/deferred_projection_outbox_v1.sqlite3"
MAX_PAYLOAD_BYTES = 256 * 1024
CLAIM_LEASE_SECONDS = 30


def _path() -> Path:
    raw = str(os.environ.get("WORKER_DEFERRED_PROJECTION_OUTBOX_PATH") or DEFAULT_OUTBOX_PATH).strip()
    return Path(raw)


def _connect() -> sqlite3.Connection:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=2.0)
    conn.execute("pragma journal_mode = wal")
    conn.execute("pragma synchronous = full")
    conn.execute(
        """
        create table if not exists deferred_projection_outbox (
          id text primary key,
          schema_name text not null,
          kind text not null,
          payload_json text not null,
          created_at_epoch real not null,
          attempts integer not null default 0,
          state text not null default 'pending',
          locked_until_epoch real,
          last_error text
        )
        """
    )
    conn.execute(
        "create index if not exists deferred_projection_outbox_state_created_idx on deferred_projection_outbox (state, created_at_epoch)"
    )
    conn.commit()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def enqueue(records: Iterable[dict[str, Any]]) -> int:
    rows: list[tuple[str, str, str, str, float]] = []
    now = time.time()
    for record in records:
        kind = str(record.get("kind") or "").strip()
        payload = record.get("payload")
        if kind not in {"supabase_step", "action_log"} or not isinstance(payload, dict):
            continue
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("deferred_projection_payload_too_large")
        rows.append((str(uuid.uuid4()), OUTBOX_SCHEMA, kind, encoded, now))
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            """
            insert into deferred_projection_outbox
              (id, schema_name, kind, payload_json, created_at_epoch)
            values (?, ?, ?, ?, ?)
            """,
            rows,
        )
    log("info", "deferred_projection_outbox_enqueued", enqueued_count=len(rows))
    return len(rows)


def _claim(limit: int) -> list[tuple[str, str, dict[str, Any]]]:
    now = time.time()
    lease_until = now + CLAIM_LEASE_SECONDS
    with _connect() as conn:
        conn.execute("begin immediate")
        conn.execute(
            """
            update deferred_projection_outbox
               set state = 'pending', locked_until_epoch = null
             where state = 'processing' and coalesce(locked_until_epoch, 0) <= ?
            """,
            (now,),
        )
        selected = conn.execute(
            """
            select id, kind, payload_json
              from deferred_projection_outbox
             where state = 'pending'
             order by created_at_epoch asc
             limit ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        ids = [row[0] for row in selected]
        if ids:
            marks = ",".join("?" for _ in ids)
            conn.execute(
                f"update deferred_projection_outbox set state = 'processing', locked_until_epoch = ? where id in ({marks})",
                (lease_until, *ids),
            )
        conn.commit()
    claimed: list[tuple[str, str, dict[str, Any]]] = []
    for record_id, kind, encoded in selected:
        try:
            payload = json.loads(encoded)
        except (TypeError, json.JSONDecodeError):
            payload = {}
        claimed.append((str(record_id), str(kind), payload if isinstance(payload, dict) else {}))
    return claimed


def _replay(kind: str, payload: dict[str, Any]) -> None:
    if kind == "supabase_step":
        fn_name = str(payload.get("fn_name") or "").strip()
        fn = getattr(supabase_client, fn_name, None)
        if not callable(fn):
            raise RuntimeError("deferred_projection_function_unavailable")
        out = fn(*(payload.get("args") or []), **dict(payload.get("kwargs") or {}))
        if isinstance(out, dict) and out.get("ok") is False:
            raise RuntimeError(str(out.get("error") or "deferred_projection_rpc_failed"))
        return
    if kind == "action_log":
        out = supabase_client.insert_action_log(**payload)
        if isinstance(out, dict) and out.get("ok") is False:
            raise RuntimeError(str(out.get("error") or "deferred_action_log_failed"))
        return
    raise RuntimeError("deferred_projection_kind_invalid")


def drain(*, limit: int = 25, time_budget_seconds: float = 4.0) -> dict[str, Any]:
    started = time.monotonic()
    claimed = _claim(limit)
    succeeded = 0
    failed = 0
    for index, (record_id, kind, payload) in enumerate(claimed):
        if time.monotonic() - started >= max(0.1, float(time_budget_seconds)):
            with _connect() as conn:
                conn.executemany(
                    "update deferred_projection_outbox set state = 'pending', locked_until_epoch = null where id = ?",
                    [(item_id,) for item_id, _kind, _payload in claimed[index:]],
                )
            break
        try:
            _replay(kind, payload)
        except Exception as exc:
            failed += 1
            with _connect() as conn:
                conn.execute(
                    """
                    update deferred_projection_outbox
                       set state = case when attempts + 1 >= 10 then 'dead_letter' else 'pending' end,
                           locked_until_epoch = null,
                           attempts = attempts + 1, last_error = ?
                     where id = ?
                    """,
                    (type(exc).__name__, record_id),
                )
            continue
        else:
            succeeded += 1
            with _connect() as conn:
                conn.execute("delete from deferred_projection_outbox where id = ?", (record_id,))
    summary = {
        "claimed_count": len(claimed),
        "succeeded_count": succeeded,
        "failed_count": failed,
        "duration_ms": round((time.monotonic() - started) * 1000.0, 2),
    }
    if claimed:
        log("info" if not failed else "warning", "deferred_projection_outbox_drain_completed", **summary)
    return summary
