import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_SOURCE = REPO_ROOT / "scripts" / "run_control_dispatcher_service.sh"


def test_wrapper_git_identity_uses_exact_process_scoped_safe_directory() -> None:
    source = WRAPPER_SOURCE.read_text(encoding="utf-8")

    assert 'git -c "safe.directory=$ROOT_DIR" -C "$ROOT_DIR" rev-parse --show-toplevel' in source
    assert 'git -c "safe.directory=$ROOT_DIR" -C "$ROOT_DIR" rev-parse HEAD' in source
    assert 'export GIT_CONFIG_COUNT="1"' in source
    assert 'export GIT_CONFIG_KEY_0="safe.directory"' in source
    assert 'export GIT_CONFIG_VALUE_0="$ROOT_DIR"' in source
    assert "safe.directory=*" not in source
    assert "git config --global" not in source


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _wait_for(predicate, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition was not met before timeout")


def _read_pid(path: Path) -> int:
    return int(path.read_text().strip())


def _event_count(path: Path, marker: str) -> int:
    if not path.exists():
        return 0
    return path.read_text().count(marker)


def _make_harness(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "worker"
    scripts = root / "scripts"
    run_dir = root / "run"
    logs = root / "logs"
    scripts.mkdir(parents=True)
    run_dir.mkdir()
    logs.mkdir()
    shutil.copy2(WRAPPER_SOURCE, scripts / "run_control_dispatcher_service.sh")
    (scripts / "run_control_dispatcher_service.sh").chmod(0o755)

    dummy_consumer = root / "account_run_request_consumer.py"
    dummy_consumer.write_text(
        """
import json
import os
import signal
import sys
import time
from pathlib import Path

events = Path(os.environ["DUMMY_CONSUMER_EVENTS"])

def emit(event):
    with events.open("a") as handle:
        handle.write(f"{event}:{os.getpid()}\\n")
        handle.flush()

if len(sys.argv) > 1 and sys.argv[1] == "preflight":
    if "linger" in sys.argv:
        time.sleep(float(os.environ.get("DUMMY_PREFLIGHT_LINGER_SECONDS", "5")))
    print(json.dumps({"ok": True, "reason": "dummy", "active_count": 0, "mode": "test"}))
    raise SystemExit(0)

def handle_signal(signum, frame):
    emit(f"signal_{signum}")
    raise SystemExit(0)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)
emit("started")
exit_after = float(os.environ.get("DUMMY_CONSUMER_EXIT_AFTER", "0") or "0")
if exit_after > 0:
    time.sleep(exit_after)
    emit("normal_exit")
    raise SystemExit(0)
while True:
    time.sleep(0.1)
""".lstrip()
    )
    (root / "phonefarm_runtime_control.py").write_text(
        """
import json
import os
import sys

if len(sys.argv) > 1 and sys.argv[1] == "deployment-gate":
    active = int(os.environ.get("DUMMY_DEPLOYMENT_GATE_ACTIVE", "0"))
    print(json.dumps({"ok": active == 0, "status": "ready" if active == 0 else "deployment_gate_blocked"}))
    raise SystemExit(0 if active == 0 else 2)
raise SystemExit(2)
""".lstrip(),
        encoding="utf-8",
    )
    adb = root / "adb"
    adb.write_text("#!/usr/bin/env sh\nexit 0\n")
    adb.chmod(0o755)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Phone Farm Tests",
            "-c",
            "user.email=phonefarm-tests@example.invalid",
            "commit",
            "-qm",
            "dispatcher harness",
        ],
        cwd=root,
        check=True,
    )
    return {"root": root, "wrapper": scripts / "run_control_dispatcher_service.sh", "run_dir": run_dir, "logs": logs, "adb": adb}


def _env(harness: dict[str, Path], events: Path, **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "RUN_CONTROL_DISPATCHER_ENV_FILE": str(harness["root"] / "missing.env"),
            "RUN_CONTROL_DISPATCHER_LOG_DIR": str(harness["logs"]),
            "RUN_CONTROL_DISPATCHER_RUN_DIR": str(harness["run_dir"]),
            "RUN_CONTROL_DISPATCHER_PYTHON": sys.executable,
            "RUN_CONTROL_DISPATCHER_STOP_TIMEOUT_SECONDS": "3",
            "SUPABASE_URL": "http://127.0.0.1",
            "SUPABASE_SERVICE_ROLE_KEY": "dummy",
            "ADB_PATH": str(harness["adb"]),
            "DUMMY_CONSUMER_EVENTS": str(events),
        }
    )
    env.update(extra)
    return env


