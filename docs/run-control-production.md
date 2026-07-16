# Run Control Production Guide

> **Production baseline - 2026-07-16.** Scheduler authority is the durable
> dispatcher heartbeat; Electron BotApp is observability only. First-tick launch,
> same-account preflight lease handoff and bounded device isolation are frozen in
> [JULY_16_PRODUCTION_BASELINE](./checkpoints/2026-07-16-production-baseline-runtime.md).

Run Control delivers production-ready manual run orchestration without half-wired Play behavior.

## Invariant

Play stays disabled unless all of the following are true:

- `INSTAGRAM_RUN_CONTROL_PLAY_ENABLED=true` on the dashboard frontend host
- `INSTAGRAM_RUN_CONTROL_DISPATCHER_WORKER_ID` matches a live dispatcher heartbeat
- Dispatcher heartbeat is fresh (`INSTAGRAM_RUN_CONTROL_DISPATCHER_HEALTH_MAX_AGE_SECONDS`, default 60)
- Account eligibility checks pass at request time

Success copy is only `Run starting.` when the request is accepted on a healthy dispatcher path.

## Components

| Layer | Artifact | Role |
|-------|----------|------|
| SQL | `account_run_requests` | Operator intent ledger |
| SQL RPCs | claim/link/cancel/complete/create | Guarded transitions |
| Python | `account_run_request_consumer.py` | Supervised dispatcher daemon |
| Python | `runner.py --run-request-id` | Links real `ig_runs` and honors cancel |
| Frontend | `POST /api/instagram-dashboard/runs/start` | Admin-only run request creation |
| Frontend | `GET /api/instagram-dashboard/runs/health` | Dispatcher readiness gate |
| Frontend | `POST /api/instagram-dashboard/stop` | Cancel queued + stop running |

## Dispatcher Environment

```bash
export RUN_CONTROL_DISPATCHER_ENABLED=true
export RUN_CONTROL_DISPATCHER_HEALTH_ONLY=false
export RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED=true
export RUN_CONTROL_DISPATCHER_WORKER_ID="run-dispatcher:mac-host-01"
export RUN_CONTROL_DISPATCHER_ALLOWED_RUN_TYPES="account_session,outreach_session"
export RUN_CONTROL_DISPATCHER_TEST_ACCOUNT_IDS=""   # optional smoke allowlist
export RUNTIME_HEARTBEATS_ENABLED=true
export SUPABASE_URL=...
export SUPABASE_SERVICE_ROLE_KEY=...
python3 account_run_request_consumer.py
```

Health-only mode (RunControl-2):

```bash
export RUN_CONTROL_DISPATCHER_ENABLED=true
export RUN_CONTROL_DISPATCHER_HEALTH_ONLY=true
export RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED=false
python3 account_run_request_consumer.py
```

## Dashboard Environment

```bash
export INSTAGRAM_RUN_CONTROL_PLAY_ENABLED=true
export INSTAGRAM_RUN_CONTROL_DISPATCHER_WORKER_ID="run-dispatcher:mac-host-01"
export INSTAGRAM_RUN_CONTROL_DISPATCHER_HEALTH_MAX_AGE_SECONDS=60
```

Play remains disabled if any of these are missing or the dispatcher heartbeat is stale.

## Permanent Dispatcher Service

Production default: supervised dispatcher service through the canonical runtime
controller, not manual heartbeat commands and not a mutable checkout.

Artifacts:

- Active root symlink: `/Users/admin/phonefarm-worker-current`
- Immutable releases: `/Users/admin/phonefarm-worker-releases/<commit>`
- Stable controller: `/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl`
- Wrapper inside the active release: `scripts/run_control_dispatcher_service.sh`
- Env template: `docs/run-control-dispatcher.env.example`
- launchd template: `ops/launchd/com.boost.phonefarm.dispatcher.plist`

Runtime invariants:

- LaunchAgents call only `/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl`.
- The controller resolves `/Users/admin/phonefarm-worker-current` on every call.
- `/Users/admin/instagram-worker-python` is never a service root.
- If the symlink is missing, points outside releases, points to the legacy root,
  or lacks required worker files, status is `runtime_root_invalid`.
- If a process is alive but from a non-active root, status is
  `runtime_root_mismatch`.
- Logs and PID/lock files live under `/Users/admin/phonefarm-runtime`, outside
  immutable releases.

