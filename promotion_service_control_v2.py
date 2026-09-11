"""Candidate-owned launchd lifecycle, independent of the active release verbs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

SERVICES = {
    "dispatcher": ("com.boost.phonefarm.dispatcher", "run-control-dispatcher"),
    "heartbeat": ("com.boost.phonefarm.device-heartbeat", "device-heartbeat"),
    "notifier": ("com.boost.phonefarm.incident-notifier", "incident-notifier"),
}


def control(component, command, *, home, runtime, uid, runner=subprocess.run):
    if component not in SERVICES or command not in ("pause", "resume") or uid <= 0:
        raise ValueError("service_control_identity_or_command_invalid")
    label, run_name = SERVICES[component]
    domain = f"gui/{uid}"
    target = f"{domain}/{label}"
    plist = Path(home) / "Library/LaunchAgents" / (label + ".plist")
    info = plistlib.loads(plist.read_bytes())
    if info.get("Label") != label or info.get("KeepAlive") is not True:
        raise ValueError("service_control_plist_contract_invalid")
    pause = Path(runtime) / "run" / run_name / "paused"
    if not pause.parent.is_dir() or pause.is_symlink():
        raise ValueError("service_control_pause_path_invalid")

    def call(*args, required=True):
        result = runner(["/bin/launchctl", *args], capture_output=True, text=True,
                        check=False, timeout=20)
        if required and result.returncode:
            raise ValueError("launchctl_" + args[0] + "_failed")
        return result

    call("print", domain)
    if command == "pause":
        with pause.open("ab") as f:
            f.flush()
            os.fsync(f.fileno())
        call("disable", target)
        if call("print", target, required=False).returncode == 0:
            call("bootout", target)
        if call("print", target, required=False).returncode == 0:
            raise ValueError("service_still_loaded_after_bootout")
        return {"ok": True, "status": "SERVICE_QUIESCED", "label": label,
                "domain": domain, "keepalive_blocked": True}
    # Never kick an existing generation during transaction recovery.
    if call("print", target, required=False).returncode == 0:
        if pause.exists():
            raise ValueError("loaded_service_with_pause_marker")
        return {"ok": True, "status": "SERVICE_ALREADY_LOADED", "label": label}
    pause.unlink(missing_ok=True)
    call("enable", target)
    call("bootstrap", domain, str(plist))
    call("print", target)
    return {"ok": True, "status": "SERVICE_STARTED", "label": label, "domain": domain}


def main():
    # Identity selection is the same verified runtime-owner contract used by
    # the parent, and refuses root as the service owner.
    import phonefarm_runtime_control as c
    try:
        paths = c.runtime_paths()
        identity = c._promotion_service_context(paths)
        if os.geteuid() != identity["uid"]:
            raise ValueError("service_control_requires_runtime_user")
        value = control(sys.argv[1], sys.argv[2], home=identity["home"],
                        runtime=paths.runtime_home, uid=identity["uid"])
    except Exception as exc:
        value = {"ok": False, "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}
    print(json.dumps(value, sort_keys=True))
    return 0 if value["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
