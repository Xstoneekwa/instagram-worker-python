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

## Supervision

Recommended first deployment:

- macOS worker host: `launchctl` KeepAlive job for `account_run_request_consumer.py`
- Linux worker host: `systemd` unit with `Restart=always`
- Do not enable Play until staging smoke passes

Example launchd label:

- `com.instagram.run-control-dispatcher`

Restart policy:

- Always restart dispatcher process
- Do not blindly replay child runs if `run_id` is already linked

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
