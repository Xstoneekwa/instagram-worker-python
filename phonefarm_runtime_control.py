from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from follow60_deployment_gate_v1 import verify_deployment_candidate


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


def _runtime_secret_env(paths: RuntimePaths) -> dict[str, str]:
    """Load only the two service credentials needed for a read-only gate."""
    values = {
        "SUPABASE_URL": str(os.environ.get("SUPABASE_URL") or "").strip(),
        "SUPABASE_SERVICE_ROLE_KEY": str(
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
        ).strip(),
    }
    env_file = paths.env_dir / "run-control-dispatcher.env"
    if env_file.exists() and (not values["SUPABASE_URL"] or not values["SUPABASE_SERVICE_ROLE_KEY"]):
        try:
            for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key not in values or values[key]:
                    continue
                values[key] = value.strip().strip("'\"")
        except Exception:
            pass
    return values


def _rest_select_ids(
    *,
    base_url: str,
    service_key: str,
    table: str,
    identity_column: str,
    filters: dict[str, str],
) -> list[dict[str, Any]]:
    query = {"select": identity_column, "limit": "1", **filters}
    url = f"{base_url.rstrip('/')}/rest/v1/{table}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"unexpected_rest_payload:{table}")
    return [row for row in value if isinstance(row, dict)]


def deployment_zero_gate(paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Fail-closed production gate shared by switch and dispatcher restart."""
    paths = paths or runtime_paths()
    secrets = _runtime_secret_env(paths)
    base_url = secrets["SUPABASE_URL"]
    service_key = secrets["SUPABASE_SERVICE_ROLE_KEY"]
    if not base_url or not service_key:
        return {
            "ok": False,
            "status": "deployment_gate_unavailable",
            "reason": "supabase_runtime_credentials_missing",
        }
    now_iso = datetime.now(timezone.utc).isoformat()
    specs = {
        "account_run_requests": ("id", {"status": "in.(queued,claimed,starting,running)"}),
        "ig_runs": ("id", {"status": "in.(pending,running)"}),
        "auto_restart_device_locks": ("device_id", {"lease_expires_at": f"gt.{now_iso}"}),
        "auto_restart_tick_locks": ("idempotency_key", {"status": "eq.started"}),
    }
    counts: dict[str, int] = {}
    try:
        for table, (identity_column, filters) in specs.items():
            counts[table] = len(
                _rest_select_ids(
                    base_url=base_url,
                    service_key=service_key,
                    table=table,
                    identity_column=identity_column,
                    filters=filters,
                )
            )
    except Exception as exc:
        return {
            "ok": False,
            "status": "deployment_gate_unavailable",
            "reason": f"deployment_gate_read_failed:{type(exc).__name__}",
            "counts": counts,
        }
    blockers = [name for name, count in counts.items() if count != 0]
    return {
        "ok": not blockers,
        "status": "ready" if not blockers else "deployment_gate_blocked",
        "reason": "zero_gate" if not blockers else "active_runtime_work_present",
        "counts": counts,
        "blockers": blockers,
    }


def _safe_resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _git_commit(root: Path) -> str:
    """Read HEAD from one already-validated release root.

    Physical Follow60 releases are deliberately root-owned.  Git therefore
    requires an explicit safe.directory exception even for read-only commands.
    Keep that exception process-scoped and bind it to the canonical root only;
    callers must validate release-directory containment before calling here.
    """
    try:
        exact_root = Path(root).expanduser().resolve(strict=True)
        proc = subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={exact_root}",
                "-C",
                str(exact_root),
                "rev-parse",
                "--short",
                "HEAD",
            ],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _git_full_commit(root: Path) -> str:
    try:
        exact_root = Path(root).expanduser().resolve(strict=True)
        proc = subprocess.run(
            [
                "git", "-c", f"safe.directory={exact_root}", "-C", str(exact_root),
                "rev-parse", "HEAD",
            ],
            check=False,
            text=True,
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _canonical_json_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_root_not_object:{path.name}")
    return value


def _is_user_immutable(path: Path) -> bool:
    immutable_flag = getattr(stat, "UF_IMMUTABLE", 0x00000002)
    return bool(path.stat().st_flags & immutable_flag)


def _validate_release_evidence(
    release_root: Path, integrity: dict[str, Any]
) -> dict[str, Any]:
    evidence_dir = release_root / ".deployment"
    paths = {
        "build": evidence_dir / "candidate-build-receipt.json",
        "migration": evidence_dir / "migration-attestation.json",
        "lineage": evidence_dir / "lineage-gate-receipt.json",
    }
    try:
        if any(not path.is_file() for path in paths.values()):
            raise ValueError("promotion_evidence_missing")
        if any(not _is_user_immutable(path) for path in paths.values()):
            raise ValueError("promotion_evidence_not_immutable")
        build = _read_json_object(paths["build"])
        migration = _read_json_object(paths["migration"])
        lineage = _read_json_object(paths["lineage"])
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:240] or type(exc).__name__}

    candidate_sha = _git_full_commit(release_root)
    if not candidate_sha or candidate_sha != integrity.get("candidate_sha"):
        return {"ok": False, "reason": "release_evidence_candidate_unresolved"}
    required_build = {
        "candidate_sha": candidate_sha,
        "release_sha": candidate_sha,
        "manifest_certified_sha": candidate_sha,
        "manifest_sha256": integrity.get("manifest_sha256"),
        "candidate_worktree_clean": True,
        "uncommitted_protected_diff": False,
        "untracked_protected_file": False,
        "protected_diff_status": "PASS",
        "follow60_lock_result": "PASS",
        "signature_result": "PASS",
        "lineage_gate_result": "PASS",
    }
    for key, expected in required_build.items():
        if build.get(key) != expected:
            return {"ok": False, "reason": f"candidate_build_receipt_mismatch:{key}"}
    if migration.get("schema") != "PHONE_FARM_MIGRATION_ATTESTATION_V1":
        return {"ok": False, "reason": "migration_attestation_schema_mismatch"}
    if build.get("migration_attestation_sha256") != _canonical_json_hash(migration):
        return {"ok": False, "reason": "migration_attestation_hash_mismatch"}
    if lineage.get("status") != "PRODUCTION_LINEAGE_GATE_V1_PASS":
        return {"ok": False, "reason": "lineage_gate_receipt_not_pass"}
    if lineage.get("candidate_sha") != candidate_sha or lineage.get("worktree_clean") is not True:
        return {"ok": False, "reason": "lineage_gate_candidate_or_cleanliness_mismatch"}
    if lineage.get("migration_gate") != "PASS":
        return {"ok": False, "reason": "lineage_migration_gate_not_pass"}
    if lineage.get("migration_attestation_sha256") != _canonical_json_hash(migration):
        return {"ok": False, "reason": "lineage_migration_attestation_mismatch"}
    applied = {str(item) for item in migration.get("applied_versions") or []}
    validated = lineage.get("validated_migrations") or []
    if not validated:
        return {"ok": False, "reason": "validated_migration_set_empty"}
    if any(str(item.get("version") or "") not in applied for item in validated if isinstance(item, dict)):
        return {"ok": False, "reason": "validated_migration_not_applied"}
    return {
        "ok": True,
        "candidate_sha": candidate_sha,
        "build": build,
        "migration": migration,
        "lineage": lineage,
    }


def _stage_receipt(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".pending")
    with staged.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return staged


def _publish_immutable_receipt(staged: Path, final: Path) -> None:
    os.replace(staged, final)
    os.chmod(final, 0o444)
    immutable_flag = getattr(stat, "UF_IMMUTABLE", 0x00000002)
    os.chflags(final, immutable_flag)
    if not _is_user_immutable(final):
        raise RuntimeError("promotion_receipt_not_immutable")


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
        return _with_root(root, {
            "ok": False,
            "status": "unknown",
            "service_state": "unknown",
            "service_health": "degraded",
            "preflight_state": "unknown",
            "preflight_reason": f"{component}_command_timeout",
            "processRunning": None,
            "launchdLoaded": None,
            "processCount": None,
            "lastError": f"{component}_command_timeout",
        })

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


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _active_worker_children() -> list[dict[str, Any]] | None:
    """Return local Runner-like children; never performs network/device I/O."""
    children: list[dict[str, Any]] = []
    needles = (" runner.py", "/runner.py", " account_session.py", "/account_session.py")
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid,ppid,command"],
            text=True,
            errors="replace",
        )
    except Exception:
        return None
    rows: list[tuple[int, int, str]] = []
    for line in output.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    for pid, ppid, command in rows:
        if any(needle in f" {command}" for needle in needles):
            children.append({"pid": pid, "ppid": ppid, "command": command[:240]})
    return children


def _acquire_autoheal_lock(paths: RuntimePaths, *, stale_seconds: float = 30.0) -> tuple[Path, bool]:
    lock_dir = paths.run_dir / "dispatcher-autoheal.lock"
    owner_file = lock_dir / "owner.json"
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(2):
        try:
            lock_dir.mkdir(mode=0o700)
            owner_file.write_text(
                json.dumps({"pid": os.getpid(), "created_epoch": time.time()}),
                encoding="utf-8",
            )
            return lock_dir, True
        except FileExistsError:
            try:
                owner = json.loads(owner_file.read_text(encoding="utf-8"))
                owner_pid = int(owner.get("pid") or 0)
                created_epoch = float(owner.get("created_epoch") or 0.0)
                age = time.time() - created_epoch
                if created_epoch <= 0.0 or age < 0.0:
                    age = stale_seconds + 1.0
            except Exception:
                owner_pid, age = 0, stale_seconds + 1.0
            if owner_pid > 0 and _pid_alive(owner_pid):
                return lock_dir, False
            if age <= stale_seconds:
                return lock_dir, False
            try:
                owner_file.unlink(missing_ok=True)
                lock_dir.rmdir()
            except OSError:
                return lock_dir, False
    return lock_dir, False


def _release_autoheal_lock(lock_dir: Path) -> None:
    try:
        (lock_dir / "owner.json").unlink(missing_ok=True)
        lock_dir.rmdir()
    except OSError:
        pass


def control_start(component: str) -> dict[str, Any]:
    """Short, idempotent start used by BotApp and operators.

    Never spawns the foreground worker itself: if the service is already
    running it reports `running`; otherwise it asks launchd to (re)start the
    long-lived `serve` job and returns a structured, non-blocking state.
    """
    status = _run_wrapper(component, "status", [], timeout=20)
    if status.get("service_state") == "running" and status.get("processRunning") is True:
        return {
            **status,
            "ok": True,
            "command": "start",
            "message": f"{component}_already_running pid={status.get('pid')}",
        }
    if component != "dispatcher":
        if status.get("processRunning") is not False:
            return {
                **status,
                "ok": False,
                "command": "start",
                "recoveryAction": "none",
                "message": f"{component}_recovery_refused_liveness_not_conclusively_absent",
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
    if status.get("service_state") != "stopped" or status.get("processRunning") is not False:
        return {
            **status,
            "ok": False,
            "command": "start",
            "recoveryAction": "none",
            "message": f"{component}_recovery_refused_liveness_not_conclusively_absent",
        }

    paths = runtime_paths()
    lock_dir, acquired = _acquire_autoheal_lock(paths)
    if not acquired:
        return {
            **status,
            "ok": False,
            "command": "start",
            "recoveryAction": "none",
            "message": f"{component}_recovery_already_in_progress",
        }
    try:
        revalidated = _run_wrapper(component, "status", [], timeout=20)
        if revalidated.get("service_state") != "stopped" or revalidated.get("processRunning") is not False:
            return {
                **revalidated,
                "ok": bool(revalidated.get("processRunning")),
                "command": "start",
                "recoveryAction": "none",
                "message": f"{component}_recovery_canceled_after_revalidation",
            }
        local_children = _active_worker_children()
        if local_children is None:
            return {
                **revalidated,
                "ok": False,
                "command": "start",
                "recoveryAction": "none",
                "message": f"{component}_recovery_blocked_child_probe_inconclusive",
            }
        if local_children:
            return {
                **revalidated,
                "ok": False,
                "command": "start",
                "recoveryAction": "none",
                "activeWorkerChildren": local_children,
                "message": f"{component}_recovery_blocked_active_child",
            }
        gate = deployment_zero_gate(paths)
        if not gate.get("ok"):
            return {
                **revalidated,
                "ok": False,
                "command": "start",
                "recoveryAction": "none",
                "activeRunGate": gate,
                "message": f"{component}_recovery_blocked_active_runtime_work",
            }
        armed = _run_wrapper(component, "prepare-auto-restart-startup-skip", [], timeout=10)
        if not armed.get("ok"):
            return {
                **revalidated,
                "ok": False,
                "command": "start",
                "recoveryAction": "none",
                "message": f"{component}_recovery_blocked_startup_skip_not_armed",
            }
        recovered = _run_wrapper(component, "resume", [], timeout=20)
        refreshed = _run_wrapper(component, "status", [], timeout=20)
        return {
            **refreshed,
            "ok": bool(recovered.get("ok")) and refreshed.get("service_state") in {"running", "starting"},
            "command": "start",
            "recoveryAction": "safe_resume",
            "startupTickSkipArmed": True,
            "message": f"{component}_safe_recovery_requested",
        }
    finally:
        _release_autoheal_lock(lock_dir)


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
            "service_state": "running",
            "service_health": "degraded",
            "processRunning": True,
            "processCount": len(matching),
            "pid": matching[0].get("pid") if matching else payload.get("pid"),
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
    validation = resolve_runtime_root(RuntimePaths(target_path, paths.releases_dir, paths.runtime_home, paths.legacy_root))
    if not validation.ok:
        return _runtime_root_payload(validation, command="switch-release")
    integrity = verify_deployment_candidate(Path(validation.resolved_root))
    if not integrity.get("ok"):
        return {
            **integrity,
            "command": "switch-release",
            "message": "Release switch refused: exact-candidate Follow60 deployment gate failed.",
        }
    evidence = _validate_release_evidence(Path(validation.resolved_root), integrity)
    if not evidence.get("ok"):
        return {
            **evidence,
            "command": "switch-release",
            "status": "promotion_evidence_blocked",
            "message": "Release switch refused: immutable promotion evidence is incomplete.",
        }
    promotion_started_at = datetime.now(timezone.utc).isoformat()
    gate = deployment_zero_gate(paths)
    if not gate.get("ok"):
        return {
            **gate,
            "command": "switch-release",
            "message": "Release switch refused: production deployment gate is not zero.",
        }
    counts = gate.get("counts") or {}
    expected_zero_keys = {
        "account_run_requests", "ig_runs", "auto_restart_device_locks", "auto_restart_tick_locks"
    }
    if set(counts) != expected_zero_keys or any(counts.get(key) != 0 for key in expected_zero_keys):
        return {
            "ok": False,
            "status": "deployment_gate_blocked",
            "reason": "zero_gate_counts_not_exact",
            "counts": counts,
            "command": "switch-release",
        }
    previous = _safe_resolve(paths.current_link) if (paths.current_link.exists() or paths.current_link.is_symlink()) else None
    previous_sha = _git_full_commit(previous) if previous else None
    candidate_sha = str(evidence["candidate_sha"])
    completed_at = datetime.now(timezone.utc).isoformat()
    receipt_dir = paths.runtime_home / "deployment-receipts"
    receipt_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + f"-{candidate_sha[:12]}.json"
    receipt_path = receipt_dir / receipt_name
    migration = dict(evidence["migration"])
    lineage = dict(evidence["lineage"])
    receipt = {
        "schema": "PHONE_FARM_IMMUTABLE_PROMOTION_RECEIPT_V1",
        "candidate_sha": candidate_sha,
        "release_sha": candidate_sha,
        "manifest_certified_sha": integrity.get("manifest_certified_sha"),
        "manifest_sha256": integrity.get("manifest_sha256"),
        "migration_versions_expected": [
            item.get("version") for item in lineage.get("validated_migrations") or []
            if isinstance(item, dict)
        ],
        "migration_versions_applied": migration.get("applied_versions"),
        "migration_attestation": "PASS",
        "migration_attestation_sha256": _canonical_json_hash(migration),
        "candidate_worktree_clean": True,
        "protected_diff_status": evidence["build"].get("protected_diff_status"),
        "follow60_lock_result": "PASS",
        "signature_result": "PASS",
        "zero_gate_account_run_requests": counts["account_run_requests"],
        "zero_gate_ig_runs": counts["ig_runs"],
        "zero_gate_device_locks": counts["auto_restart_device_locks"],
        "zero_gate_tick_locks": counts["auto_restart_tick_locks"],
        "promotion_started_at": promotion_started_at,
        "promotion_completed_at": completed_at,
        "runtime_root": validation.resolved_root,
        "previous_release_sha": previous_sha,
        "previous_release_path": str(previous) if previous else None,
        "receipt_state": "PROMOTED",
    }
    try:
        staged_receipt = _stage_receipt(receipt_path, receipt)
    except Exception as exc:
        return {
            "ok": False,
            "status": "promotion_receipt_stage_failed",
            "reason": type(exc).__name__,
            "command": "switch-release",
        }
    tmp = paths.current_link.with_name(f"{paths.current_link.name}.tmp")
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(validation.resolved_root)
    os.replace(tmp, paths.current_link)
    try:
        _publish_immutable_receipt(staged_receipt, receipt_path)
    except Exception as exc:
        rollback_tmp = paths.current_link.with_name(f"{paths.current_link.name}.rollback.tmp")
        if rollback_tmp.exists() or rollback_tmp.is_symlink():
            rollback_tmp.unlink()
        if previous is not None:
            rollback_tmp.symlink_to(previous)
            os.replace(rollback_tmp, paths.current_link)
        for leftover in (receipt_path, staged_receipt):
            try:
                if leftover.exists():
                    os.chmod(leftover, 0o600)
                    leftover.unlink()
            except Exception:
                pass
        return {
            "ok": False,
            "status": "promotion_receipt_publish_failed_rolled_back",
            "reason": type(exc).__name__,
            "command": "switch-release",
        }
    return _with_root(validation, {
        "ok": True,
        "status": "switched",
        "previousRoot": str(previous) if previous else None,
        "promotionReceipt": str(receipt_path),
        "zeroGate": counts,
    })


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
    parser.add_argument("target", nargs="?", default="status", choices=["status", "validate", "deployment-gate", "dispatcher", "heartbeat", "notifier", "scheduler", "switch-release"])
    parser.add_argument("command", nargs="?", default="status")
    parser.add_argument("extra", nargs="*")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.target in {"status", "validate"}:
        return _print(runtime_status(), args.json)
    if args.target == "deployment-gate":
        return _print(deployment_zero_gate(), args.json)
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
