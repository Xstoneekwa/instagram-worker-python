"""Durable fail-closed Like-before-Follow ledger for Ordering V2."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


ORDERING_VERSION = "FOLLOW60_ORDERING_V2"
STAGES = (
    "profile_certified",
    "post_opened",
    "like_verified",
    "like_skipped",
    "profile_reentry_verified",
    "follow_pending",
    "follow_verified",
    "follow_failed",
    "mute_posts_verified",
    "mute_stories_verified",
    "return_ct_exact",
    "cycle_complete",
    "stop_recorded",
)

DEFAULT_PATH = Path(
    os.environ.get(
        "FOLLOW60_ORDERING_V2_LEDGER_PATH",
        "/Users/admin/phonefarm-worker-runtime/follow60_ordering_v2_ledger_v1.sqlite3",
    )
)
_LOCK = threading.RLock()
_ACTIVE_STORES: dict[tuple[str, str, str], "DurableOrderingLedgerV1"] = {}


@dataclass(frozen=True)
class LedgerScope:
    account_id: str
    run_id: str
    request_id: str
    business_session_id: str
    target_id: str
    action_id: str
    candidate_username: str
    ordering_version: str = ORDERING_VERSION

    def validate(self) -> None:
        for field_name, value in self.__dict__.items():
            if not str(value or "").strip():
                raise ValueError(f"ledger_scope_missing:{field_name}")
        if self.ordering_version != ORDERING_VERSION:
            raise ValueError("ledger_ordering_version_invalid")

    def idempotency_key(self, action_type: str) -> str:
        self.validate()
        if action_type not in STAGES:
            raise ValueError("ledger_action_type_invalid")
        material = "|".join(
            (
                self.account_id,
                self.run_id,
                self.request_id,
                self.business_session_id,
                self.target_id,
                self.action_id,
                self.candidate_username.lower().lstrip("@"),
                self.ordering_version,
                action_type,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class LedgerReceipt:
    action_type: str
    payload_hash: str
    idempotency_key: str
    acknowledged: bool = False


@dataclass
class OrderingLedger:
    scope: LedgerScope
    stages: set[str] = field(default_factory=set)
    receipts: dict[str, LedgerReceipt] = field(default_factory=dict)
    stopped: bool = False

    def _payload_hash(self, payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        ).hexdigest()

    def _require(self, stage: str, required: tuple[str, ...]) -> None:
        missing = [item for item in required if item not in self.stages]
        if missing:
            raise ValueError(f"ledger_transition_invalid:{stage}:missing:{','.join(missing)}")

    def _validate_transition(self, stage: str) -> None:
        if stage == "profile_certified":
            return
        if stage == "post_opened":
            self._require(stage, ("profile_certified",))
        elif stage in {"like_verified", "like_skipped"}:
            self._require(stage, ("post_opened",))
            other = "like_skipped" if stage == "like_verified" else "like_verified"
            if other in self.stages:
                raise ValueError(f"ledger_transition_conflict:{stage}:{other}")
        elif stage == "profile_reentry_verified":
            if not ({"like_verified", "like_skipped"} & self.stages):
                raise ValueError("ledger_transition_invalid:profile_reentry_verified:like_terminal_missing")
        elif stage == "follow_pending":
            self._require(stage, ("profile_reentry_verified",))
        elif stage in {"follow_verified", "follow_failed"}:
            self._require(stage, ("follow_pending",))
            other = "follow_failed" if stage == "follow_verified" else "follow_verified"
            if other in self.stages:
                raise ValueError(f"ledger_transition_conflict:{stage}:{other}")
        elif stage in {"mute_posts_verified", "mute_stories_verified"}:
            self._require(stage, ("follow_verified",))
        elif stage == "return_ct_exact":
            self._require(
                stage,
                ("follow_verified", "mute_posts_verified", "mute_stories_verified"),
            )
        elif stage == "cycle_complete":
            self._require(
                stage,
                (
                    "profile_certified",
                    "post_opened",
                    "profile_reentry_verified",
                    "follow_verified",
                    "mute_posts_verified",
                    "mute_stories_verified",
                    "return_ct_exact",
                ),
            )
            if not ({"like_verified", "like_skipped"} & self.stages):
                raise ValueError("ledger_transition_invalid:cycle_complete:like_terminal_missing")
        elif stage == "stop_recorded":
            return
        else:
            raise ValueError("ledger_action_type_invalid")

    def apply_receipt(self, stage: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.scope.validate()
        if stage not in STAGES:
            raise ValueError("ledger_action_type_invalid")
        if stage == "like_verified" and "like_action_state" in payload:
            if str(payload.get("like_action_state") or "") != "LIKE_ACTION_PERFORMED_NOW":
                raise ValueError("ledger_like_verified_without_current_action")
            if payload.get("real_tap_sent") is not True:
                raise ValueError("ledger_like_verified_without_real_tap")
            if payload.get("fresh_like_verified") is not True:
                raise ValueError("ledger_like_verified_without_fresh_verification")
            if str(payload.get("candidate_username") or "").lower().lstrip("@") != str(
                self.scope.candidate_username or ""
            ).lower().lstrip("@"):
                raise ValueError("ledger_like_verified_candidate_binding_mismatch")
            if str(payload.get("action_id") or "") != str(self.scope.action_id or ""):
                raise ValueError("ledger_like_verified_action_binding_mismatch")
            if not str(payload.get("stable_proof_hash") or "") or not str(
                payload.get("media_binding") or ""
            ):
                raise ValueError("ledger_like_verified_media_binding_missing")
        payload_hash = self._payload_hash(payload)
        key = self.scope.idempotency_key(stage)
        existing = self.receipts.get(stage)
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise ValueError(f"ledger_duplicate_conflict:{stage}")
            return {
                "ok": True,
                "inserted": False,
                "duplicate": True,
                "stage": stage,
                "cycle_complete": self.cycle_complete,
                "idempotency_key": key,
            }
        self._validate_transition(stage)
        self.receipts[stage] = LedgerReceipt(
            action_type=stage,
            payload_hash=payload_hash,
            idempotency_key=key,
        )
        self.stages.add(stage)
        if stage == "stop_recorded":
            self.stopped = True
        return {
            "ok": True,
            "inserted": True,
            "duplicate": False,
            "stage": stage,
            "cycle_complete": self.cycle_complete,
            "idempotency_key": key,
        }

    def acknowledge(self, stage: str) -> dict[str, Any]:
        receipt = self.receipts.get(stage)
        if receipt is None:
            raise ValueError(f"ledger_receipt_missing:{stage}")
        if receipt.acknowledged:
            return {"ok": True, "duplicate": True, "stage": stage}
        receipt.acknowledged = True
        return {"ok": True, "duplicate": False, "stage": stage}

    @property
    def cycle_complete(self) -> bool:
        required = {
            "profile_certified",
            "post_opened",
            "profile_reentry_verified",
            "follow_verified",
            "mute_posts_verified",
            "mute_stories_verified",
            "return_ct_exact",
        }
        return bool(
            required.issubset(self.stages)
            and {"like_verified", "like_skipped"} & self.stages
            and "follow_failed" not in self.stages
            and "cycle_complete" in self.stages
        )

    def next_stage(self) -> str:
        if "follow_failed" in self.stages:
            return "follow_failed_terminal"
        sequence = (
            "profile_certified",
            "post_opened",
            "like_terminal",
            "profile_reentry_verified",
            "follow_pending",
            "follow_verified",
            "mute_posts_verified",
            "mute_stories_verified",
            "return_ct_exact",
        )
        for stage in sequence:
            if stage == "like_terminal":
                if not ({"like_verified", "like_skipped"} & self.stages):
                    return "like"
            elif stage not in self.stages:
                return stage
        return "cycle_complete"

    def replay_plan(self) -> dict[str, Any]:
        return {
            "ordering_version": self.scope.ordering_version,
            "next_stage": self.next_stage(),
            "like_replay_allowed": not bool({"like_verified", "like_skipped"} & self.stages),
            "follow_invented": False,
            "mute_invented": False,
            "cycle_complete": self.cycle_complete,
            "stopped": self.stopped,
        }


class DurableOrderingLedgerV1:
    """SQLite WAL receipt store; every acknowledged physical stage is fsynced.

    The table is local runtime state, not a production Supabase migration.  A
    later composite outbox flush may project the same idempotency keys, while
    this store prevents a crash/restart from replaying Like or Follow.
    """

    def __init__(self, scope: LedgerScope, *, path: Path = DEFAULT_PATH) -> None:
        scope.validate()
        self.scope = scope
        self.path = Path(path)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=3.0)
        conn.execute("pragma journal_mode=WAL")
        conn.execute("pragma synchronous=FULL")
        conn.execute("pragma foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with _LOCK, self._connect() as conn:
            conn.execute(
                """
                create table if not exists follow60_ordering_v2_receipts (
                  ordering_version text not null,
                  account_id text not null,
                  run_id text not null,
                  request_id text not null,
                  business_session_id text not null,
                  target_id text not null,
                  action_id text not null,
                  candidate_username text not null,
                  stage text not null,
                  idempotency_key text not null,
                  payload_hash text not null,
                  payload_json text not null,
                  acknowledged integer not null check (acknowledged in (0, 1)),
                  created_at_monotonic real not null,
                  primary key (account_id, run_id, action_id, stage),
                  unique (idempotency_key)
                )
                """
            )
            conn.commit()
        for item in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                if item.exists():
                    os.chmod(item, 0o600)
            except OSError:
                pass

    def _rows(self) -> list[sqlite3.Row]:
        with _LOCK, self._connect() as conn:
            conn.row_factory = sqlite3.Row
            return list(
                conn.execute(
                    """
                    select stage, idempotency_key, payload_hash, payload_json,
                           acknowledged
                    from follow60_ordering_v2_receipts
                    where account_id=? and run_id=? and action_id=?
                    order by created_at_monotonic, rowid
                    """,
                    (self.scope.account_id, self.scope.run_id, self.scope.action_id),
                ).fetchall()
            )

    def load(self) -> OrderingLedger:
        ledger = OrderingLedger(self.scope)
        for row in self._rows():
            stage = str(row["stage"])
            payload = json.loads(str(row["payload_json"] or "{}"))
            out = ledger.apply_receipt(stage, payload)
            if str(out.get("idempotency_key")) != str(row["idempotency_key"]):
                raise ValueError("ledger_idempotency_key_mismatch")
            if ledger.receipts[stage].payload_hash != str(row["payload_hash"]):
                raise ValueError("ledger_payload_hash_mismatch")
            if bool(row["acknowledged"]):
                ledger.acknowledge(stage)
        return ledger

    def apply_receipt(self, stage: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        clean = dict(payload or {})
        for forbidden in ("xml", "hierarchy", "screenshot", "token", "secret"):
            clean.pop(forbidden, None)
        with _LOCK:
            ledger = self.load()
            result = ledger.apply_receipt(stage, clean)
            receipt = ledger.receipts[stage]
            payload_json = json.dumps(
                clean, sort_keys=True, separators=(",", ":"), default=str
            )
            with self._connect() as conn:
                existing = conn.execute(
                    """
                    select payload_hash from follow60_ordering_v2_receipts
                    where account_id=? and run_id=? and action_id=? and stage=?
                    """,
                    (
                        self.scope.account_id,
                        self.scope.run_id,
                        self.scope.action_id,
                        stage,
                    ),
                ).fetchone()
                if existing is not None:
                    if str(existing[0]) != receipt.payload_hash:
                        raise ValueError(f"ledger_duplicate_conflict:{stage}")
                    return {**result, "durable": True}
                conn.execute(
                    """
                    insert into follow60_ordering_v2_receipts (
                      ordering_version, account_id, run_id, request_id,
                      business_session_id, target_id, action_id,
                      candidate_username, stage, idempotency_key, payload_hash,
                      payload_json, acknowledged, created_at_monotonic
                    ) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        self.scope.ordering_version,
                        self.scope.account_id,
                        self.scope.run_id,
                        self.scope.request_id,
                        self.scope.business_session_id,
                        self.scope.target_id,
                        self.scope.action_id,
                        self.scope.candidate_username,
                        stage,
                        receipt.idempotency_key,
                        receipt.payload_hash,
                        payload_json,
                        0,
                        time.monotonic(),
                    ),
                )
                conn.commit()
            return {**result, "durable": True}

    def acknowledge(self, stage: str) -> dict[str, Any]:
        with _LOCK:
            ledger = self.load()
            result = ledger.acknowledge(stage)
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    update follow60_ordering_v2_receipts set acknowledged=1
                    where account_id=? and run_id=? and action_id=? and stage=?
                    """,
                    (
                        self.scope.account_id,
                        self.scope.run_id,
                        self.scope.action_id,
                        stage,
                    ),
                )
                conn.commit()
            if cur.rowcount != 1:
                raise ValueError(f"ledger_receipt_missing:{stage}")
            return {**result, "durable": True}

    def replay_plan(self) -> dict[str, Any]:
        return self.load().replay_plan()


def register_active_store(store: DurableOrderingLedgerV1) -> None:
    key = (
        store.scope.account_id,
        store.scope.run_id,
        store.scope.action_id,
    )
    with _LOCK:
        _ACTIVE_STORES[key] = store


def clear_active_store(store: DurableOrderingLedgerV1) -> None:
    key = (
        store.scope.account_id,
        store.scope.run_id,
        store.scope.action_id,
    )
    with _LOCK:
        _ACTIVE_STORES.pop(key, None)


def record_stop_for_run(*, account_id: str, run_id: str, reason: str) -> dict[str, Any]:
    """Persist Stop for every in-flight V2 action without inventing a stage."""

    with _LOCK:
        stores = [
            store
            for (bound_account, bound_run, _action), store in _ACTIVE_STORES.items()
            if bound_account == str(account_id or "") and bound_run == str(run_id or "")
        ]
    recorded = 0
    failures: list[str] = []
    for store in stores:
        try:
            store.apply_receipt(
                "stop_recorded",
                {"reason": str(reason or "manual_stop"), "source": "runner_signal"},
            )
            recorded += 1
        except Exception as exc:
            failures.append(type(exc).__name__)
    return {
        "ok": not failures,
        "active_count": len(stores),
        "recorded": recorded,
        "failure_types": failures,
    }
