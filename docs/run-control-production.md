# Run Control Production Guide

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

Production default: supervised dispatcher service, not manual heartbeat commands.

Artifacts:

- Wrapper: `scripts/run_control_dispatcher_service.sh`
- Env template: `docs/run-control-dispatcher.env.example`
- launchd template: `ops/launchd/com.boost.phonefarm.dispatcher.plist`

### One-time setup

```bash
cd /Users/admin/instagram-worker-python
cp docs/run-control-dispatcher.env.example .env.run-control-dispatcher
# edit .env.run-control-dispatcher with SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
chmod +x scripts/run_control_dispatcher_service.sh
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
./scripts/run_control_dispatcher_service.sh status
./scripts/run_control_dispatcher_service.sh preflight
./scripts/run_control_dispatcher_service.sh once
./scripts/run_control_dispatcher_service.sh start
```

### launchd install (macOS)

```bash
cp ops/launchd/com.instagram.run-control-dispatcher.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.instagram.run-control-dispatcher.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.instagram.run-control-dispatcher.plist
launchctl start com.instagram.run-control-dispatcher
launchctl print gui/$(id -u)/com.instagram.run-control-dispatcher | head
```

Stop / restart:

```bash
launchctl stop com.instagram.run-control-dispatcher
launchctl unload ~/Library/LaunchAgents/com.instagram.run-control-dispatcher.plist
```

Logs:

- `logs/run-control-dispatcher/dispatcher.log`
- `logs/run-control-dispatcher/launchd.stdout.log`
- `logs/run-control-dispatcher/launchd.stderr.log`

### Dispatcher lifecycle ownership

The production chain is:

```text
launchd com.boost.phonefarm.dispatcher
-> scripts/run_control_dispatcher_service.sh start
-> account_run_request_consumer.py
```

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

Known UI limitation: BotApp Runtime Health can still display an unhealthy state
if it interprets an `idle` dispatcher as not running. Treat the backend
heartbeat and wrapper status as the source of truth until that UI contract is
changed separately.

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

- `com.instagram.run-control-dispatcher`

Restart policy:

- Always restart dispatcher process
- Do not blindly replay child runs if `run_id` is already linked
- Safe-start blocks launch mode when active queue exists unless explicitly overridden

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