### One-time setup

```bash
mkdir -p /Users/admin/phonefarm-runtime/{bin,env,logs,run}
# Provision secrets out of band; never copy them from a mutable checkout.
# Required files:
#   /Users/admin/phonefarm-runtime/env/run-control-dispatcher.env
#   /Users/admin/phonefarm-runtime/env/device-heartbeat.env
ln -sfn /Users/admin/phonefarm-worker-releases/<commit> /Users/admin/phonefarm-worker-current
cp /Users/admin/phonefarm-worker-current/scripts/phonefarm-runtimectl /Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl
chmod +x /Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl
```

Set the same worker id on the dashboard host:

```bash
export INSTAGRAM_RUN_CONTROL_DISPATCHER_WORKER_ID="run-dispatcher:your-worker-host"
```

### Safe startup preflight

Before launch mode starts, the dispatcher checks active `account_run_requests`
(`queued`, `claimed`, `starting`, `running`).

- If active queue exists and `RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE` is not `true`,
  launch mode refuses to start.
- Health-only mode still starts and publishes heartbeat.
- Preflight is read-only: no claim, no mutation, no runner launch.

Commands:

```bash
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl status --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher status --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher start
```

### `serve` vs `start` vs `status` (contract)

- `dispatcher serve` / `heartbeat serve` — **launchd-only** entry point for the
  long-lived services. The controller resolves the canonical root, then
  `exec`s the release wrapper in foreground mode. There is **no Python parent
  with a timeout** left in the chain; launchd is the only supervisor. Never
  call `serve` from BotApp or an interactive shell.
- `dispatcher start` / `heartbeat start` — short, idempotent, **bounded**
  control command for BotApp/operators. If the service is running it returns
  `running`; otherwise it issues `launchctl kickstart` on the canonical label
  and returns a structured state. It never spawns the worker itself and never
  becomes the consumer's parent.
- `dispatcher status` / `heartbeat status` — strictly read-only. It must never
  start, stop, or kill anything, and short-lived diagnostic subprocesses
  (`preflight`, `once`) are never counted as real dispatchers/publishers.

**Never wrap a long-lived service in a bounded timeout.** The historical
defect (fixed here) was `subprocess.run(..., timeout=60)` around
`dispatcher start` in the controller: the controller killed its own service
every 60 s, launchd relaunched it after `ThrottleInterval` (30 s), producing a
permanent ~60 s running → SIGTERM → ~30 s stopped loop, visible in
`dispatcher.log` as `run_control_dispatcher_stop_signal signal=15` every
~90 s and in `launchd.stdout.log` as `dispatcher_command_timeout`. The same
defect existed for the heartbeat publisher (`heartbeat_command_timeout`).
Diagnostic signature of a regression: launchd `runs` counter climbing, PID
changing every minute, consumer receiving periodic SIGTERM.

### launchd install (macOS)

```bash
cp /Users/admin/phonefarm-worker-current/ops/launchd/com.boost.phonefarm.dispatcher.plist ~/Library/LaunchAgents/
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.boost.phonefarm.dispatcher.plist 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.boost.phonefarm.dispatcher.plist
launchctl enable "gui/$(id -u)/com.boost.phonefarm.dispatcher"
launchctl kickstart -k "gui/$(id -u)/com.boost.phonefarm.dispatcher"
launchctl print "gui/$(id -u)/com.boost.phonefarm.dispatcher"
```

Stop / restart:

```bash
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher stop
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher restart
```

Logs:

- `/Users/admin/phonefarm-runtime/logs/run-control-dispatcher/dispatcher.log`
- `/Users/admin/phonefarm-runtime/logs/run-control-dispatcher/launchd.stdout.log`
- `/Users/admin/phonefarm-runtime/logs/run-control-dispatcher/launchd.stderr.log`

### Dispatcher lifecycle ownership

The production chain is:

```text
launchd com.boost.phonefarm.dispatcher
-> exec /Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher serve
-> (exec) /Users/admin/phonefarm-worker-current/scripts/run_control_dispatcher_service.sh start
-> account_run_request_consumer.py
```

Every arrow is an `exec` (no intermediate parent survives): the launchd job
PID is the wrapper itself, which traps SIGTERM and forwards it to the
verified consumer. The same shape applies to
`com.boost.phonefarm.device-heartbeat` with `heartbeat serve` and
`device_heartbeat_service.sh`.

