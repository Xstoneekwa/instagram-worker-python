"""Publish local ADB device heartbeats to Supabase.

This CLI is intentionally read-only against Android devices: it runs
`adb devices -l` and optional safe shell reads, then upserts device_heartbeats.
It never starts Instagram, taps UI, claims work, or creates assignments.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import runtime_heartbeat
import supabase_client

ADB_TO_HEARTBEAT_STATUS = {
    "device": "online",
    "offline": "offline",
    "unauthorized": "unauthorized",
}
SAFE_ADB_DETAIL_KEYS = {"model", "product", "device", "transport_id"}
DEFAULT_SERVE_INTERVAL_SECONDS = 60
MIN_SERVE_INTERVAL_SECONDS = 15
MAX_SERVE_INTERVAL_SECONDS = 300
_shutdown_requested = False


@dataclass(frozen=True)
class AdbDeviceObservation:
    adb_serial: str
    adb_state: str
    model: str | None = None
    product: str | None = None
    device: str | None = None
    transport_id: str | None = None
    battery_pct: int | None = None


def load_env_file(path: str = ".env") -> None:
    """Load simple KEY=VALUE lines without printing or overriding env vars."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def parse_adb_devices_l(output: str) -> list[AdbDeviceObservation]:
    observations: list[AdbDeviceObservation] = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("list of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial = parts[0].strip()
        state = parts[1].strip().lower()
        if not serial or serial.startswith("*"):
            continue

        details: dict[str, str] = {}
        for part in parts[2:]:
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            if key in SAFE_ADB_DETAIL_KEYS and value.strip():
                details[key] = value.strip()

        observations.append(
            AdbDeviceObservation(
                adb_serial=serial,
                adb_state=state,
                model=details.get("model"),
                product=details.get("product"),
                device=details.get("device"),
                transport_id=details.get("transport_id"),
            )
        )
    return observations


def adb_devices_l(*, adb_path: str = "adb", timeout_s: float = 10.0) -> list[AdbDeviceObservation]:
    completed = subprocess.run(
        [adb_path, "devices", "-l"],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("adb devices -l failed")
    return parse_adb_devices_l(completed.stdout)


def parse_battery_level(output: str) -> int | None:
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line.lower().startswith("level:"):
            continue
        raw_value = line.split(":", 1)[1].strip()
        try:
            value = int(raw_value)
        except ValueError:
            return None
        if 0 <= value <= 100:
            return value
    return None


def read_battery_level(serial: str, *, adb_path: str = "adb", timeout_s: float = 5.0) -> int | None:
    completed = subprocess.run(
        [adb_path, "-s", serial, "shell", "dumpsys", "battery"],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return parse_battery_level(completed.stdout)


def phone_devices_by_serial(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for row in rows:
        serial = str(row.get("adb_serial") or "").strip()
        device_id = str(row.get("id") or "").strip()
        if serial and device_id and serial not in mapped:
            mapped[serial] = row
    return mapped


def heartbeat_status_for_adb_state(adb_state: str) -> str:
    return ADB_TO_HEARTBEAT_STATUS.get(str(adb_state or "").strip().lower(), "unknown")


def build_heartbeat_metadata(observation: AdbDeviceObservation) -> dict[str, str]:
    metadata = {
        "source": "local_adb_devices_l",
        "adb_state": observation.adb_state,
    }
    for key in ("model", "product", "device", "transport_id"):
        value = getattr(observation, key)
        if value:
            metadata[key] = value
    return metadata


def write_cycle_state(state_file: str | None, payload: dict[str, Any]) -> None:
    if not state_file:
        return
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def run_publish_cycle(
    *,
    adb_path: str = "adb",
    host_label: str,
    include_battery: bool = False,
    allowed_serials: set[str] | None = None,
    dry_run: bool = False,
    state_file: str | None = None,
) -> dict[str, Any]:
    observations = adb_devices_l(adb_path=adb_path)
    if allowed_serials:
        observations = [item for item in observations if item.adb_serial in allowed_serials]

    if include_battery:
        observations = [
            AdbDeviceObservation(
                **{
                    **item.__dict__,
                    "battery_pct": read_battery_level(item.adb_serial, adb_path=adb_path) if item.adb_state == "device" else None,
                }
            )
            for item in observations
        ]

    try:
        phone_rows = supabase_client.list_phone_devices_for_heartbeat()
    except Exception as exc:
        summary = {
            "ok": False,
            "reason": "backend_unavailable",
            "error": str(exc),
            "observed_count": len(observations),
            "published_count": 0,
            "skipped_count": 0,
            "published": [],
            "skipped": [],
        }
        write_cycle_state(state_file, {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ok": False,
            "reason": "backend_unavailable",
            "observed_count": len(observations),
            "published_count": 0,
            "skipped_count": 0,
            "physical_phones_seen": 0,
        })
        return summary

    summary = publish_observations(
        observations,
        phone_rows,
        host_label=host_label,
        dry_run=dry_run,
    )
    summary["ok"] = True
    physical_seen = sum(
        1
        for item in summary.get("published", [])
        if str(item.get("heartbeat_status") or "") == "online"
    )
    write_cycle_state(state_file, {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": True,
        "reason": "cycle_completed",
        "observed_count": int(summary.get("observed_count") or 0),
        "published_count": int(summary.get("published_count") or 0),
        "skipped_count": int(summary.get("skipped_count") or 0),
        "physical_phones_seen": physical_seen,
    })
    return summary


def _handle_shutdown(signum: int, _frame: object | None) -> None:
    del signum
    global _shutdown_requested
    _shutdown_requested = True


def serve_forever(
    *,
    adb_path: str,
    host_label: str,
    include_battery: bool,
    allowed_serials: set[str] | None,
    interval_seconds: int,
    state_file: str | None,
) -> int:
    global _shutdown_requested
    _shutdown_requested = False
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    interval = max(MIN_SERVE_INTERVAL_SECONDS, min(MAX_SERVE_INTERVAL_SECONDS, int(interval_seconds or DEFAULT_SERVE_INTERVAL_SECONDS)))
    while not _shutdown_requested:
        started = time.monotonic()
        summary = run_publish_cycle(
            adb_path=adb_path,
            host_label=host_label,
            include_battery=include_battery,
            allowed_serials=allowed_serials,
            state_file=state_file,
        )
        print(json.dumps({"mode": "serve", **summary}, sort_keys=True), flush=True)
        if _shutdown_requested:
            break
        elapsed = time.monotonic() - started
        sleep_for = max(1.0, interval - elapsed)
        deadline = time.monotonic() + sleep_for
        while time.monotonic() < deadline and not _shutdown_requested:
            time.sleep(min(1.0, deadline - time.monotonic()))
    return 0


def publish_observations(
    observations: list[AdbDeviceObservation],
    phone_rows: list[dict[str, Any]],
    *,
    host_label: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    by_serial = phone_devices_by_serial(phone_rows)
    published: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for observation in observations:
        phone = by_serial.get(observation.adb_serial)
        if not phone:
            skipped.append({"adb_serial": observation.adb_serial, "reason": "phone_device_not_registered"})
            continue

        device_id = str(phone.get("id") or "").strip()
        status = heartbeat_status_for_adb_state(observation.adb_state)
        row = {
            "device_id": device_id,
            "adb_serial": observation.adb_serial,
            "host_machine": host_label,
            "status": status,
            "battery_pct": observation.battery_pct,
            "metadata": build_heartbeat_metadata(observation),
        }
        if not dry_run:
            # This CLI is an explicit operator action; bypass the runtime throttle/env gate
            # without enabling any worker, dispatcher, or Instagram runtime path.
            runtime_heartbeat.config.RUNTIME_HEARTBEATS_ENABLED = True
            result = runtime_heartbeat.heartbeat_device(
                device_id,
                status=status,
                adb_serial=observation.adb_serial,
                host_machine=host_label,
                metadata=row["metadata"],
                force=True,
            )
            if not result.get("published"):
                skipped.append({"adb_serial": observation.adb_serial, "reason": str(result.get("reason") or "publish_failed")})
                continue
            if observation.battery_pct is not None:
                supabase_client.upsert_device_heartbeat({**row, "last_seen_at": result["row"].get("last_seen_at")})

        published.append({
            "adb_serial": observation.adb_serial,
            "device_id": device_id,
            "adb_state": observation.adb_state,
            "heartbeat_status": status,
            "battery_pct": observation.battery_pct,
        })

    return {
        "dry_run": dry_run,
        "observed_count": len(observations),
        "published_count": len(published),
        "skipped_count": len(skipped),
        "published": published,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish safe local ADB device heartbeats.")
    parser.add_argument("--env-file", default=".env", help="Optional local env file with Supabase server settings.")
    parser.add_argument("--adb", default="adb", help="ADB executable path.")
    parser.add_argument("--host-label", default=socket.gethostname(), help="Host label stored in device_heartbeats.host_machine.")
    parser.add_argument("--serial", action="append", default=[], help="Only publish these ADB serials. Repeatable.")
    parser.add_argument("--include-battery", action="store_true", help="Also read safe battery level via dumpsys battery for online devices.")
    parser.add_argument("--dry-run", action="store_true", help="Read ADB and DB mapping but do not write heartbeats.")
    parser.add_argument("--serve", action="store_true", help="Run as a persistent publisher loop for local supervision.")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_SERVE_INTERVAL_SECONDS, help="Serve loop interval in seconds.")
    parser.add_argument("--state-file", default="", help="Optional JSON state file updated after each serve cycle.")
    args = parser.parse_args()

    load_env_file(args.env_file)
    allowed_serials = {str(serial).strip() for serial in args.serial if str(serial).strip()} or None
    host_label = str(args.host_label).strip() or socket.gethostname()
    state_file = str(args.state_file).strip() or None

    if args.serve:
        return serve_forever(
            adb_path=args.adb,
            host_label=host_label,
            include_battery=bool(args.include_battery),
            allowed_serials=allowed_serials,
            interval_seconds=int(args.interval_seconds or DEFAULT_SERVE_INTERVAL_SECONDS),
            state_file=state_file,
        )

    summary = run_publish_cycle(
        adb_path=args.adb,
        host_label=host_label,
        include_battery=bool(args.include_battery),
        allowed_serials=allowed_serials,
        dry_run=bool(args.dry_run),
        state_file=state_file,
    )
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
