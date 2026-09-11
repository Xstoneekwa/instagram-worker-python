"""Production adapter for the durable Worker promotion state machine.

This module is release machinery only. Importing it performs no runtime,
service, registry, Supabase or device operation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid
from typing import Any

from promotion_admission_fence_v1 import SupabasePromotionFence
from promotion_transaction_v1 import DurablePromotion, PromotionFailure, PromotionPlan, SERVICES, canonical_bytes, read_journal
from startup_tick_skip_token import (
    CANCELLATION_RECEIPT_SCHEMA,
    CONSUMPTION_RECEIPT_SCHEMA,
    TOKEN_FILENAME,
    cancel_prepared_startup_skip_token,
    consumption_receipt_path,
    prepare_startup_skip_token,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(result: dict[str, Any], reason: str) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise PromotionFailure(f"{reason}:{(result or {}).get('reason', 'failed') if isinstance(result, dict) else type(result).__name__}")
    return result


def _durable_registry_replace(path: Path, payload: dict[str, Any], expected_worker: dict[str, Any]) -> dict[str, Any]:
    """Worker-component CAS that preserves the freshest unrelated components."""
    path = path.expanduser().resolve(strict=True)
    fresh = json.loads(path.read_text(encoding="utf-8"))
    if fresh.get("schema") != "PHONE_FARM_CANONICAL_DELTA_REGISTRY_V1":
        raise PromotionFailure("canonical_registry_schema_mismatch")
    worker = ((fresh.get("components") or {}).get("worker") or {})
    if worker.get("production") != expected_worker:
        raise PromotionFailure("canonical_worker_generation_compare_and_swap_mismatch")
    # Only the Worker generation pair belongs to this commit point. Preserve
    # fresher deltas and any parallel metadata written under the Worker
    # component after the plan snapshot was captured.
    if not isinstance(payload.get("production"), dict) or not isinstance(payload.get("rollback"), dict):
        raise PromotionFailure("canonical_worker_generation_payload_invalid")
    fresh["components"]["worker"] = {
        **worker,
        "production": payload["production"],
        "rollback": payload["rollback"],
    }
    info = path.stat()
    staged = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.pending")
    try:
        with staged.open("xb") as handle:
            handle.write(json.dumps(fresh, indent=2, sort_keys=False).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(staged, stat.S_IMODE(info.st_mode))
        if os.geteuid() == 0:
            os.chown(staged, info.st_uid, info.st_gid)
        os.replace(staged, path)
        fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        staged.unlink(missing_ok=True)
    return fresh


class PhoneFarmPromotionAdapter:
    def __init__(
        self,
        *,
        controller: Any,
        transaction: Any,
        paths: Any,
        validation: Any,
        integrity: dict[str, Any],
        evidence: dict[str, Any],
        plan: PromotionPlan,
    ):
        self.c = controller
        self.transaction = transaction
        self.paths = paths
        self.validation = validation
        self.integrity = integrity
        self.evidence = evidence
        self.plan = plan
        secrets = controller._runtime_secret_env(paths)
        self.fence = SupabasePromotionFence(secrets.get("SUPABASE_URL", ""), secrets.get("SUPABASE_SERVICE_ROLE_KEY", ""))
        self.service_context = dict(plan.launchd_identity)
        self.token_path = paths.run_dir / "run-control-dispatcher" / TOKEN_FILENAME
        self.last_token_verification: dict[str, Any] = {}
        self.final_receipt: Path | None = None
        self.final_receipt_sha256 = ""

    def acquire_transaction(self, plan: PromotionPlan) -> dict[str, Any]:
        proof = self.transaction.recheck()
        return {"ok": True, "external_lock": proof, "transaction_id": plan.transaction_id}

    def acquire_fence(self, plan: PromotionPlan) -> dict[str, Any]:
        return self.fence.acquire(transaction_id=plan.transaction_id, candidate_sha=plan.candidate_sha)

    def assert_fence(self, plan: PromotionPlan) -> dict[str, Any]:
        return self.fence.inspect(transaction_id=plan.transaction_id)

    def fence_state(self, plan: PromotionPlan) -> dict[str, Any]:
        return self.fence.inspect_state(transaction_id=plan.transaction_id)

    def drain_naturally(self, plan: PromotionPlan) -> dict[str, Any]:
        timeout = float(os.environ.get("PHONEFARM_PROMOTION_DRAIN_TIMEOUT_SECONDS", "900"))
        deadline = time.monotonic() + max(0.0, timeout)
        observations = 0
        while True:
            self.assert_fence(plan)
            gate = self.c.deployment_zero_gate(self.paths)
            observations += 1
            if gate.get("ok") is True:
                return {"ok": True, "status": "NATURAL_DRAIN_COMPLETE", "observations": observations, "counts": gate.get("counts")}
            if gate.get("status") == "deployment_gate_unavailable":
                raise PromotionFailure(str(gate.get("reason") or "drain_gate_unavailable"))
            if time.monotonic() >= deadline:
                raise PromotionFailure("natural_drain_timeout")
            time.sleep(0.5)

    def zero_gate(self, plan: PromotionPlan) -> dict[str, Any]:
        self.assert_fence(plan)
        gate = self.c.deployment_zero_gate(self.paths)
        counts = gate.get("counts") or {}
        required = {"account_run_requests", "ig_runs", "auto_restart_device_locks", "auto_restart_tick_locks"}
        if gate.get("ok") is not True or set(counts) != required or any(type(value) is not int or value != 0 for value in counts.values()):
            raise PromotionFailure("post_fence_zero_gate_failed")
        return {"ok": True, "status": "POST_FENCE_ZERO_GATE_PASS", "counts": counts}

    def verify_and_pin(self, plan: PromotionPlan) -> dict[str, Any]:
        self.transaction.recheck()
        candidate = self.c.verify_deployment_candidate(Path(plan.candidate_release))
        if candidate.get("ok") is not True or candidate.get("candidate_identity", {}).get("candidate_commit_sha") != plan.candidate_sha:
            raise PromotionFailure("candidate_identity_changed")
        from follow60_external_deployment_lock_v2 import exact_admission, verify_seal
        old = exact_admission(Path(plan.old_active_release))
        verify_seal(Path(plan.old_active_release))
        if old.get("candidate_commit_sha") != plan.old_active_sha:
            raise PromotionFailure("old_release_identity_changed")
        registry = self.c._read_json_object(self.c._canonical_registry_path())
        production = registry["components"]["worker"]["production"]
        if production != plan.registry_pre_state["worker_production"]:
            raise PromotionFailure("registry_worker_pre_state_changed")
        if self.paths.current_link.resolve() != Path(plan.old_active_release).resolve():
            raise PromotionFailure("runtime_pointer_pre_state_changed")
        return {"ok": True, "candidate": candidate.get("candidate_identity"), "old": old, "registry_sha256": _sha256(self.c._canonical_registry_path())}

    def quiesce(self, service: str, plan: PromotionPlan) -> dict[str, Any]:
        self.assert_fence(plan)
        value = self.c._run_canonical_controller(
            service, "pause", paths=self.paths, timeout=60, expected_context=self.service_context
        )
        if value.get("ok") is not True:
            return value
        deadline = time.monotonic() + 15.0
        snapshot = []
        while True:
            snapshot = self.c._canonical_component_process_convergence(
                component=service,
                paths=self.paths,
                target_root=Path(plan.old_active_release),
                target_sha=plan.old_active_sha,
            )[0]
            if not snapshot:
                break
            if time.monotonic() >= deadline:
                return {"ok": False, "reason": "quiesced_service_still_running", "service": service, "processes": snapshot}
            time.sleep(0.1)
        return {"ok": True, "status": "SERVICE_QUIESCED", "service": service, "launchd": self.service_context}

    def switch_pointer(self, release: str, plan: PromotionPlan) -> dict[str, Any]:
        self.assert_fence(plan)
        self.transaction.recheck()
        self.c._atomic_switch_runtime_root(self.paths.current_link, release)
        if self.paths.current_link.resolve() != Path(release).resolve():
            raise PromotionFailure("runtime_pointer_switch_not_durable")
        return {"ok": True, "runtime_root": str(self.paths.current_link.resolve())}

    def prepare_token(self, *, phase: str, release_sha: str, plan: PromotionPlan) -> dict[str, Any]:
        root = plan.candidate_release if phase == "PROMOTE" else plan.old_active_release
        prepared = prepare_startup_skip_token(
            self.token_path,
            self.paths.runtime_home,
            {
                "transaction_id": plan.transaction_id,
                "phase": phase,
                "candidate_sha": release_sha,
                "dispatcher_module_root": root,
            },
        )
        token = dict(prepared["token"])
        return {"ok": True, **token, "preparation_status": prepared["status"]}

    def start(self, service: str, release: str, plan: PromotionPlan) -> dict[str, Any]:
        self.assert_fence(plan)
        if self.paths.current_link.resolve() != Path(release).resolve():
            raise PromotionFailure("service_start_pointer_mismatch")
        return self.c._run_canonical_controller(
            service, "resume", paths=self.paths, timeout=60, expected_context=self.service_context
        )

    def verify_services_and_token(self, release: str, release_sha: str, token: dict[str, Any], plan: PromotionPlan) -> dict[str, Any]:
        fence = self.fence_state(plan)
        expected_terminal = "PROMOTED" if release_sha == plan.candidate_sha else "ROLLED_BACK"
        if fence.get("active") is not True and fence.get("terminal_state") != expected_terminal:
            return {"ok": False, "reason": "service_verification_fence_state_invalid"}
        try:
            current_context = self.c._promotion_service_context(self.paths)
        except Exception as exc:
            return {"ok": False, "reason": f"launchd_domain_probe_failed:{type(exc).__name__}"}
        if current_context != {key: self.service_context[key] for key in ("uid", "gid", "user", "home", "launchd_domain")}:
            return {"ok": False, "reason": "launchd_domain_identity_changed"}
        value = self.c._wait_for_service_convergence(
            paths=self.paths,
            expected_root=release,
            expected_sha=release_sha,
            expected_startup_token=token,
            timeout_seconds=float(os.environ.get("PHONEFARM_PROMOTION_CONVERGENCE_TIMEOUT_SECONDS", "45")),
        )
        if value.get("ok") is not True:
            return value
        if set(value.get("services") or {}) != set(SERVICES):
            return {"ok": False, "reason": "service_set_incomplete"}
        for service, proof in value["services"].items():
            processes = proof.get("processes") or []
            if proof.get("ok") is not True or proof.get("process_count") != 1 or len(processes) != 1:
                return {"ok": False, "reason": f"{service}_cardinality_invalid"}
            process = processes[0]
            if process.get("module_root") != str(Path(release).resolve()) or process.get("runtime_sha") != release_sha:
                return {"ok": False, "reason": f"{service}_identity_invalid"}
        token_gate = value.get("startupTokenGate") or {}
        if (
            token_gate.get("ok") is not True
            or token_gate.get("token_id") != token.get("token_id")
            or token_gate.get("binding") != token.get("binding")
        ):
            return {"ok": False, "reason": "startup_token_phase_identity_invalid", "startupTokenGate": token_gate}
        self.last_token_verification = value
        return {**value, "launchdIdentity": current_context, "launchdLabels": self.service_context["labels"]}

    def archive_token_evidence(self, token: dict[str, Any], plan: PromotionPlan) -> dict[str, Any]:
        receipt = consumption_receipt_path(self.token_path)
        if receipt.is_file() and not receipt.is_symlink():
            value = json.loads(receipt.read_text(encoding="utf-8"))
            if value.get("token_id") != token.get("token_id") or value.get("binding") != token.get("binding"):
                return {"ok": False, "reason": "token_consumption_receipt_binding_mismatch"}
            schema = value.get("schema")
            if schema not in {CONSUMPTION_RECEIPT_SCHEMA, CANCELLATION_RECEIPT_SCHEMA}:
                return {"ok": False, "reason": "token_receipt_schema_invalid"}
            status = "CONSUMED_TOKEN_EVIDENCE_ARCHIVED" if schema == CONSUMPTION_RECEIPT_SCHEMA else "CANCELLED_TOKEN_EVIDENCE_ARCHIVED"
            return {"ok": True, "status": status, "receipt": str(receipt), "receipt_sha256": _sha256(receipt), "evidence": value}
        if self.token_path.is_file() and not self.token_path.is_symlink():
            info = self.token_path.lstat()
            if info.st_dev != token.get("device") or info.st_ino != token.get("inode"):
                return {"ok": False, "reason": "pending_token_identity_mismatch"}
            evidence = {
                "token_id": token.get("token_id"),
                "binding": token.get("binding"),
                "token_sha256": _sha256(self.token_path),
                "device": info.st_dev,
                "inode": info.st_ino,
                "state": "PREPARED_NOT_CONSUMED",
            }
            cancelled = cancel_prepared_startup_skip_token(self.token_path, self.paths.runtime_home, token)
            if cancelled.get("ok") is not True:
                return {"ok": False, "reason": "pending_token_cancel_failed"}
            return {"ok": True, "status": "PENDING_TOKEN_EVIDENCE_ARCHIVED", "evidence": evidence, "cancel": cancelled}
        return {"ok": False, "reason": "token_evidence_missing"}

    def publish_generation(self, plan: PromotionPlan) -> dict[str, Any]:
        self.assert_fence(plan)
        # The immutable receipt is durable before the canonical generation CAS.
        receipt_dir = self.paths.runtime_home / "deployment-receipts"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        path = receipt_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{plan.candidate_sha[:12]}-transactional-promoted.json"
        payload = {
            "schema": "PHONE_FARM_IMMUTABLE_PROMOTION_RECEIPT_V2",
            "receipt_state": "COMMIT_READY",
            "transaction_id": plan.transaction_id,
            "candidate_sha": plan.candidate_sha,
            "candidate_tree": plan.candidate_tree,
            "runtime_root": plan.candidate_release,
            "previous_release_sha": plan.old_active_sha,
            "previous_release_path": plan.old_active_release,
            "service_convergence": self.last_token_verification,
            "created_at": _utc_now(),
        }
        staged = self.c._stage_receipt(path, payload)
        self.c._publish_immutable_receipt(staged, path)
        receipt_sha = _sha256(path)
        registry_path = self.c._canonical_registry_path()
        old_production = plan.registry_pre_state["worker_production"]
        production = {
            **old_production,
            "verification_state": "VERIFIED",
            "sha": plan.candidate_sha,
            "runtime_root": plan.candidate_release,
            "committed_by_transaction": plan.transaction_id,
            "promotion_receipt": str(path),
            "promotion_receipt_sha256": receipt_sha,
        }
        rollback = {
            "verification_state": "VERIFIED",
            "sha": plan.old_active_sha,
            "runtime_root": plan.old_active_release,
            "source": "previous_active_generation",
            "committed_by_transaction": plan.transaction_id,
        }
        worker_payload = {
            **plan.registry_pre_state["worker_component"],
            "production": production,
            "rollback": rollback,
        }
        _durable_registry_replace(registry_path, worker_payload, old_production)
        self.final_receipt = path
        self.final_receipt_sha256 = receipt_sha
        return {
            "ok": True,
            "status": "WORKER_GENERATION_COMMITTED",
            "active": production,
            "rollback": rollback,
            "receipt": str(path),
            "receipt_sha256": receipt_sha,
        }

    def committed_generation_state(self, plan: PromotionPlan) -> dict[str, Any]:
        worker = self.c._read_json_object(self.c._canonical_registry_path())["components"]["worker"]
        production = worker.get("production") or {}
        rollback = worker.get("rollback") or {}
        committed = (
            production.get("sha") == plan.candidate_sha
            and production.get("runtime_root") == plan.candidate_release
            and production.get("committed_by_transaction") == plan.transaction_id
            and rollback.get("sha") == plan.old_active_sha
            and rollback.get("runtime_root") == plan.old_active_release
            and rollback.get("committed_by_transaction") == plan.transaction_id
        )
        old = production == plan.registry_pre_state["worker_production"] and rollback == plan.registry_pre_state["worker_component"].get("rollback")
        return {
            "ok": committed or old,
            "committed": committed,
            "old": old,
            "production_sha": production.get("sha"),
            "rollback_sha": rollback.get("sha"),
        }

    def _storage_registry_path(self) -> Path:
        return self.paths.runtime_home / "storage-maintenance" / "registry.v2.json"

    def reconcile_projections(self, plan: PromotionPlan, *, promoted: bool) -> dict[str, Any]:
        if not promoted:
            return self.restore_precommit_generation(plan)
        storage = self._storage_registry_path()
        if not storage.is_file() or storage.is_symlink():
            raise PromotionFailure("storage_registry_missing")
        payload = json.loads(storage.read_text(encoding="utf-8"))
        stable = self.paths.runtime_home / "phonefarm_runtime_control.py"
        controller_root = str(stable.resolve().parent) if stable.is_symlink() else str(Path(plan.controller_identity["release_root"]))
        protected = set(str(item) for item in payload.get("protected_paths") or [])
        # Recompute the three runtime authorities instead of accumulating every
        # historical active/rollback/controller release forever. Approval and
        # parallel-task paths remain owned by the bootstrap inventory.
        previous_authorities = [payload.get("active_worker"), payload.get("rollback_worker")]
        previous_authorities.extend(payload.get("control_plane_dependencies") or [])
        for authority in previous_authorities:
            if isinstance(authority, dict) and isinstance(authority.get("path"), str):
                protected.discard(authority["path"])
        protected.update({plan.candidate_release, plan.old_active_release, controller_root})
        payload["active_worker"] = {"path": plan.candidate_release, "sha": plan.candidate_sha}
        payload["rollback_worker"] = {"path": plan.old_active_release, "sha": plan.old_active_sha}
        payload["control_plane_dependencies"] = [{"path": controller_root, "reason": "stable_controller_symlink_target"}]
        payload["protected_paths"] = sorted(protected)
        payload["generation"] = uuid.uuid4().hex
        payload["generated_at"] = _utc_now()
        info = storage.stat()
        staged = storage.with_name(f".{storage.name}.{os.getpid()}.pending")
        try:
            with staged.open("xb") as handle:
                handle.write(canonical_bytes(payload) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(staged, stat.S_IMODE(info.st_mode))
            if os.geteuid() == 0:
                os.chown(staged, info.st_uid, info.st_gid)
            os.replace(staged, storage)
            fd = os.open(storage.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            staged.unlink(missing_ok=True)
        return {"ok": True, "status": "PROJECTIONS_RECONCILED", "storage_registry_sha256": _sha256(storage)}

    def restore_precommit_generation(self, plan: PromotionPlan) -> dict[str, Any]:
        registry = self.c._read_json_object(self.c._canonical_registry_path())
        worker = registry["components"]["worker"]
        if worker.get("production") != plan.registry_pre_state["worker_production"]:
            return {"ok": False, "reason": "precommit_registry_not_old"}
        if worker.get("rollback") != plan.registry_pre_state["worker_component"].get("rollback"):
            return {"ok": False, "reason": "precommit_rollback_registry_changed"}
        return {"ok": True, "status": "PRECOMMIT_GENERATION_PRESERVED"}

    def verify_terminal(self, plan: PromotionPlan, *, promoted: bool, token: dict[str, Any]) -> dict[str, Any]:
        release = plan.candidate_release if promoted else plan.old_active_release
        sha = plan.candidate_sha if promoted else plan.old_active_sha
        fence = self.fence_state(plan)
        expected_terminal = "PROMOTED" if promoted else "ROLLED_BACK"
        if fence.get("active") is not True and fence.get("terminal_state") != expected_terminal:
            return {"ok": False, "reason": "terminal_fence_state_invalid"}
        if self.paths.current_link.resolve() != Path(release).resolve():
            return {"ok": False, "reason": "terminal_runtime_pointer_mismatch"}
        services = self.verify_services_and_token(release, sha, token, plan)
        if services.get("ok") is not True:
            return services
        registry = self.c._read_json_object(self.c._canonical_registry_path())
        worker = registry["components"]["worker"]
        if promoted:
            if worker.get("production", {}).get("sha") != plan.candidate_sha or worker.get("rollback", {}).get("sha") != plan.old_active_sha:
                return {"ok": False, "reason": "terminal_committed_generation_mismatch"}
        elif worker.get("production") != plan.registry_pre_state["worker_production"]:
            return {"ok": False, "reason": "terminal_rollback_generation_mismatch"}
        return {
            "ok": True,
            "status": "NEW_RELEASE_FULLY_ACTIVE" if promoted else "OLD_RELEASE_FULLY_ACTIVE",
            "runtime_root": release,
            "release_sha": sha,
            "services": services,
            "registry_path": str(self.c._canonical_registry_path()),
            "registry_worker": worker,
            "registry_worker_sha256": hashlib.sha256(canonical_bytes(worker)).hexdigest(),
            "fence": fence,
        }

    def release_fence(self, plan: PromotionPlan) -> dict[str, Any]:
        promoted = self.c._read_json_object(self.c._canonical_registry_path())["components"]["worker"]["production"].get("sha") == plan.candidate_sha
        return self.fence.release(transaction_id=plan.transaction_id, terminal_state="PROMOTED" if promoted else "ROLLED_BACK")

    def complete_finalization(self, plan: PromotionPlan, *, promoted: bool, evidence: dict[str, Any]) -> dict[str, Any]:
        fence = self.fence_state(plan)
        state = "PROMOTED" if promoted else "ROLLED_BACK"
        if fence.get("active") is not False or fence.get("terminal_state") != state:
            raise PromotionFailure("final_receipt_requires_released_owned_fence")
        path = self.paths.runtime_home / "deployment-receipts" / (plan.transaction_id.replace(":", "-") + "-finalized.json")
        payload = {"schema": "PHONE_FARM_TRANSACTION_FINALIZATION_V2",
                   "transaction_id": plan.transaction_id, "receipt_state": state,
                   "candidate_sha": plan.candidate_sha,
                   "active_sha": plan.candidate_sha if promoted else plan.old_active_sha,
                   "external_finish": evidence["finish"], "fence_release": fence}
        if path.exists():
            existing = self.c._read_json_object(path)
            if existing != payload or not (path.stat().st_flags & stat.UF_IMMUTABLE):
                raise PromotionFailure("finalization_receipt_mismatch")
        else:
            staged = self.c._stage_receipt(path, payload)
            self.c._publish_immutable_receipt(staged, path)
        return {"ok": True, "path": str(path), "sha256": _sha256(path)}

    def finish_transaction(self, plan: PromotionPlan, *, promoted: bool, evidence: dict[str, Any]) -> dict[str, Any]:
        previous = getattr(self, "finished_result", None)
        if previous is not None:
            if previous[0] != promoted:
                raise PromotionFailure("finished_transaction_direction_mismatch")
            return previous[1]
        if promoted:
            if self.final_receipt is None:
                production = self.c._read_json_object(self.c._canonical_registry_path())["components"]["worker"]["production"]
                self.final_receipt = Path(production["promotion_receipt"])
            path = self.paths.runtime_home / "deployment-receipts" / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{plan.candidate_sha[:12]}-transactional-promoted.json"
            payload = {
                "schema": "PHONE_FARM_IMMUTABLE_PROMOTION_RECEIPT_V2",
                "receipt_state": "PROMOTED",
                "transaction_id": plan.transaction_id,
                "candidate_sha": plan.candidate_sha,
                "candidate_tree": plan.candidate_tree,
                "runtime_root": plan.candidate_release,
                "release_sha": plan.candidate_sha,
                "previous_release_sha": plan.old_active_sha,
                "previous_release_path": plan.old_active_release,
                "commit_evidence": str(self.final_receipt),
                "commit_evidence_sha256": _sha256(self.final_receipt),
                "terminal_evidence": evidence,
                "completed_at": _utc_now(),
            }
            staged = self.c._stage_receipt(path, payload)
            self.c._publish_immutable_receipt(staged, path)
            archive = self.transaction.finish(path, terminal_evidence=evidence)
        else:
            path = self.paths.runtime_home / "deployment-receipts" / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{plan.candidate_sha[:12]}-transactional-rolled-back.json"
            payload = {
                "schema": "PHONE_FARM_IMMUTABLE_PROMOTION_RECEIPT_V2",
                "receipt_state": "ROLLBACK_COMPLETE",
                "transaction_id": plan.transaction_id,
                "candidate_sha": plan.candidate_sha,
                "failed_candidate_root": plan.candidate_release,
                "runtime_root": plan.old_active_release,
                "release_sha": plan.old_active_sha,
                "terminal_evidence": evidence,
                "completed_at": _utc_now(),
            }
            staged = self.c._stage_receipt(path, payload)
            self.c._publish_immutable_receipt(staged, path)
            archive = self.transaction.finish(path, terminal_evidence=evidence)
        result = {"ok": True, "status": "EXTERNAL_TRANSACTION_FINISHED", "archive": str(archive)}
        self.finished_result = (promoted, result)
        return result


def build_plan(controller: Any, transaction: Any, paths: Any, validation: Any) -> PromotionPlan:
    registry = controller._read_json_object(controller._canonical_registry_path())
    worker = registry["components"]["worker"]
    production = worker["production"]
    old_root = paths.current_link.resolve(strict=True)
    old_sha = controller._git_full_commit(old_root)
    # A separately built rollback artifact does not change the identity of the
    # currently active generation. Never quiesce a V1-only OLD runtime and then
    # discover that it cannot consume the mandatory bound rollback token.
    import ast
    token_source = old_root / "startup_tick_skip_token.py"
    constants = {}
    for node in ast.parse(token_source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    if (constants.get("TOKEN_SCHEMA") != "PHONEFARM_STARTUP_SKIP_TOKEN_V2"
            or constants.get("CONSUMPTION_RECEIPT_SCHEMA") != "PHONEFARM_STARTUP_SKIP_CONSUMPTION_V2"):
        raise PromotionFailure("rollback_runtime_requires_transactional_compatibility_baseline")
    candidate_root = Path(validation.resolved_root).resolve(strict=True)
    candidate_sha = controller._git_full_commit(candidate_root)
    candidate_tree = controller.subprocess.check_output(
        ["git", "-c", f"safe.directory={candidate_root}", "-C", str(candidate_root), "rev-parse", "HEAD^{tree}"],
        text=True,
    ).strip()
    if production.get("sha") != old_sha or production.get("runtime_root") != str(old_root):
        raise PromotionFailure("canonical_registry_does_not_match_old_runtime")
    previous_rollback = worker.get("rollback") or {}
    service_context = controller._promotion_service_context(paths)
    controller_path = paths.runtime_home / "phonefarm_runtime_control.py"
    controller_root = controller_path.resolve().parent if controller_path.exists() or controller_path.is_symlink() else candidate_root
    return PromotionPlan(
        transaction_id=f"worker-promotion:{uuid.uuid4().hex}",
        candidate_sha=candidate_sha,
        candidate_tree=candidate_tree,
        candidate_release=str(candidate_root),
        old_active_sha=old_sha,
        old_active_release=str(old_root),
        previous_rollback_sha=str(previous_rollback.get("sha") or ""),
        previous_rollback_release=str(previous_rollback.get("runtime_root") or ""),
        controller_identity={
            "path": str(controller_path),
            "release_root": str(controller_root),
            "sha256": _sha256(controller_path),
        },
        launchd_identity={
            **service_context,
            "labels": {
                "dispatcher": controller.DISPATCHER_LABEL,
                "heartbeat": controller.HEARTBEAT_LABEL,
                "notifier": controller.NOTIFIER_LABEL,
            },
        },
        registry_pre_state={
            "registry_path": str(controller._canonical_registry_path()),
            "registry_sha256": _sha256(controller._canonical_registry_path()),
            "worker_component": worker,
            "worker_production": production,
        },
        runtime_pointer_pre_state=str(old_root),
    )


def run_transactional_switch(controller: Any, target: str, transaction: Any) -> dict[str, Any]:
    paths = controller.runtime_paths()
    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = paths.releases_dir / target
    validation = controller.resolve_runtime_root(
        controller.RuntimePaths(target_path, paths.releases_dir, paths.runtime_home, paths.legacy_root)
    )
    if not validation.ok:
        return controller._runtime_root_payload(validation, command="switch-release")
    integrity = controller.verify_deployment_candidate(Path(validation.resolved_root))
    if integrity.get("ok") is not True:
        return {**integrity, "command": "switch-release"}
    evidence = controller._validate_release_evidence(Path(validation.resolved_root), integrity)
    if evidence.get("ok") is not True:
        return {**evidence, "command": "switch-release", "status": "promotion_evidence_blocked"}
    plan = build_plan(controller, transaction, paths, validation)
    journal_dir = paths.runtime_home / "run" / "follow60-lock-v2" / "transactions"
    journal_path = journal_dir / f"{plan.candidate_sha}-{plan.transaction_id.rsplit(':', 1)[-1]}.json"
    adapter = PhoneFarmPromotionAdapter(
        controller=controller,
        transaction=transaction,
        paths=paths,
        validation=validation,
        integrity=integrity,
        evidence=evidence,
        plan=plan,
    )
    result = DurablePromotion(journal_path, plan, adapter).run()
    return {**result, "command": "switch-release", "transactionJournal": str(journal_path)}


def _plan_from_journal(value: dict[str, Any]) -> PromotionPlan:
    candidate = value["candidate"]
    old = value["old_active"]
    rollback = value["previous_rollback"]
    return PromotionPlan(
        transaction_id=value["transaction_id"],
        candidate_sha=candidate["sha"],
        candidate_tree=candidate["tree"],
        candidate_release=candidate["release"],
        old_active_sha=old["sha"],
        old_active_release=old["release"],
        previous_rollback_sha=rollback["sha"],
        previous_rollback_release=rollback["release"],
        controller_identity=value["controller_identity"],
        launchd_identity=value["launchd_identity"],
        registry_pre_state=value["registry_pre_state"],
        runtime_pointer_pre_state=value["runtime_pointer_pre_state"],
    )


def recover_transactional_switch(controller: Any, expected_lock_sha256: str) -> dict[str, Any]:
    """Resume one dead-owner transaction; never archive it as merely stale."""
    from follow60_external_deployment_lock_v2 import (
        STORE,
        completed_lease,
        read_record,
        resume_completed_transaction,
        resume_stale_transaction,
    )

    active = STORE / "transactions" / "active.json"
    active_present = active.exists() or active.is_symlink()
    if active_present:
        lease = read_record(active)
    else:
        _, lease = completed_lease(expected_lock_sha256)
    candidate_root = Path(lease["candidate_root"]).resolve(strict=True)
    journals = sorted(
        (STORE / "transactions").glob(f"{lease['candidate_commit_sha']}-*.json"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    open_journals = []
    for journal in journals:
        value = read_journal(journal)
        if value["phase"] not in {"PROMOTED", "ROLLED_BACK"}:
            open_journals.append((journal, value))
    if len(open_journals) != 1:
        raise PromotionFailure("recoverable_transaction_journal_cardinality_invalid")
    journal_path, state = open_journals[0]
    plan = _plan_from_journal(state)
    if plan.candidate_release != str(candidate_root):
        raise PromotionFailure("recovery_candidate_root_mismatch")
    transaction = (
        resume_stale_transaction(candidate_root, expected_lock_sha256)
        if active_present
        else resume_completed_transaction(candidate_root, expected_lock_sha256)
    )
    paths = controller.runtime_paths()
    validation = controller.resolve_runtime_root(
        controller.RuntimePaths(candidate_root, paths.releases_dir, paths.runtime_home, paths.legacy_root)
    )
    adapter = PhoneFarmPromotionAdapter(
        controller=controller,
        transaction=transaction,
        paths=paths,
        validation=validation,
        integrity={},
        evidence={},
        plan=plan,
    )
    try:
        result = DurablePromotion(journal_path, plan, adapter).recover()
        return {**result, "command": "recover-promotion", "transactionJournal": str(journal_path)}
    finally:
        transaction.__exit__(None, None, None)