The legacy root `/Users/admin/instagram-worker-python` must never appear in
this chain; the controller refuses to resolve it.

The wrapper remains the real supervisor. It owns:

- `RUN_CONTROL_DISPATCHER_RUN_DIR/dispatcher.lock`
- `RUN_CONTROL_DISPATCHER_RUN_DIR/dispatcher.lock/owner.pid`
- `RUN_CONTROL_DISPATCHER_RUN_DIR/dispatcher.pid`

`dispatcher.pid` points to the consumer after startup. The lock owner points to
the wrapper. On normal child exit or wrapper `SIGTERM`/`SIGINT`, the wrapper
forwards the signal to the verified consumer, waits for it to stop, and removes
only the PID/lock files it owns. It does not kill unrelated PIDs.

Anti-double-consumer rules:

- If `dispatcher.pid` points to a live `account_run_request_consumer.py`, `start`
  is idempotent and prints `dispatcher_already_running`.
- If `dispatcher.pid` points to an absent process, it is stale and may be
  removed before startup.
- If `dispatcher.pid` points to a live process that is not the expected consumer,
  startup stops with `dispatcher_pid_file_conflict`; do not adopt or kill it.
- A held lock without a live owner may be removed; a held lock with a live
  owner blocks startup.

Safe restart preconditions:

- Auto Restart is off.
- Queue is empty.
- No active run exists.
- No device lock exists.
- No dispatcher lease exists.
- There is only one wrapper -> consumer chain.

Safe restart sequence:

```bash
# After the preconditions above are verified.
kill -TERM -<dispatcher_pgid>
# Wait until both wrapper and consumer are gone.
launchctl kickstart gui/$(id -u)/com.boost.phonefarm.dispatcher
```

Do not use `kill -9`. Do not start Python directly. Do not create a parallel
wrapper. After restart, verify a fresh dispatcher heartbeat, `health_only=false`,
`launch_enabled=true`, queue/runs/locks still empty, and the new process start
time after the deployed commit.

BotApp must call the same stable controller. It must not hardcode a release
hash, guess a checkout, or silently fall back to `/Users/admin/instagram-worker-python`.

### Intentionally processing an existing queue

Only after reviewing `./scripts/run_control_dispatcher_service.sh status`:

```bash
export RUN_CONTROL_DISPATCHER_ALLOW_EXISTING_QUEUE=true
./scripts/run_control_dispatcher_service.sh start
```

## Supervision

Recommended first deployment:

- macOS worker host: `launchctl` KeepAlive job via `scripts/run_control_dispatcher_service.sh`
- Linux worker host: `systemd` unit with `Restart=always`
- Dashboard reads `/api/instagram-dashboard/runs/health` automatically

Example launchd label:

- `com.boost.phonefarm.dispatcher`

Restart policy:

- Always restart dispatcher process
- Do not blindly replay child runs if `run_id` is already linked
- Safe-start blocks launch mode when active queue exists unless explicitly overridden

## Device Heartbeat Service

Production phone backend heartbeats are a separate launchd service, but they use
the same canonical root contract:

```text
launchd com.boost.phonefarm.device-heartbeat
-> /Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl heartbeat start
-> /Users/admin/phonefarm-worker-current
-> scripts/device_heartbeat_service.sh start
-> device_heartbeat_publisher.py --serve
```

Current configured cadence is `DEVICE_HEARTBEAT_INTERVAL_SECONDS=60` unless the
external env file overrides it. Keep this value unless operational evidence
shows the assignment backend needs a shorter freshness window.

The publisher reads local ADB inventory and publishes per registered device. A
missing emulator must be reported as that emulator's stale/offline state, not as
Samsung A16 phone failure. The service status distinguishes:

- service alive/stopped/degraded;
- local ADB visibility;
- backend heartbeat freshness;
- publish/backend errors;
- root mismatch.

Heartbeat logs are external to releases:

- `/Users/admin/phonefarm-runtime/logs/device-heartbeat-service/heartbeat.log`
- `/Users/admin/phonefarm-runtime/logs/device-heartbeat-service/heartbeat.log.1`
- `/Users/admin/phonefarm-runtime/logs/device-heartbeat-service/launchd.stdout.log`
- `/Users/admin/phonefarm-runtime/logs/device-heartbeat-service/launchd.stderr.log`

