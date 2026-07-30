"""Bounded, local-only observability for the Gate 4B capture pilot.

The probe keeps two fixed-size aggregate summaries (current and previous run),
never stores an observation payload, and exposes only an operator snapshot.
It has no network, Supabase, queue, logging, or thread dependency.
"""

from __future__ import annotations

from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import sys
import tempfile
import time
from typing import Any, Mapping, Optional


PROBE_FLAG = "TARGET_AVAILABILITY_MEMORY_PROBE_ENABLED"
STATUS_FILE_KEY = "TARGET_AVAILABILITY_MEMORY_PROBE_STATUS_FILE"
DEFAULT_STATUS_FILE = "/private/tmp/phonefarm-target-availability-gate4b-status.json"
SNAPSHOT_SCHEMA_VERSION = "target-availability-memory-probe-v2"
MAX_SNAPSHOT_BYTES = 4_096
_SAFE_CODE_RE = re.compile(r"[^a-z0-9_.:-]+")


def _enabled(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run_hash(run_id: object) -> str:
    raw = str(run_id or "no-run").strip() or "no-run"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _safe_code(value: object, fallback: str = "") -> str:
    normalized = _SAFE_CODE_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return (normalized or fallback)[:80]


def _peak_rss_bytes() -> int:
    try:
        value = max(0, int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
        return value if sys.platform == "darwin" else value * 1024
    except (OSError, TypeError, ValueError):
        return 0


def _empty_summary() -> dict[str, Any]:
    return {
        "capture_attempt_count": 0,
        "observation_created_count": 0,
        "observation_valid_count": 0,
        "observation_rejected_count": 0,
        "observation_error_count": 0,
        "payload_retained_count": 0,
        "hook_total_duration_ns": 0,
        "hook_max_duration_ns": 0,
        "cpu_total_duration_ns": 0,
        "cpu_max_duration_ns": 0,
        "memory_before_bytes": 0,
        "memory_peak_bytes": 0,
        "memory_after_bytes": 0,
        "last_run_id_hash": "",
        "last_account_id": "",
        "last_stage": "",
        "last_error_code": "",
        "last_reason_code": "",
    }


class TargetAvailabilityMemoryProbe:
    """Single-process, fixed-cardinality aggregate probe."""

    def __init__(self, *, status_file: str) -> None:
        configured = str(status_file or "").strip()
        path = Path(configured)
        if not configured or not path.is_absolute():
            raise ValueError("memory_probe_status_file_absolute_path_required")
        self._status_file = path
        self._current = _empty_summary()
        self._previous = _empty_summary()
        self._snapshot_written = False
        self._snapshot_error_count = 0
        self._hook_cpu_started_ns = 0
        self._hook_memory_before_bytes = 0
        self._writer_enabled = False
        self._shadow_enabled = False
        self._restore_previous_aggregate()

    @classmethod
    def from_mapping(
        cls,
        values: Optional[Mapping[str, object]] = None,
    ) -> Optional["TargetAvailabilityMemoryProbe"]:
        source = values if values is not None else os.environ
        if not _enabled(source.get(PROBE_FLAG)):
            return None
        configured = str(source.get(STATUS_FILE_KEY) or DEFAULT_STATUS_FILE).strip()
        try:
            return cls(status_file=configured)
        except (OSError, TypeError, ValueError):
            return None

    @property
    def status_file(self) -> Path:
        return self._status_file

    def set_runtime_state(self, *, writer_enabled: bool, shadow_enabled: bool) -> None:
        self._writer_enabled = bool(writer_enabled)
        self._shadow_enabled = bool(shadow_enabled)

    def _restore_previous_aggregate(self) -> None:
        """Carry only the prior fixed summary across one-run subprocesses."""

        try:
            if self._status_file.stat().st_size > MAX_SNAPSHOT_BYTES:
                return
            payload = json.loads(self._status_file.read_text(encoding="utf-8"))
            candidate = payload.get("current_run") if isinstance(payload, dict) else None
            if not isinstance(candidate, dict):
                return
            restored = _empty_summary()
            for key in restored:
                value = candidate.get(key)
                if isinstance(restored[key], int):
                    restored[key] = max(0, int(value or 0))
                else:
                    restored[key] = str(value or "")[:80]
            restored["payload_retained_count"] = 0
            self._previous = restored
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return

    def _reset_if_operator_removed_snapshot(self) -> None:
        if self._snapshot_written and not self._status_file.exists():
            self._current = _empty_summary()
            self._previous = _empty_summary()
            self._snapshot_written = False
            self._snapshot_error_count = 0

    def start_hook(self, *, account_id: str, run_id: object, stage: str) -> int:
        self._reset_if_operator_removed_snapshot()
        run_hash = _run_hash(run_id)
        account = str(account_id or "").strip().lower()[:80]
        if self._current["capture_attempt_count"] and (
            self._current["last_run_id_hash"] != run_hash
            or self._current["last_account_id"] != account
        ):
            self._previous = dict(self._current)
            self._current = _empty_summary()
        self._current["capture_attempt_count"] += 1
        self._current["last_run_id_hash"] = run_hash
        self._current["last_account_id"] = account
        self._current["last_stage"] = _safe_code(stage, "unknown")
        self._hook_cpu_started_ns = time.process_time_ns()
        self._hook_memory_before_bytes = _peak_rss_bytes()
        return time.perf_counter_ns()

    def record_observation(self, observation: object) -> bool:
        """Validate serialization without retaining either payload or object."""

        self._current["observation_created_count"] += 1
        try:
            serializer = getattr(observation, "to_json")
            serialized = serializer()
            decoded = json.loads(serialized)
            if not isinstance(decoded, dict):
                raise ValueError("observation_serialization_not_object")
            self._current["observation_valid_count"] += 1
            self._current["last_stage"] = _safe_code(
                getattr(observation, "observation_stage", ""), "unknown"
            )
            reasons = getattr(observation, "reason_codes", ())
            if isinstance(reasons, (list, tuple)) and reasons:
                self._current["last_reason_code"] = _safe_code(reasons[-1])
            self._current["last_error_code"] = ""
            del decoded
            del serialized
            return True
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.record_rejected(type(exc).__name__)
            return False
        except Exception as exc:
            self.record_error(type(exc).__name__)
            return False

    def record_rejected(self, reason: object) -> None:
        self._current["observation_rejected_count"] += 1
        self._current["last_error_code"] = _safe_code(reason, "observation_rejected")

    def record_error(self, reason: object) -> None:
        self._current["observation_error_count"] += 1
        self._current["last_error_code"] = _safe_code(reason, "observation_error")

    def finish_hook(self, started_ns: int) -> None:
        elapsed = max(0, time.perf_counter_ns() - int(started_ns))
        cpu_elapsed = max(0, time.process_time_ns() - self._hook_cpu_started_ns)
        memory_after = _peak_rss_bytes()
        self._current["hook_total_duration_ns"] += elapsed
        self._current["hook_max_duration_ns"] = max(
            self._current["hook_max_duration_ns"], elapsed
        )
        self._current["cpu_total_duration_ns"] += cpu_elapsed
        self._current["cpu_max_duration_ns"] = max(self._current["cpu_max_duration_ns"], cpu_elapsed)
        self._current["memory_before_bytes"] = self._hook_memory_before_bytes
        self._current["memory_peak_bytes"] = max(
            self._current["memory_peak_bytes"], self._hook_memory_before_bytes, memory_after
        )
        self._current["memory_after_bytes"] = memory_after
        # The probe never owns observation references. This invariant is not an
        # estimate: its retention capacity is structurally zero.
        self._current["payload_retained_count"] = 0
        self._persist_noexcept()

    def initialize_snapshot(self) -> None:
        """Persist an explicit zero-state operator snapshot without an observation."""

        self._persist_noexcept()

    def snapshot(self) -> Mapping[str, Any]:
        aggregate = {
            "current_run": dict(self._current),
            "previous_run": dict(self._previous),
        }
        aggregate_memory_bytes = len(
            json.dumps(aggregate, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "captured_at": _utc_now(),
            "probe_enabled": True,
            "writer_enabled": self._writer_enabled,
            "shadow_enabled": self._shadow_enabled,
            "payload_retention_capacity": 0,
            "aggregate_memory_bytes": aggregate_memory_bytes,
            "max_snapshot_bytes": MAX_SNAPSHOT_BYTES,
            "snapshot_error_count": self._snapshot_error_count,
            **aggregate,
        }

    def _encoded_snapshot(self) -> bytes:
        payload = dict(self.snapshot())
        payload["snapshot_size_bytes"] = 0
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        payload["snapshot_size_bytes"] = len(encoded)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        payload["snapshot_size_bytes"] = len(encoded)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise ValueError("memory_probe_snapshot_too_large")
        return encoded

    def _persist_noexcept(self) -> None:
        temp_path: Optional[str] = None
        try:
            self._status_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            encoded = self._encoded_snapshot()
            descriptor, temp_path = tempfile.mkstemp(
                prefix=".%s." % self._status_file.name,
                dir=str(self._status_file.parent),
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._status_file)
            temp_path = None
            self._snapshot_written = True
        except Exception:
            self._snapshot_error_count += 1
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def cleanup(self) -> None:
        try:
            self._status_file.unlink()
        except FileNotFoundError:
            pass
        self._current = _empty_summary()
        self._previous = _empty_summary()
        self._snapshot_written = False
        self._snapshot_error_count = 0


__all__ = [
    "DEFAULT_STATUS_FILE",
    "MAX_SNAPSHOT_BYTES",
    "PROBE_FLAG",
    "STATUS_FILE_KEY",
    "TargetAvailabilityMemoryProbe",
]


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read or reset the bounded Gate 4B operator snapshot.")
    parser.add_argument("action", choices=("initialize", "status", "reset"))
    parser.add_argument("--status-file", default=DEFAULT_STATUS_FILE)
    args = parser.parse_args(argv)
    path = Path(args.status_file)
    if not path.is_absolute():
        parser.error("--status-file must be absolute")
    if args.action == "reset":
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return 0
    if args.action == "initialize":
        probe = TargetAvailabilityMemoryProbe(status_file=str(path))
        probe.initialize_snapshot()
        return 0 if path.exists() else 1
    try:
        if path.stat().st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError("memory_probe_snapshot_too_large")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("memory_probe_snapshot_invalid")
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"available": False, "reason": _safe_code(type(exc).__name__)}))
        return 1
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
