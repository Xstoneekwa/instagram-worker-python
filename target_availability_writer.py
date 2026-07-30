"""Fail-open transport for the private Target Availability Shadow pipeline.

The Worker emits observations only. The authenticated Backend owns identity,
assessment and current-state persistence. No thread or network call starts
until capture, writer, all producers and global Shadow are explicitly enabled.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from typing import Any, Callable, Deque, Iterable, Mapping, Optional, Protocol, Sequence
from urllib import error, request

from target_availability_observation import TargetAvailabilityObservation


CAPTURE_FLAG = "target_availability_observation_capture_enabled"
WRITER_FLAG = "target_availability_writer_enabled"
SHADOW_FLAG = "target_availability_shadow_enabled"
POLICY_SHADOW_FLAG = "target_availability_policy_shadow_enabled"
IDENTITY_PRODUCER_FLAG = "target_availability_identity_producer_enabled"
ASSESSMENT_PRODUCER_FLAG = "target_availability_assessment_producer_enabled"
CURRENT_PROJECTOR_FLAG = "target_availability_current_projector_enabled"

SCOPE_MODE_OFF = "off"
SCOPE_MODE_EXPLICIT = "explicit_allowlist"
SCOPE_MODE_ALL_ACTIVE = "all_active_accounts"
DEFAULT_CONTROL_FILE = "/Users/admin/phonefarm-runtime/control/target-availability-control.json"
DEFAULT_AUTO_KILL_FILE = "/Users/admin/phonefarm-runtime/control/target-availability-auto-kill.json"

_ENV_KEYS = {
    CAPTURE_FLAG: "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED",
    WRITER_FLAG: "TARGET_AVAILABILITY_WRITER_ENABLED",
    SHADOW_FLAG: "TARGET_AVAILABILITY_SHADOW_ENABLED",
    POLICY_SHADOW_FLAG: "TARGET_AVAILABILITY_POLICY_SHADOW_ENABLED",
    IDENTITY_PRODUCER_FLAG: "TARGET_AVAILABILITY_IDENTITY_PRODUCER_ENABLED",
    ASSESSMENT_PRODUCER_FLAG: "TARGET_AVAILABILITY_ASSESSMENT_PRODUCER_ENABLED",
    CURRENT_PROJECTOR_FLAG: "TARGET_AVAILABILITY_CURRENT_PROJECTOR_ENABLED",
}

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _enabled(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _normalized_uuid(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if _UUID_RE.fullmatch(normalized) else ""


def _account_allowlist(value: object) -> frozenset[str]:
    """Parse the all-or-nothing UUID allowlist shared with the Backend contract."""

    raw_items: object
    if isinstance(value, (list, tuple, set, frozenset)):
        raw_items = list(value)
    elif isinstance(value, str):
        serialized = value.strip()
        if not serialized:
            return frozenset()
        if serialized.startswith("[") or serialized.startswith("{"):
            try:
                raw_items = json.loads(serialized)
            except (TypeError, ValueError, json.JSONDecodeError):
                return frozenset()
            if not isinstance(raw_items, list):
                return frozenset()
        else:
            raw_items = serialized.split(",")
    elif value is None:
        return frozenset()
    else:
        return frozenset()

    normalized = [_normalized_uuid(item) for item in raw_items]
    if not normalized or any(not item for item in normalized):
        return frozenset()
    return frozenset(normalized)


def _kill_switch_file_active(path: object) -> bool:
    configured = str(path or "").strip()
    if not configured:
        return False
    try:
        os.stat(configured)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _read_control_file(values: Mapping[str, object], *, use_default: bool) -> tuple[dict[str, object], bool]:
    configured = str(values.get("TARGET_AVAILABILITY_CONTROL_FILE") or (DEFAULT_CONTROL_FILE if use_default else "")).strip()
    if not configured:
        return {}, False
    try:
        with open(configured, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {}, True
        return {str(key): value for key, value in payload.items()}, False
    except FileNotFoundError:
        return {}, False
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}, True


def _atomic_safe_json(path: str, payload: Mapping[str, object]) -> bool:
    target = str(path or "").strip()
    if not target or not os.path.isabs(target):
        return False
    temporary: str | None = None
    try:
        parent = os.path.dirname(target)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".target-availability-auto-kill.", dir=parent)
        os.fchmod(descriptor, 0o600)
        encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
        return True
    except OSError:
        return False
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
@dataclass(frozen=True)
class TargetAvailabilityFeatureFlags:
    target_availability_observation_capture_enabled: bool = False
    target_availability_writer_enabled: bool = False
    target_availability_shadow_enabled: bool = False
    target_availability_policy_shadow_enabled: bool = False
    target_availability_identity_producer_enabled: bool = False
    target_availability_assessment_producer_enabled: bool = False
    target_availability_current_projector_enabled: bool = False
    scope_mode: str = SCOPE_MODE_OFF
    account_allowlist: frozenset[str] = frozenset()
    kill_switch: bool = False
    config_invalid: bool = False

    @classmethod
    def from_mapping(cls, values: Optional[Mapping[str, object]] = None) -> "TargetAvailabilityFeatureFlags":
        environment = values if values is not None else os.environ
        control, control_invalid = _read_control_file(environment, use_default=values is None)
        source = {**environment, **control}
        kill_switch_file = source.get("TARGET_AVAILABILITY_KILL_SWITCH_FILE")
        allowlist = _account_allowlist(source.get("TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST"))
        raw_mode = str(source.get("TARGET_AVAILABILITY_SCOPE_MODE") or "").strip().lower()
        if not raw_mode:
            raw_mode = SCOPE_MODE_EXPLICIT if allowlist else SCOPE_MODE_OFF
        scope_mode = raw_mode if raw_mode in {SCOPE_MODE_OFF, SCOPE_MODE_EXPLICIT, SCOPE_MODE_ALL_ACTIVE} else SCOPE_MODE_OFF
        auto_kill_file = source.get("TARGET_AVAILABILITY_AUTO_KILL_FILE") or DEFAULT_AUTO_KILL_FILE
        return cls(
            **{field: _enabled(source.get(env_key)) for field, env_key in _ENV_KEYS.items()},
            scope_mode=scope_mode,
            account_allowlist=allowlist,
            kill_switch=_enabled(source.get("TARGET_AVAILABILITY_KILL_SWITCH"))
            or _kill_switch_file_active(kill_switch_file)
            or _kill_switch_file_active(auto_kill_file)
            or control_invalid,
            config_invalid=control_invalid or raw_mode not in {SCOPE_MODE_OFF, SCOPE_MODE_EXPLICIT, SCOPE_MODE_ALL_ACTIVE},
        )

    def capture_allowed(self, account_id: str) -> bool:
        normalized_account = _normalized_uuid(account_id)
        return bool(
            not self.kill_switch
            and not self.config_invalid
            and self.target_availability_observation_capture_enabled
            and normalized_account
            and (
                self.scope_mode == SCOPE_MODE_ALL_ACTIVE
                or (self.scope_mode == SCOPE_MODE_EXPLICIT and self.account_allowlist and normalized_account in self.account_allowlist)
            )
        )

    def writer_allowed(self, account_id: str) -> bool:
        return self.capture_allowed(account_id) and self.target_availability_writer_enabled

    def pipeline_allowed(self, account_id: str) -> bool:
        return bool(
            self.writer_allowed(account_id)
            and self.target_availability_shadow_enabled
            and self.target_availability_identity_producer_enabled
            and self.target_availability_assessment_producer_enabled
            and self.target_availability_current_projector_enabled
            and not self.target_availability_policy_shadow_enabled
        )


class ObservationTransport(Protocol):
    def send_batch(self, rows: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]: ...


def observation_to_database_row(observation: TargetAvailabilityObservation) -> Mapping[str, Any]:
    payload = observation.to_dict()
    evidence_safe = dict(payload["evidence_safe"])
    evidence_safe["contract_context"] = {
        "request_id": payload["request_id"],
        "instance_id": payload["instance_id"],
        "observation_stage": payload["observation_stage"],
        "identity_source": payload["identity_source"],
        "identity_confidence": payload["identity_confidence"],
        "username_lookup_started": payload["username_lookup_started"],
        "username_lookup_completed": payload["username_lookup_completed"],
        "profile_ambiguous": payload["profile_ambiguous"],
        "followers_surface_entered": payload["followers_surface_entered"],
        "followers_surface_entry_failed": payload["followers_surface_entry_failed"],
        "pagination_stalled": payload["pagination_stalled"],
        "recovery_attempted": payload["recovery_attempted"],
        "ui_ambiguous": payload["ui_ambiguous"],
        "network_ambiguous": payload["network_ambiguous"],
        "session_ambiguous": payload["session_ambiguous"],
        "source_profile_mismatch": payload["source_profile_mismatch"],
        "identity_conflict": payload["identity_conflict"],
    }
    return {
        "tenant_id": payload["tenant_id"],
        "account_id": payload["account_id"],
        "target_id": payload["target_id"],
        "observed_at": payload["observed_at"],
        "source": "worker",
        "source_run_id": payload["run_id"],
        "source_worker": "phonefarm-worker",
        "worker_version": payload["worker_version"],
        "source_device_key": payload["device_key"],
        "instagram_version": payload["instagram_version"],
        "searched_username": payload["searched_username"],
        "observed_username": payload["observed_username"],
        "observed_stable_platform_user_id": payload["observed_stable_platform_user_id"],
        "lookup_result": payload["lookup_result"],
        "profile_found": payload["profile_found"],
        "verified_badge": payload["verified_badge"],
        "followers_surface": payload["followers_surface"],
        "accessible_profiles_count": payload["accessible_profiles_count"],
        "terminal_end_detected": payload["terminal_end_detected"],
        "repeated_first_profiles_detected": payload["repeated_first_profiles_detected"],
        "retry_count": payload["retry_count"],
        "retry_budget_exhausted": payload["retry_budget_exhausted"],
        "navigation_timeout": payload["navigation_timeout"],
        "recovery_outcome": payload["recovery_outcome"],
        "ui_evidence_quality": payload["ui_evidence_quality"],
        "network_state": payload["network_state"],
        "session_state": payload["session_state"],
        "reason_codes": payload["reason_codes"],
        "idempotency_key": payload["idempotency_key"],
        "evidence_safe": evidence_safe,
    }


class BackendPipelineTransport:
    """Bounded private Backend adapter; no Supabase credential is exposed to runs."""

    def __init__(
        self,
        *,
        api_base_url: str,
        caller_token: str,
        worker_id: str,
        worker_release: str,
        timeout_seconds: float = 1.5,
        max_retries: int = 1,
    ) -> None:
        base_url = str(api_base_url or "").strip().rstrip("/")
        token = str(caller_token or "").strip()
        identifier = str(worker_id or "").strip()
        release = str(worker_release or "").strip()
        if not base_url.startswith(("https://", "http://")) or not token or not identifier or not release:
            raise ValueError("target_availability_backend_transport_context_required")
        self._endpoint = "%s/api/internal/target-availability/ingest" % base_url
        self._token = token
        self._worker_id = identifier[:120]
        self._worker_release = release[:120]
        self._timeout = max(0.1, min(float(timeout_seconds), 3.0))
        self._max_retries = max(0, min(int(max_retries), 1))

    @classmethod
    def from_environment(cls) -> "BackendPipelineTransport":
        return cls(
            api_base_url=os.getenv("INSTAGRAM_DASHBOARD_API_BASE_URL") or "",
            caller_token=os.getenv("INSTAGRAM_AUTO_RESTART_TICK_TOKEN") or "",
            worker_id=os.getenv("RUN_CONTROL_DISPATCHER_WORKER_ID")
            or os.getenv("PHONEFARM_INSTANCE_ID")
            or "phonefarm-dispatcher",
            worker_release=os.getenv("PHONEFARM_WORKER_RELEASE") or os.getenv("GIT_SHA") or "unknown-release",
            timeout_seconds=float(os.getenv("TARGET_AVAILABILITY_WRITER_TIMEOUT_SECONDS") or "1.5"),
            max_retries=int(os.getenv("TARGET_AVAILABILITY_WRITER_MAX_RETRIES") or "1"),
        )

    def send_batch(self, rows: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
        keys = sorted(str(row.get("idempotency_key") or "") for row in rows)
        batch_key = "target-availability-batch:%s" % hashlib.sha256(
            json.dumps(keys, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        body = json.dumps(
            {
                "rows": list(rows),
                "worker_id": self._worker_id,
                "worker_release": self._worker_release,
                "batch_key": batch_key,
                "queue_depth": len(rows),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        req = request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-instagram-auto-restart-tick-token": self._token,
                "x-run-control-worker-id": self._worker_id,
            },
        )
        last_error: BaseException | None = None
        for attempt in range(self._max_retries + 1):
            try:
                with request.urlopen(req, timeout=self._timeout) as response:
                    decoded = json.loads(response.read().decode("utf-8"))
                result = decoded.get("data") if isinstance(decoded, dict) and decoded.get("ok") is True else None
                if not isinstance(result, dict):
                    raise RuntimeError("target_availability_backend_response_invalid")
                if result.get("autoKilled") is True:
                    raise RuntimeError("target_availability_backend_auto_killed")
                if result.get("active") is not True:
                    raise RuntimeError("target_availability_backend_inactive")
                return result
            except (error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    time.sleep(0.05 * (attempt + 1))
        raise RuntimeError("target_availability_backend_transport_failed") from last_error


class FailOpenTargetAvailabilityWriter:
    def __init__(
        self,
        transport: ObservationTransport,
        *,
        capacity: int = 256,
        batch_size: int = 20,
        circuit_failure_threshold: int = 3,
        circuit_cooldown_seconds: float = 30.0,
        auto_kill_file: str = "",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._queue: Deque[Mapping[str, Any]] = deque()
        self._seen: set[str] = set()
        self._capacity = max(1, min(int(capacity), 2_000))
        self._batch_size = max(1, min(int(batch_size), 100))
        self._threshold = max(1, int(circuit_failure_threshold))
        self._cooldown = max(0.0, float(circuit_cooldown_seconds))
        self._clock = clock
        self._auto_kill_file = str(auto_kill_file or "").strip()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0
        self._circuit_until = 0.0
        self.metrics = {"accepted": 0, "duplicates": 0, "flushed": 0, "failures": 0, "dropped": 0, "circuit_open": 0, "auto_killed": 0}

    def enqueue(self, observation: TargetAvailabilityObservation) -> bool:
        try:
            row = observation_to_database_row(observation)
            key = str(row["idempotency_key"])
            with self._lock:
                if key in self._seen:
                    self.metrics["duplicates"] += 1
                    return True
                if len(self._queue) >= self._capacity:
                    self.metrics["dropped"] += 1
                    return False
                self._seen.add(key)
                self._queue.append(row)
                self.metrics["accepted"] += 1
            self._wake.set()
            return True
        except Exception:
            self.metrics["dropped"] += 1
            return False

    def flush_once(self) -> int:
        if self._auto_kill_file and _kill_switch_file_active(self._auto_kill_file):
            with self._lock:
                dropped = len(self._queue)
                self._queue.clear()
                self._seen.clear()
                self.metrics["dropped"] += dropped
            self._stop.set()
            return 0
        now = self._clock()
        if now < self._circuit_until:
            self.metrics["circuit_open"] += 1
            return 0
        with self._lock:
            rows = [self._queue.popleft() for _ in range(min(self._batch_size, len(self._queue)))]
        if not rows:
            return 0
        try:
            self._transport.send_batch(rows)
        except Exception:
            with self._lock:
                for row in reversed(rows):
                    self._queue.appendleft(row)
            self._failures += 1
            self.metrics["failures"] += 1
            if self._failures >= self._threshold:
                self._circuit_until = now + self._cooldown
                if _atomic_safe_json(
                    self._auto_kill_file,
                    {
                        "schema_version": "target-availability-auto-kill-v1",
                        "reason": "repeated_backend_pipeline_failure",
                        "human_reenable_required": True,
                        "failure_count": self._failures,
                        "created_at_epoch_seconds": int(time.time()),
                    },
                ):
                    self.metrics["auto_killed"] += 1
                    with self._lock:
                        dropped = len(self._queue)
                        self._queue.clear()
                        self._seen.clear()
                        self.metrics["dropped"] += dropped
                    self._stop.set()
            return 0
        self._failures = 0
        with self._lock:
            for row in rows:
                self._seen.discard(str(row.get("idempotency_key") or ""))
        self.metrics["flushed"] += len(rows)
        return len(rows)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            candidate = threading.Thread(target=self._run, name="target-availability-writer", daemon=True)
            try:
                candidate.start()
            except Exception:
                self.metrics["failures"] += 1
                return
            self._thread = candidate

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            try:
                while not self._stop.is_set() and self.flush_once() > 0:
                    pass
            except Exception:
                self.metrics["failures"] += 1

    def close(self, timeout_seconds: float = 0.5) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout_seconds))

    @property
    def queue_size(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def thread_alive(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())


__all__ = [
    "CAPTURE_FLAG",
    "WRITER_FLAG",
    "SHADOW_FLAG",
    "POLICY_SHADOW_FLAG",
    "IDENTITY_PRODUCER_FLAG",
    "ASSESSMENT_PRODUCER_FLAG",
    "CURRENT_PROJECTOR_FLAG",
    "SCOPE_MODE_OFF",
    "SCOPE_MODE_EXPLICIT",
    "SCOPE_MODE_ALL_ACTIVE",
    "DEFAULT_CONTROL_FILE",
    "DEFAULT_AUTO_KILL_FILE",
    "TargetAvailabilityFeatureFlags",
    "BackendPipelineTransport",
    "FailOpenTargetAvailabilityWriter",
    "observation_to_database_row",
]
