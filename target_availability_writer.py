"""Dormant fail-open writer for Target Availability observations.

The writer owns one table only: ``ct_target_availability_observations``.  It is
disabled by default and starts no thread or network call until both capture and
writer flags are explicitly enabled.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import base64
import json
import os
import re
import threading
import time
from typing import Any, Callable, Deque, Iterable, Mapping, Optional, Protocol, Sequence
from urllib import error, parse, request

from target_availability_observation import TargetAvailabilityObservation


CAPTURE_FLAG = "target_availability_observation_capture_enabled"
WRITER_FLAG = "target_availability_writer_enabled"
SHADOW_FLAG = "target_availability_shadow_enabled"
POLICY_SHADOW_FLAG = "target_availability_policy_shadow_enabled"

_ENV_KEYS = {
    CAPTURE_FLAG: "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED",
    WRITER_FLAG: "TARGET_AVAILABILITY_WRITER_ENABLED",
    SHADOW_FLAG: "TARGET_AVAILABILITY_SHADOW_ENABLED",
    POLICY_SHADOW_FLAG: "TARGET_AVAILABILITY_POLICY_SHADOW_ENABLED",
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


@dataclass(frozen=True)
class TargetAvailabilityFeatureFlags:
    target_availability_observation_capture_enabled: bool = False
    target_availability_writer_enabled: bool = False
    target_availability_shadow_enabled: bool = False
    target_availability_policy_shadow_enabled: bool = False
    account_allowlist: frozenset[str] = frozenset()
    kill_switch: bool = False

    @classmethod
    def from_mapping(cls, values: Optional[Mapping[str, object]] = None) -> "TargetAvailabilityFeatureFlags":
        source = values if values is not None else os.environ
        kill_switch_file = source.get("TARGET_AVAILABILITY_KILL_SWITCH_FILE")
        allowlist = _account_allowlist(source.get("TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST"))
        return cls(
            **{field: _enabled(source.get(env_key)) for field, env_key in _ENV_KEYS.items()},
            account_allowlist=allowlist,
            kill_switch=_enabled(source.get("TARGET_AVAILABILITY_KILL_SWITCH"))
            or _kill_switch_file_active(kill_switch_file),
        )

    def capture_allowed(self, account_id: str) -> bool:
        return bool(
            not self.kill_switch
            and self.target_availability_observation_capture_enabled
            and self.account_allowlist
            and _normalized_uuid(account_id) in self.account_allowlist
        )

    def writer_allowed(self, account_id: str) -> bool:
        return self.capture_allowed(account_id) and self.target_availability_writer_enabled


class ObservationTransport(Protocol):
    def send_batch(self, rows: Sequence[Mapping[str, Any]]) -> None: ...


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


class SupabaseObservationTransport:
    """Bounded PostgREST adapter; service-role credentials are mandatory."""

    def __init__(self, *, url: str, service_role_key: str, timeout_seconds: float = 1.5, max_retries: int = 1) -> None:
        if not str(url or "").strip() or not str(service_role_key or "").strip():
            raise ValueError("supabase_service_role_credentials_required")
        key = str(service_role_key).strip()
        if key.startswith("sb_secret_"):
            pass
        else:
            try:
                encoded = key.split(".")[1]
                encoded += "=" * (-len(encoded) % 4)
                claims = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
            except (IndexError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("supabase_service_role_key_required") from exc
            if str(claims.get("role") or "") != "service_role":
                raise ValueError("supabase_service_role_key_required")
        self._endpoint = "%s/rest/v1/ct_target_availability_observations?%s" % (
            str(url).rstrip("/"),
            parse.urlencode({"on_conflict": "tenant_id,account_id,idempotency_key"}),
        )
        self._key = key
        self._timeout = max(0.1, min(float(timeout_seconds), 3.0))
        self._max_retries = max(0, min(int(max_retries), 1))

    @classmethod
    def from_environment(cls) -> "SupabaseObservationTransport":
        return cls(
            url=os.getenv("SUPABASE_URL") or "",
            service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "",
            timeout_seconds=float(os.getenv("TARGET_AVAILABILITY_WRITER_TIMEOUT_SECONDS") or "1.5"),
            max_retries=int(os.getenv("TARGET_AVAILABILITY_WRITER_MAX_RETRIES") or "1"),
        )

    def send_batch(self, rows: Sequence[Mapping[str, Any]]) -> None:
        body = json.dumps(list(rows), sort_keys=True, separators=(",", ":")).encode("utf-8")
        req = request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "apikey": self._key,
                "authorization": "Bearer %s" % self._key,
                "content-type": "application/json",
                "prefer": "resolution=ignore-duplicates,return=minimal",
            },
        )
        last_error: BaseException | None = None
        for attempt in range(self._max_retries + 1):
            try:
                with request.urlopen(req, timeout=self._timeout) as response:
                    response.read()
                return
            except (error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    time.sleep(0.05 * (attempt + 1))
        raise RuntimeError("target_availability_writer_transport_failed") from last_error


class FailOpenTargetAvailabilityWriter:
    def __init__(
        self,
        transport: ObservationTransport,
        *,
        capacity: int = 256,
        batch_size: int = 20,
        circuit_failure_threshold: int = 3,
        circuit_cooldown_seconds: float = 30.0,
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
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._failures = 0
        self._circuit_until = 0.0
        self.metrics = {"accepted": 0, "duplicates": 0, "flushed": 0, "failures": 0, "dropped": 0, "circuit_open": 0}

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
    "TargetAvailabilityFeatureFlags",
    "SupabaseObservationTransport",
    "FailOpenTargetAvailabilityWriter",
    "observation_to_database_row",
]
