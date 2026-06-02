"""Publish local ADB device heartbeats to Supabase.

This CLI is intentionally read-only against Android devices: it runs
`adb devices -l` and optional safe shell reads, then upserts device_heartbeats.
It never starts Instagram, taps UI, claims work, or creates assignments.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
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
    args = parser.parse_args()

    load_env_file(args.env_file)
    observations = adb_devices_l(adb_path=args.adb)
    allowed_serials = {str(serial).strip() for serial in args.serial if str(serial).strip()}
    if allowed_serials:
        observations = [item for item in observations if item.adb_serial in allowed_serials]

    if args.include_battery:
        observations = [
            AdbDeviceObservation(
                **{
                    **item.__dict__,
                    "battery_pct": read_battery_level(item.adb_serial, adb_path=args.adb) if item.adb_state == "device" else None,
                }
            )
            for item in observations
        ]

    summary = publish_observations(
        observations,
        supabase_client.list_phone_devices_for_heartbeat(),
        host_label=str(args.host_label).strip() or socket.gethostname(),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