`device_heartbeat_publisher.py --serve` owns the heartbeat log write and rotates
it at `DEVICE_HEARTBEAT_LOG_MAX_BYTES` (default 10 MiB). Avoid shell append
redirection for the persistent heartbeat loop because it can keep writing to a
renamed inode and hide disk-pressure incidents.

## Embedded Scheduler

Auto Restart scheduling is embedded in `account_run_request_consumer.py`; do not
create a second scheduler service. The dispatcher calls the canonical backend
route `/api/instagram-dashboard/auto-restart/tick` at most once per minute via
`auto_restart_dispatcher_tick.py`.

Scheduler operator status is derived from the dispatcher tick logs:

- `disabled_by_config` when backend returns `scheduler_disabled`;
- `no_eligible_accounts` when the backend evaluated candidates but selected none;
- `running` when the backend enqueued work;
- `error` when the tick request fails.

The backend route owns account selection, schedule windows, commercial gates and
`manual_only` exclusion. Never modify caps, schedules, packages or account
settings to force a scheduler smoke test.

## Release And Rollback

Release flow:

1. Commit and push worker changes.
2. Create `/Users/admin/phonefarm-worker-releases/<commit>` from that commit.
3. Verify the release contains `phonefarm_runtime_control.py`, wrappers and
   launchd templates.
4. Save the previous `/Users/admin/phonefarm-worker-current` target.
5. Atomically repoint `/Users/admin/phonefarm-worker-current`.
6. Copy `scripts/phonefarm-runtimectl` to
   `/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl`.
7. Install LaunchAgents from the active root.
8. Restart only dispatcher/heartbeat services and verify roots.

Rollback is the same symlink operation in reverse: repoint
`/Users/admin/phonefarm-worker-current` to the saved release and restart only the
affected LaunchAgents. Roll back immediately if any service starts from the
legacy checkout, reports `runtime_root_mismatch`, fails to publish heartbeat, or
BotApp reports a contradictory runtime root.

### Mandatory 12-minute stability validation

After any release switch or launchd reload, observe **at least 12 minutes**
without manual action before declaring the deployment healthy:

- dispatcher: same PID for the whole window, stable launchd `runs` counter,
  no periodic `stop_signal 15`, no `dispatcher_command_timeout`, continuous
  `running` status, no `duplicate_dispatcher_processes`;
- heartbeat: same publisher PID, no `heartbeat_command_timeout`, devices
  publishing on their expected ~60 s cadence, no periodic publisher restart;
- scheduler: backend state read without change; `scheduler_disabled` (an
  embedded-tick skip reason) stays fully distinct from dispatcher health and
  never flips the dispatcher banner;
- opening or closing BotApp must not affect either service.

If any check fails, roll the pointer back to the saved release and reload only
the affected services.

## Staging Smoke Checklist

1. Migration applied: `account_run_requests` + RPCs
2. Dispatcher running with fresh heartbeat
3. Play enabled only after health gate passes
4. `POST /runs/start` creates queued request
5. Dispatcher claims and launches `runner.py`
6. `ig_runs.status='running'` created and linked
7. Dashboard shows running state via existing run projections
8. Stop cancels queued request
9. Stop requests cooperative cancel for running request
10. Duplicate Play blocked
11. Stale claim reclaimed after dispatcher crash simulation

## Audit Events

Safe audit action types:

- `manual_run_requested`
- `manual_run_claimed`
- `manual_run_started`
- `manual_run_completed`
- `manual_run_failed`
- `manual_run_canceled`
- `manual_run_blocked`

Never store secrets, tokens, raw XML, screenshots, Vault ids, or raw device serials in audit payloads.

## Checkpoints Delivered

- RunControl-1: SQL schema/RPC
- RunControl-2: dispatcher health skeleton
- RunControl-3: runner link + cancellation checks
- RunControl-4: dispatcher claim + subprocess launch (env gated)
- RunControl-5: frontend start API + guarded Play
- RunControl-6: stop/cancel integration
- RunControl-7: production activation docs + env gates

## Rollout

1. Apply migration to staging
2. Deploy dispatcher health-only and verify heartbeat
3. Enable launch on test account allowlist only
4. Validate runner link + stop/cancel smoke
5. Enable dashboard Play flag for selected admins
6. Expand account allowlist / remove test-only gate after production sign-off