def _start_wrapper(harness: dict[str, Path], events: Path, **extra_env: str) -> subprocess.Popen:
    return subprocess.Popen(
        [str(harness["wrapper"]), "start"],
        cwd=harness["root"],
        env=_env(harness, events, **extra_env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _wait_for_consumer_pid(harness: dict[str, Path], wrapper_pid: int) -> int:
    pid_file = harness["run_dir"] / "dispatcher.pid"
    _wait_for(lambda: pid_file.exists())

    def read_live_child_pid():
        if not pid_file.exists():
            return None
        pid = _read_pid(pid_file)
        if pid != wrapper_pid and _pid_alive(pid):
            return pid
        return None

    return _wait_for(read_live_child_pid)


def _terminate_process(process: subprocess.Popen):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=5)


def test_prepare_startup_tick_skip_creates_private_one_shot_token(tmp_path):
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    token_path = harness["run_dir"] / "auto-restart-skip-startup-tick.once"

    result = subprocess.run(
        [str(harness["wrapper"]), "prepare-auto-restart-startup-skip"],
        cwd=harness["root"],
        env=_env(harness, events),
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "auto_restart_startup_tick_skip_once_prepared"
    assert token_path.read_text().strip() == "phonefarm-auto-restart-startup-skip-v1"
    assert token_path.stat().st_mode & 0o777 == 0o600


def test_restart_refuses_to_kill_dispatcher_when_deployment_gate_is_active(tmp_path):
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    result = subprocess.run(
        [str(harness["wrapper"]), "restart"],
        cwd=harness["root"],
        env=_env(harness, events, DUMMY_DEPLOYMENT_GATE_ACTIVE="1"),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "dispatcher_restart_blocked_deployment_gate" in result.stderr


def test_start_tracks_single_child_and_wrapper_waits(tmp_path):
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    process = _start_wrapper(harness, events)
    try:
        child_pid = _wait_for_consumer_pid(harness, process.pid)
        assert child_pid != process.pid
        assert _pid_alive(child_pid)
        assert process.poll() is None
        assert (harness["run_dir"] / "dispatcher.lock" / "owner.pid").read_text().strip() == str(process.pid)
        _wait_for(lambda: _event_count(events, "started:") == 1)
    finally:
        _terminate_process(process)


def test_sigterm_wrapper_stops_child_and_cleans_state(tmp_path):
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    process = _start_wrapper(harness, events)
    child_pid = _wait_for_consumer_pid(harness, process.pid)

    process.terminate()
    process.wait(timeout=5)

    assert not _pid_alive(child_pid)
    assert not (harness["run_dir"] / "dispatcher.pid").exists()
    assert not (harness["run_dir"] / "dispatcher.lock").exists()
    assert "signal_15" in events.read_text()


def test_sigterm_process_group_stops_wrapper_and_child(tmp_path):
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    process = _start_wrapper(harness, events)
    child_pid = _wait_for_consumer_pid(harness, process.pid)

    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    process.wait(timeout=5)

    assert not _pid_alive(child_pid)
    assert not (harness["run_dir"] / "dispatcher.pid").exists()
    assert not (harness["run_dir"] / "dispatcher.lock").exists()


def test_stale_pid_file_is_removed_and_start_succeeds(tmp_path):
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    pid_file = harness["run_dir"] / "dispatcher.pid"
    pid_file.write_text("999999\n")

    process = _start_wrapper(harness, events)
    try:
        child_pid = _wait_for_consumer_pid(harness, process.pid)
        assert child_pid != 999999
        assert _pid_alive(child_pid)
    finally:
        _terminate_process(process)


def test_live_non_matching_pid_file_stops_without_killing_process(tmp_path):
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (harness["run_dir"] / "dispatcher.pid").write_text(f"{unrelated.pid}\n")
        process = _start_wrapper(harness, events)
        stdout, stderr = process.communicate(timeout=5)

        assert process.returncode == 3
        assert "dispatcher_pid_file_conflict" in stderr
        assert _pid_alive(unrelated.pid)
        assert not events.exists()
        assert "dispatcher_already_running" not in stdout
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_double_start_is_idempotent(tmp_path):
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    first = _start_wrapper(harness, events)
    try:
        child_pid = _wait_for_consumer_pid(harness, first.pid)
        second = _start_wrapper(harness, events)
        stdout, _stderr = second.communicate(timeout=5)

        assert second.returncode == 0
        assert f"dispatcher_already_running pid={child_pid}" in stdout
        assert _event_count(events, "started:") == 1
        assert _pid_alive(child_pid)
    finally:
        _terminate_process(first)


def test_status_ignores_short_lived_diagnostic_subcommands(tmp_path):
    """Un sous-processus `preflight`/`once` ne doit jamais être compté comme
    un dispatcher réel (sinon status peut rapporter duplicate_dispatcher_processes
    à cause de son propre enfant)."""
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    process = _start_wrapper(harness, events)
    try:
        child_pid = _wait_for_consumer_pid(harness, process.pid)
        # Simule un preflight de diagnostic vivant pendant le status.
        lingering_preflight = subprocess.Popen(
            [sys.executable, "account_run_request_consumer.py", "preflight", "linger"],
            cwd=harness["root"],
            env=_env(harness, events, DUMMY_PREFLIGHT_LINGER_SECONDS="10"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            status = subprocess.run(
                [str(harness["wrapper"]), "status", "--json"],
                cwd=harness["root"],
                env=_env(harness, events),
                capture_output=True,
                text=True,
                timeout=30,
            )
            payload = json.loads(status.stdout.strip().splitlines()[-1])
            assert payload["status"] == "running"
            assert payload["processCount"] == 1
            assert payload["duplicateProcess"] is False
            assert payload["pid"] == child_pid
        finally:
            lingering_preflight.terminate()
            lingering_preflight.wait(timeout=5)
    finally:
        _terminate_process(process)


def test_normal_child_exit_cleans_pid_and_lock(tmp_path):
    harness = _make_harness(tmp_path)
    events = tmp_path / "events.log"
    process = _start_wrapper(harness, events, DUMMY_CONSUMER_EXIT_AFTER="0.2")

    process.wait(timeout=5)

    assert process.returncode == 0
    assert "normal_exit" in events.read_text()
    assert not (harness["run_dir"] / "dispatcher.pid").exists()
    assert not (harness["run_dir"] / "dispatcher.lock").exists()
