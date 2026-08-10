from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CURRENT_LINK = Path("/Users/admin/phonefarm-worker-current")
DEFAULT_RELEASES_DIR = Path("/Users/admin/phonefarm-worker-releases")
DEFAULT_RUNTIME_HOME = Path("/Users/admin/phonefarm-runtime")
DEFAULT_LEGACY_ROOT = Path("/Users/admin/instagram-worker-python")

DISPATCHER_LABEL = "com.boost.phonefarm.dispatcher"
HEARTBEAT_LABEL = "com.boost.phonefarm.device-heartbeat"
NOTIFIER_LABEL = "com.boost.phonefarm.incident-notifier"


@dataclass(frozen=True)
class RuntimePaths:
    current_link: Path
    releases_dir: Path
    runtime_home: Path
    legacy_root: Path

    @property
    def log_dir(self) -> Path:
        return self.runtime_home / "logs"

    @property
    def run_dir(self) -> Path:
        return self.runtime_home / "run"

    @property
    def env_dir(self) -> Path:
        return self.runtime_home / "env"


@dataclass(frozen=True)
class RuntimeRoot:
    ok: bool
    status: str
    active_root: str
    resolved_root: str
    commit: str
    reason: str = ""


def _path_from_env(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser()


def runtime_paths() -> RuntimePaths:
    return RuntimePaths(
        current_link=_path_from_env("PHONEFARM_RUNTIME_CURRENT_LINK", DEFAULT_CURRENT_LINK),
        releases_dir=_path_from_env("PHONEFARM_RUNTIME_RELEASES_DIR", DEFAULT_RELEASES_DIR),
        runtime_home=_path_from_env("PHONEFARM_RUNTIME_HOME", DEFAULT_RUNTIME_HOME),
        legacy_root=_path_from_env("PHONEFARM_RUNTIME_LEGACY_ROOT", DEFAULT_LEGACY_ROOT),
    )


def _safe_resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _git_commit(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def resolve_runtime_root(paths: RuntimePaths | None = None) -> RuntimeRoot:
    paths = paths or runtime_paths()
    link = paths.current_link
    if not link.exists() and not link.is_symlink():
        return RuntimeRoot(False, "runtime_root_invalid", str(link), "", "", "active_root_missing")

    resolved = _safe_resolve(link)
    legacy = _safe_resolve(paths.legacy_root)
    releases = _safe_resolve(paths.releases_dir)
    try:
        resolved.relative_to(releases)
    except ValueError:
        return RuntimeRoot(False, "runtime_root_invalid", str(link), str(resolved), "", "active_root_not_in_releases_dir")

    if resolved == legacy:
        return RuntimeRoot(False, "runtime_root_invalid", str(link), str(resolved), "", "legacy_root_forbidden")

    required = [
        resolved / "account_run_request_consumer.py",
        resolved / "device_heartbeat_publisher.py",
        resolved / "scripts" / "run_control_dispatcher_service.sh",
        resolved / "scripts" / "device_heartbeat_service.sh",
    ]
    missing = [str(path.relative_to(resolved)) for path in required if not path.exists()]
    if missing:
        return RuntimeRoot(False, "runtime_root_invalid", str(link), str(resolved), "", f"missing:{','.join(missing)}")

    commit = _git_commit(resolved)
    if not commit:
        return RuntimeRoot(False, "runtime_root_invalid", str(link), str(resolved), "", "commit_unresolved")

    return RuntimeRoot(True, "valid", str(link), str(resolved), commit, "")


def _json_line(stdout: str) -> dict[str, Any] | None:
    for line in reversed(str(stdout or "").splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            continue
        try:
            value = json.loads(stripped)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return None


def _component_env(paths: RuntimePaths, root: RuntimeRoot, component: str) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    if component == "dispatcher":
        env.setdefault("RUN_CONTROL_DISPATCHER_ENV_FILE", str(paths.env_dir / "run-control-dispatcher.env"))
        env["RUN_CONTROL_DISPATCHER_LOG_DIR"] = str(paths.log_dir / "run-control-dispatcher")
        env["RUN_CONTROL_DISPATCHER_RUN_DIR"] = str(paths.run_dir / "run-control-dispatcher")
    elif component == "heartbeat":
        env.setdefault("DEVICE_HEARTBEAT_ENV_FILE", str(paths.env_dir / "device-heartbeat.env"))
        env["DEVICE_HEARTBEAT_LOG_DIR"] = str(paths.log_dir / "device-heartbeat-service")
        env["DEVICE_HEARTBEAT_RUN_DIR"] = str(paths.run_dir / "device-heartbeat-service")
        env.setdefault("DEVICE_HEARTBEAT_INTERVAL_SECONDS", "60")
    elif component == "notifier":
        env.setdefault("INCIDENT_NOTIFIER_ENV_FILE", str(paths.env_dir / "incident-notifier.env"))
        env["INCIDENT_NOTIFIER_LOG_DIR"] = str(paths.log_dir / "incident-notifier")
        env["INCIDENT_NOTIFIER_RUN_DIR"] = str(paths.run_dir / "incident-notifier")
        env.setdefault("INCIDENT_NOTIFIER_INTERVAL_SECONDS", "60")
    env["PHONEFARM_ACTIVE_ROOT"] = root.resolved_root
    env["PHONEFARM_ACTIVE_COMMIT"] = root.commit
    return env


def _component_wrapper_name(component: str) -> str:
    if component == "dispatcher":
        return "run_control_dispatcher_service.sh"
    if component == "notifier":
        return "incident_notifier_service.sh"
    return "device_heartbeat_service.sh"


def _component_launchd_label(component: str) -> str:
    if component == "dispatcher":
        return DISPATCHER_LABEL
    if component == "notifier":
        return NOTIFIER_LABEL
    return HEARTBEAT_LABEL


# Long-lived service commands must never run under the bounded control
# timeout: doing so kills the foreground service every `timeout` seconds
# (observed as the ~60s running -> SIGTERM -> ~30s stopped launchd loop).
LONG_LIVED_COMMANDS = {"serve", "start-foreground"}


def _run_wrapper(component: str, command: str, args: list[str], *, timeout: int = 60) -> dict[str, Any]:
    if command in LONG_LIVED_COMMANDS:
        return {
            "ok": False,
            "status": "unknown",
            "lastError": "long_lived_command_requires_exec",
            "message": f"Command '{command}' is long-lived; use serve_component() (exec), never a timed subprocess.",
        }
    paths = runtime_paths()
    root = resolve_runtime_root(paths)
    if not root.ok:
        return _runtime_root_payload(root, component=component, command=command)

    wrapper_name = _component_wrapper_name(component)
    wrapper = Path(root.resolved_root) / "scripts" / wrapper_name
    env = _component_env(paths, root, component)
    wrapper_args = list(args)
    if command == "status" and "--json" not in wrapper_args:
        wrapper_args.append("--json")
    try:
        proc = subprocess.run(
            [str(wrapper), command, *wrapper_args],
            cwd=root.resolved_root,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _with_root(root, {"ok": False, "status": "degraded", "lastError": f"{component}_command_timeout"})

    parsed = _json_line(proc.stdout)
    if parsed is None:
        parsed = {
            "ok": proc.returncode == 0,
            "status": "running" if proc.returncode == 0 and command in {"start", "resume", "restart"} else "unknown",
            "message": (proc.stdout or proc.stderr or "").strip()[:600],
        }
    parsed.setdefault("ok", proc.returncode == 0)
    parsed["exitCode"] = proc.returncode
    if proc.stderr:
        parsed.setdefault("lastError", proc.stderr.strip()[:600])
    enriched = _with_root(root, parsed)
    if command == "status":
        enriched = _detect_component_mismatch(component, enriched, root)
    return enriched


def serve_component(component: str) -> int:
    """launchd entry point for long-lived services.

    Resolves the canonical runtime root, then *exec*s the release wrapper in
    foreground mode. There is no Python parent left to expire: the wrapper
    (and its consumer/publisher child) lives until launchd stops it.
    """
    paths = runtime_paths()
    root = resolve_runtime_root(paths)
    if not root.ok:
        print(json.dumps(_runtime_root_payload(root, component=component, command="serve"), sort_keys=True))
        return 2
    wrapper = Path(root.resolved_root) / "scripts" / _component_wrapper_name(component)
    if not wrapper.exists():
        # Older releases may not ship this component's wrapper (e.g. notifier
        # before P2): report a structured error instead of an execve crash loop.
        print(json.dumps({
            "ok": False,
            "status": "runtime_root_invalid",
            "component": component,
            "command": "serve",
            "lastError": f"missing_component_wrapper:{wrapper.name}",
        }, sort_keys=True))
        return 2
    env = _component_env(paths, root, component)
    os.chdir(root.resolved_root)
    os.execve(str(wrapper), [str(wrapper), "start"], env)
    return 2  # unreachable; keeps the signature honest for tests


def _launchctl_kickstart(label: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"],
            check=False,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:  # includes TimeoutExpired
        return False, str(exc)[:200]
    return proc.returncode == 0, (proc.stderr or proc.stdout or "").strip()[:200]


def control_start(component: str) -> dict[str, Any]:
    """Short, idempotent start used by BotApp and operators.

    Never spawns the foreground worker itself: if the service is already
    running it reports `running`; otherwise it asks launchd to (re)start the
    long-lived `serve` job and returns a structured, non-blocking state.
    """
    status = _run_wrapper(component, "status", [], timeout=20)
    if status.get("processRunning"):
        return {
            **status,
            "ok": True,
            "command": "start",
            "message": f"{component}_already_running pid={status.get('pid')}",
        }
    label = _component_launchd_label(component)
    kicked, kick_detail = _launchctl_kickstart(label)
    refreshed = _run_wrapper(component, "status", [], timeout=20)
    launch_requested_ok = kicked or bool(refreshed.get("processRunning"))
    return {
        **refreshed,
        "ok": bool(refreshed.get("ok")) and launch_requested_ok,
        "command": "start",
        "launchdKickstart": kicked,
        "message": (
            f"{component}_start_requested label={label}"
            if launch_requested_ok
            else f"{component}_start_request_failed detail={kick_detail}"
        ),
    }


def _runtime_root_payload(root: RuntimeRoot, *, component: str = "runtime", command: str = "status") -> dict[str, Any]:
    return {
        "ok": False,
        "status": root.status,
        "component": component,
        "command": command,
        "runtimeRootOk": False,
        "activeRoot": root.active_root,
        "resolvedRoot": root.resolved_root or None,
        "runtimeCommit": root.commit or None,
        "lastError": root.reason,
        "message": f"Runtime root invalid: {root.reason}",
    }


def _with_root(root: RuntimeRoot, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **payload,
        "runtimeRootOk": root.ok,
        "activeRoot": root.active_root,
        "resolvedRoot": root.resolved_root,
        "runtimeCommit": root.commit,
    }


def _ps_rows() -> list[tuple[int, int, str]]:
    try:
        out = subprocess.check_output(["ps", "-axo", "pid,ppid,command"], text=True, errors="replace")
    except Exception:
        return []
    rows: list[tuple[int, int, str]] = []
    for line in out.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def _pid_cwd(pid: int) -> str:
    try:
        out = subprocess.check_output(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def _detect_component_mismatch(component: str, payload: dict[str, Any], root: RuntimeRoot) -> dict[str, Any]:
    if component == "dispatcher":
        needle = "account_run_request_consumer.py"
    elif component == "notifier":
        needle = "incident_notification_service.py"
    else:
        needle = "device_heartbeat_publisher.py"
    matching: list[dict[str, Any]] = []
    for pid, ppid, command in _ps_rows():
        if needle not in command:
            continue
        cwd = _pid_cwd(pid)
        matching.append({"pid": pid, "ppid": ppid, "cwd": cwd, "command": command[:240]})

    active_root = _safe_resolve(Path(root.resolved_root))
    foreign = [
        row
        for row in matching
        if row.get("cwd") and _safe_resolve(Path(str(row["cwd"]))) != active_root
    ]
    if foreign:
        return {
            **payload,
            "ok": False,
            "status": "runtime_root_mismatch",
            "lastError": "service_running_from_non_active_root",
            "message": "Service process exists, but not from the active runtime root.",
            "processes": matching,
        }

    if payload.get("processRunning"):
        real_root = str(payload.get("processRoot") or "")
        if real_root and _safe_resolve(Path(real_root)) != active_root:
            return {**payload, "ok": False, "status": "runtime_root_mismatch", "processes": matching}
        return {**payload, "processes": matching}

    return {**payload, "processes": matching}


def runtime_status() -> dict[str, Any]:
    root = resolve_runtime_root()
    if not root.ok:
        return _runtime_root_payload(root)
    return _with_root(root, {"ok": True, "status": "valid", "message": "Active runtime root is valid."})


def scheduler_status() -> dict[str, Any]:
    paths = runtime_paths()
    root = resolve_runtime_root(paths)
    if not root.ok:
        return _runtime_root_payload(root, component="scheduler")
    log_path = paths.log_dir / "run-control-dispatcher" / "dispatcher.log"
    last_tick: dict[str, Any] | None = None
    if log_path.exists():
        try:
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
                if "auto_restart_dispatcher_tick_" not in line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                last_tick = parsed
        except Exception:
            last_tick = None

    status = "disabled_by_config" if (last_tick or {}).get("reason") == "scheduler_disabled" else "unknown"
    if last_tick and status == "unknown":
        if last_tick.get("eligible_count") == 0 and not last_tick.get("enqueued_count"):
            status = "no_eligible_accounts"
        elif last_tick.get("enqueued_count"):
            status = "running"
        elif "failed" in str(last_tick.get("event") or ""):
            status = "error"
        else:
            status = "enabled"
    return _with_root(
        root,
        {
            "ok": status not in {"error", "unknown"},
            "status": status,
            "embedded": True,
            "service": "dispatcher",
            "lastTick": last_tick,
            "lastSkipReason": (last_tick or {}).get("reason"),
            "candidateCount": (last_tick or {}).get("evaluated_count"),
            "eligibleCount": (last_tick or {}).get("eligible_count"),
            "manualOnlyExcludedBy": "backend_canonical_auto_restart_tick",
            "message": "Scheduler tick is embedded in the run-control dispatcher.",
        },
    )


def switch_release(target: str) -> dict[str, Any]:
    paths = runtime_paths()
    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = paths.releases_dir / target
    candidate = RuntimeRoot(True, "valid", str(paths.current_link), str(_safe_resolve(target_path)), _git_commit(target_path))
    validation = resolve_runtime_root(RuntimePaths(target_path, paths.releases_dir, paths.runtime_home, paths.legacy_root))
    if not validation.ok:
        return _runtime_root_payload(validation, command="switch-release")
    previous = _safe_resolve(paths.current_link) if (paths.current_link.exists() or paths.current_link.is_symlink()) else None
    tmp = paths.current_link.with_name(f"{paths.current_link.name}.tmp")
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(validation.resolved_root)
    os.replace(tmp, paths.current_link)
    return _with_root(validation, {"ok": True, "status": "switched", "previousRoot": str(previous) if previous else None})


def _print(payload: dict[str, Any], json_mode: bool) -> int:
    if json_mode:
        print(json.dumps(payload, sort_keys=True))
    else:
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True)
            print(f"{key}={value}")
    return 0 if payload.get("ok") else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canonical Phone Farm runtime controller.")
    parser.add_argument("target", nargs="?", default="status", choices=["status", "validate", "dispatcher", "heartbeat", "notifier", "scheduler", "switch-release"])
    parser.add_argument("command", nargs="?", default="status")
    parser.add_argument("extra", nargs="*")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.target in {"status", "validate"}:
        return _print(runtime_status(), args.json)
    if args.target in {"dispatcher", "heartbeat", "notifier"}:
        if args.command == "serve":
            # launchd-only entry point: exec the release wrapper, no timeout.
            return serve_component(args.target)
        if args.command == "start":
            # Short idempotent control command (BotApp/operator): delegates the
            # actual long-lived process to the launchd `serve` job.
            return _print(control_start(args.target), args.json)
        return _print(_run_wrapper(args.target, args.command, args.extra), args.json)
    if args.target == "scheduler":
        return _print(scheduler_status(), args.json)
    if args.target == "switch-release":
        if not args.command or args.command == "status":
            return _print({"ok": False, "status": "runtime_root_invalid", "lastError": "missing_release_target"}, args.json)
        return _print(switch_release(args.command), args.json)
    return _print({"ok": False, "status": "unknown", "lastError": "unsupported_command"}, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
