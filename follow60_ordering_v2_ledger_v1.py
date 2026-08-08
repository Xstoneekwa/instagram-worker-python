"""Dormant source contract for a durable Like-before-Follow ledger.

The production runtime does not import this module.  It specifies and tests the
state machine that a future explicit Behavioral V2 implementation must use.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping


ORDERING_VERSION = "FOLLOW60_ORDERING_V2_LEDGER_V1"
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
    "stop_recorded",
)


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
        elif stage == "stop_recorded":
            return
        else:
            raise ValueError("ledger_action_type_invalid")

    def apply_receipt(self, stage: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.scope.validate()
        if stage not in STAGES:
            raise ValueError("ledger_action_type_invalid")
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
