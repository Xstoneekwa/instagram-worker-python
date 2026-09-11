"""Durable, recoverable Worker promotion state machine.

The machine deliberately knows nothing about Instagram or devices.  Runtime,
launchd, registry, token and admission operations are supplied by an adapter so
the same transition contract can be exercised with isolated fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Protocol


SCHEMA = "PHONE_FARM_WORKER_PROMOTION_TRANSACTION_V1"
SERVICES = ("dispatcher", "heartbeat", "notifier")
PRE_COMMIT_PHASES = (
    "PREPARE",
    "ACQUIRE_TRANSACTION",
    "FENCE_ADMISSION",
    "DRAIN_NATURALLY",
    "ZERO_GATE",
    "VERIFY_AND_PIN_OLD_AND_NEW",
    "QUIESCE_OLD_SERVICES",
    "SWITCH_EXECUTION_POINTER",
    "START_NEW_SERVICES_FENCED",
    "VERIFY_THREE_SERVICES_AND_TOKEN",
)
COMMIT_PHASE = "PUBLISH_COMMIT"
POST_COMMIT_PHASES = ("RECONCILE_PROJECTIONS", "FINISH_AND_UNFENCE", "TRANSACTION_FINISHED", "COMMITTED_FINALIZATION_REQUIRED", "PROTECTED_RECOVERY_REQUIRED")
ROLLBACK_PHASES = (
    "ROLLBACK_PREPARE",
    "ROLLBACK_QUIESCE",
    "ROLLBACK_SWITCH_POINTER",
    "ROLLBACK_START_OLD_SERVICES",
    "ROLLBACK_VERIFY",
    "ROLLBACK_RESTORE_PROJECTIONS",
    "ROLLBACK_FINISH_AND_UNFENCE",
)
TERMINAL_PHASES = ("PROMOTED", "ROLLED_BACK")
ALL_PHASES = PRE_COMMIT_PHASES + (COMMIT_PHASE,) + POST_COMMIT_PHASES + ROLLBACK_PHASES + TERMINAL_PHASES


class PromotionFailure(RuntimeError):
    pass


class SimulatedHardCrash(BaseException):
    """Test-only crash signal: bypasses rollback like SIGKILL would."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_json(path: Path, value: dict[str, Any]) -> None:
    """Publish one complete JSON generation with file and directory fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".pending", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_journal(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise PromotionFailure("promotion_journal_schema_invalid")
    if value.get("phase") not in ALL_PHASES:
        raise PromotionFailure("promotion_journal_phase_invalid")
    expected_state_hash = value.get("state_sha256")
    state_body = {key: item for key, item in value.items() if key != "state_sha256"}
    if expected_state_hash != sha256_json(state_body):
        raise PromotionFailure("promotion_journal_state_hash_invalid")
    history = value.get("history")
    if not isinstance(history, list) or not history:
        raise PromotionFailure("promotion_journal_history_missing")
    previous = "0" * 64
    for event in history:
        if not isinstance(event, dict) or event.get("previous_event_sha256") != previous:
            raise PromotionFailure("promotion_journal_hash_chain_invalid")
        body = {key: item for key, item in event.items() if key != "event_sha256"}
        actual = sha256_json(body)
        if event.get("event_sha256") != actual:
            raise PromotionFailure("promotion_journal_event_hash_invalid")
        previous = actual
    if value.get("last_event_sha256") != previous:
        raise PromotionFailure("promotion_journal_tail_hash_invalid")
    return value


@dataclass(frozen=True)
class PromotionPlan:
    transaction_id: str
    candidate_sha: str
    candidate_tree: str
    candidate_release: str
    old_active_sha: str
    old_active_release: str
    previous_rollback_sha: str
    previous_rollback_release: str
    controller_identity: dict[str, Any]
    launchd_identity: dict[str, Any]
    registry_pre_state: dict[str, Any]
    runtime_pointer_pre_state: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "candidate": {"sha": self.candidate_sha, "tree": self.candidate_tree, "release": self.candidate_release},
            "old_active": {"sha": self.old_active_sha, "release": self.old_active_release},
            "previous_rollback": {"sha": self.previous_rollback_sha, "release": self.previous_rollback_release},
            "controller_identity": self.controller_identity,
            "launchd_identity": self.launchd_identity,
            "expected_service_identities": {
                service: {"module_root": self.candidate_release, "release_sha": self.candidate_sha}
                for service in SERVICES
            },
            "registry_pre_state": self.registry_pre_state,
            "runtime_pointer_pre_state": self.runtime_pointer_pre_state,
        }


class PromotionAdapter(Protocol):
    def acquire_transaction(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def acquire_fence(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def assert_fence(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def fence_state(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def drain_naturally(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def zero_gate(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def verify_and_pin(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def quiesce(self, service: str, plan: PromotionPlan) -> dict[str, Any]: ...
    def switch_pointer(self, release: str, plan: PromotionPlan) -> dict[str, Any]: ...
    def prepare_token(self, *, phase: str, release_sha: str, plan: PromotionPlan) -> dict[str, Any]: ...
    def start(self, service: str, release: str, plan: PromotionPlan) -> dict[str, Any]: ...
    def verify_services_and_token(self, release: str, release_sha: str, token: dict[str, Any], plan: PromotionPlan) -> dict[str, Any]: ...
    def archive_token_evidence(self, token: dict[str, Any], plan: PromotionPlan) -> dict[str, Any]: ...
    def publish_generation(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def committed_generation_state(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def reconcile_projections(self, plan: PromotionPlan, *, promoted: bool) -> dict[str, Any]: ...
    def restore_precommit_generation(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def verify_terminal(self, plan: PromotionPlan, *, promoted: bool, token: dict[str, Any]) -> dict[str, Any]: ...
    def release_fence(self, plan: PromotionPlan) -> dict[str, Any]: ...
    def finish_transaction(self, plan: PromotionPlan, *, promoted: bool, evidence: dict[str, Any]) -> dict[str, Any]: ...


class DurablePromotion:
    def __init__(self, journal_path: Path, plan: PromotionPlan, adapter: PromotionAdapter):
        self.path = Path(journal_path)
        self.plan = plan
        self.adapter = adapter
        self.state: dict[str, Any]

    def create(self) -> dict[str, Any]:
        if self.path.exists() or self.path.is_symlink():
            raise PromotionFailure("promotion_journal_already_exists")
        self.state = {
            "schema": SCHEMA,
            **self.plan.as_dict(),
            "phase": "PREPARE",
            "commit_published": False,
            "admission_fence": {"active": False},
            "startup_tokens": {},
            "service_state": {},
            "timestamps": {"created_at": utc_now(), "updated_at": utc_now()},
            "last_error": None,
            "recovery_status": "FORWARD",
            "history": [],
            "last_event_sha256": "0" * 64,
        }
        self._transition("PREPARE", {"plan_sha256": sha256_json(self.plan.as_dict())})
        return self.state

    def load(self) -> dict[str, Any]:
        self.state = read_journal(self.path)
        if self.state.get("transaction_id") != self.plan.transaction_id:
            raise PromotionFailure("promotion_journal_transaction_mismatch")
        if self.state.get("candidate", {}).get("sha") != self.plan.candidate_sha:
            raise PromotionFailure("promotion_journal_candidate_mismatch")
        if self.state["history"][0].get("evidence", {}).get("plan_sha256") != sha256_json(self.plan.as_dict()):
            raise PromotionFailure("promotion_journal_plan_hash_mismatch")
        return self.state

    def _persist(self) -> None:
        self.state.pop("state_sha256", None)
        self.state["state_sha256"] = sha256_json(self.state)
        durable_json(self.path, self.state)

    def _transition(self, phase: str, evidence: dict[str, Any] | None = None) -> None:
        if phase not in ALL_PHASES:
            raise PromotionFailure("promotion_phase_unknown")
        previous = self.state.get("last_event_sha256", "0" * 64)
        body = {
            "sequence": len(self.state["history"]) + 1,
            "phase": phase,
            "at": utc_now(),
            "previous_event_sha256": previous,
            "evidence": evidence or {},
        }
        event = {**body, "event_sha256": sha256_json(body)}
        self.state["history"].append(event)
        self.state["last_event_sha256"] = event["event_sha256"]
        self.state["phase"] = phase
        self.state["timestamps"]["updated_at"] = event["at"]
        self._persist()

    @staticmethod
    def _require(result: dict[str, Any], reason: str) -> dict[str, Any]:
        if not isinstance(result, dict) or result.get("ok") is not True:
            detail = result.get("reason") if isinstance(result, dict) else type(result).__name__
            raise PromotionFailure(f"{reason}:{detail or 'failed'}")
        return result

    def _step(self, phase: str, operation, *args, **kwargs) -> dict[str, Any]:
        result = self._require(operation(*args, **kwargs), phase.lower() + "_failed")
        self._transition(phase, result)
        return result

    def run(self) -> dict[str, Any]:
        self.create()
        try:
            self._step("ACQUIRE_TRANSACTION", self.adapter.acquire_transaction, self.plan)
            fence = self._step("FENCE_ADMISSION", self.adapter.acquire_fence, self.plan)
            self.state["admission_fence"] = {**fence, "active": True}
            self._persist()
            self._step("DRAIN_NATURALLY", self.adapter.drain_naturally, self.plan)
            self._step("ZERO_GATE", self.adapter.zero_gate, self.plan)
            self._step("VERIFY_AND_PIN_OLD_AND_NEW", self.adapter.verify_and_pin, self.plan)
            self._require(self.adapter.assert_fence(self.plan), "admission_fence_lost")
            for service in SERVICES:
                result = self._require(self.adapter.quiesce(service, self.plan), f"quiesce_{service}_failed")
                self.state["service_state"][service] = "QUIESCED"
                self._transition("QUIESCE_OLD_SERVICES", {"service": service, **result})
            self._step("SWITCH_EXECUTION_POINTER", self.adapter.switch_pointer, self.plan.candidate_release, self.plan)
            token = self._require(
                self.adapter.prepare_token(phase="PROMOTE", release_sha=self.plan.candidate_sha, plan=self.plan),
                "candidate_token_prepare_failed",
            )
            self.state["startup_tokens"]["PROMOTE"] = token
            self._persist()
            for service in SERVICES:
                result = self._require(self.adapter.start(service, self.plan.candidate_release, self.plan), f"start_new_{service}_failed")
                self.state["service_state"][service] = "NEW"
                self._transition("START_NEW_SERVICES_FENCED", {"service": service, **result})
            verification = self._require(
                self.adapter.verify_services_and_token(
                    self.plan.candidate_release, self.plan.candidate_sha, token, self.plan
                ),
                "new_service_verification_failed",
            )
            archived = self._require(self.adapter.archive_token_evidence(token, self.plan), "candidate_token_archive_failed")
            self.state["startup_tokens"]["PROMOTE_ARCHIVED"] = archived
            self._transition("VERIFY_THREE_SERVICES_AND_TOKEN", verification)
            commit = self._step("PUBLISH_COMMIT", self.adapter.publish_generation, self.plan)
            self.state["commit_published"] = True
            self.state["committed_generation"] = commit
            self._persist()
            return self._finalize_forward(token)
        except SimulatedHardCrash:
            raise
        except BaseException as exc:
            self._record_error(exc)
            observed = self.adapter.committed_generation_state(self.plan)
            if observed.get("committed") is True:
                self.state["commit_published"] = True
                self.state["committed_generation"] = observed
                self._persist()
                return self._recover_post_commit()
            if observed.get("old") is not True:
                return self._protect_recovery("canonical_commit_state_ambiguous")
            return self._rollback()

    def recover(self) -> dict[str, Any]:
        self.load()
        if self.state["phase"] in TERMINAL_PHASES:
            return {"ok": self.state["phase"] != "PROTECTED_RECOVERY_REQUIRED", "status": self.state["phase"], "idempotent": True}
        self.state["recovery_status"] = "RECOVERING"
        self._persist()
        try:
            self._require(self.adapter.acquire_transaction(self.plan), "transaction_recovery_failed")
            observed = self.adapter.committed_generation_state(self.plan)
            if observed.get("committed") is True:
                self.state["commit_published"] = True
                self.state["committed_generation"] = observed
                self._persist()
                return self._recover_post_commit()
            if observed.get("old") is not True:
                return self._protect_recovery("canonical_commit_state_ambiguous")
            fence_state = self._require(self.adapter.fence_state(self.plan), "recovery_fence_missing")
            if fence_state.get("active") is not True and fence_state.get("terminal_state") != "ROLLED_BACK":
                return self._protect_recovery("recovery_fence_state_invalid")
            if self.state.get("transaction_finished") or fence_state.get("terminal_state") == "ROLLED_BACK":
                return self._finalize_rollback()
            return self._rollback()
        except SimulatedHardCrash:
            raise
        except BaseException as exc:
            self._record_error(exc)
            return self._protect_recovery("recovery_failed")

    def _finalize_forward(self, token: dict[str, Any]) -> dict[str, Any]:
        fence_state = self._require(self.adapter.fence_state(self.plan), "fence_missing_after_commit")
        if fence_state.get("active") is not True and fence_state.get("terminal_state") != "PROMOTED":
            raise PromotionFailure("postcommit_fence_state_invalid")
        projection = self._step("RECONCILE_PROJECTIONS", self.adapter.reconcile_projections, self.plan, promoted=True)
        terminal = self._require(
            self.adapter.verify_terminal(self.plan, promoted=True, token=token), "terminal_new_verification_failed"
        )
        finish, terminal = self._finish_then_unfence(promoted=True, terminal=terminal)
        self._transition("FINISH_AND_UNFENCE", {"projection": projection, "terminal": terminal, "finish": finish})
        self._transition("PROMOTED", terminal)
        return {"ok": True, "status": "PROMOTED", "transaction_id": self.plan.transaction_id}

    def _recover_post_commit(self) -> dict[str, Any]:
        token = self.state.get("startup_tokens", {}).get("PROMOTE") or {}
        try:
            return self._finalize_forward(token)
        except SimulatedHardCrash:
            raise
        except BaseException as exc:
            self._record_error(exc)
            return self._protect_recovery("post_commit_finalization_failed", committed=True)

    def _rollback(self) -> dict[str, Any]:
        self.state["recovery_status"] = "ROLLING_BACK"
        self._transition("ROLLBACK_PREPARE", {"cause": self.state.get("last_error")})
        try:
            fence_state = self._require(self.adapter.fence_state(self.plan), "rollback_fence_missing")
            if fence_state.get("active") is not True and fence_state.get("terminal_state") != "ROLLED_BACK":
                raise PromotionFailure("rollback_fence_state_invalid")
            self._require(self.adapter.zero_gate(self.plan), "rollback_requires_drained_runtime")
            candidate_token = self.state.get("startup_tokens", {}).get("PROMOTE")
            if candidate_token and not self.state.get("startup_tokens", {}).get("PROMOTE_ARCHIVED"):
                archived = self._require(self.adapter.archive_token_evidence(candidate_token, self.plan), "rollback_candidate_token_archive_failed")
                self.state["startup_tokens"]["PROMOTE_ARCHIVED"] = archived
                self._persist()
            for service in SERVICES:
                result = self._require(self.adapter.quiesce(service, self.plan), f"rollback_quiesce_{service}_failed")
                self._transition("ROLLBACK_QUIESCE", {"service": service, **result})
            self._step("ROLLBACK_SWITCH_POINTER", self.adapter.switch_pointer, self.plan.old_active_release, self.plan)
            rollback_token = self._require(
                self.adapter.prepare_token(phase="ROLLBACK", release_sha=self.plan.old_active_sha, plan=self.plan),
                "rollback_token_prepare_failed",
            )
            if candidate_token and rollback_token.get("token_id") == candidate_token.get("token_id"):
                raise PromotionFailure("rollback_token_reused_candidate_token")
            self.state["startup_tokens"]["ROLLBACK"] = rollback_token
            self._persist()
            for service in SERVICES:
                result = self._require(self.adapter.start(service, self.plan.old_active_release, self.plan), f"rollback_start_{service}_failed")
                self._transition("ROLLBACK_START_OLD_SERVICES", {"service": service, **result})
            verification = self._require(
                self.adapter.verify_services_and_token(
                    self.plan.old_active_release, self.plan.old_active_sha, rollback_token, self.plan
                ),
                "rollback_service_verification_failed",
            )
            self._transition("ROLLBACK_VERIFY", verification)
            projection = self._step("ROLLBACK_RESTORE_PROJECTIONS", self.adapter.restore_precommit_generation, self.plan)
            return self._finalize_rollback()
        except SimulatedHardCrash:
            raise
        except BaseException as exc:
            self._record_error(exc)
            return self._protect_recovery("automatic_rollback_failed")

    def _finish_then_unfence(self, *, promoted: bool, terminal: dict[str, Any]):
        # Persist validated terminal evidence while admissions are still closed.
        # A completed lease can idempotently validate an already archived finish
        # after a crash between the external archive and this journal write.
        finish = self._require(
            self.adapter.finish_transaction(self.plan, promoted=promoted, evidence=terminal),
            "transaction_finish_failed",
        )
        self.state["transaction_finished"] = {"promoted": promoted, "evidence": finish}
        self._transition("TRANSACTION_FINISHED", {"promoted": promoted, "finish": finish})
        released = self._require(self.adapter.release_fence(self.plan), "terminal_unfence_failed")
        if released.get("active") is not False:
            raise PromotionFailure("terminal_fence_still_active")
        self.state["admission_fence"]["active"] = False
        final_receipt = self._require(
            self.adapter.complete_finalization(self.plan, promoted=promoted,
                                               evidence={"finish": finish, "fence_release": released}),
            "finalization_receipt_failed",
        )
        return finish, {**terminal, "fence_release": released, "final_receipt": final_receipt}

    def _finalize_rollback(self) -> dict[str, Any]:
        token = self.state.get("startup_tokens", {}).get("ROLLBACK") or {}
        terminal = self._require(
            self.adapter.verify_terminal(self.plan, promoted=False, token=token),
            "terminal_old_verification_failed",
        )
        finish, terminal = self._finish_then_unfence(promoted=False, terminal=terminal)
        self._transition("ROLLBACK_FINISH_AND_UNFENCE", {"terminal": terminal, "finish": finish})
        self.state["recovery_status"] = "ROLLED_BACK"
        self._transition("ROLLED_BACK", terminal)
        return {"ok": False, "status": "ROLLED_BACK", "transaction_id": self.plan.transaction_id}

    def _record_error(self, exc: BaseException) -> None:
        self.state["last_error"] = {
            "type": type(exc).__name__,
            "reason": str(exc)[:500],
            "phase": self.state.get("phase"),
            "at": utc_now(),
        }
        self._persist()

    def _protect_recovery(self, reason: str, *, committed: bool = False) -> dict[str, Any]:
        # Report physical evidence, never manufacture an active-fence boolean.
        try:
            fence = self.adapter.fence_state(self.plan)
            active = fence.get("active") if fence.get("ok") is True else None
        except Exception:
            active = None
        if active is not True and not self.state.get("transaction_finished"):
            try:
                self._require(self.adapter.acquire_transaction(self.plan), "protection_transaction_unavailable")
                reacquired = self.adapter.acquire_fence(self.plan)
                active = reacquired.get("active") if reacquired.get("ok") is True else active
            except Exception:
                pass  # UNKNOWN remains UNKNOWN; no invented protection proof.
        status = "COMMITTED_FINALIZATION_REQUIRED" if committed else "PROTECTED_RECOVERY_REQUIRED"
        self.state["recovery_status"] = status
        self._transition(status, {"reason": reason, "admission_fence_active": active})
        return {"ok": False, "status": status, "reason": reason,
                "transaction_id": self.plan.transaction_id, "admission_fence_active": active}
