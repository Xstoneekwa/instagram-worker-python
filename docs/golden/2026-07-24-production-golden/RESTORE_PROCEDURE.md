# Restore procedure

Use this procedure to restore the Golden Worker without changing business data.
It is a runbook, not authorization: any production switch/restart still requires
Liam approval.

## 1. Verify the Git object

```bash
git fetch --tags origin
git show --no-patch golden-phonefarm-production-2026-07-24
git rev-parse golden-phonefarm-production-2026-07-24^{}
git show --no-patch e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f
```

The tag target is the docs-only commit. Confirm that its parent is exactly
`e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f`.

## 2. Recreate the immutable release

Preferred: use a detached Git worktree at the business commit.

```bash
git worktree add --detach \
  /Users/admin/phonefarm-worker-releases/abf0ebf-f93c501-navigation-consolidated-v1 \
  e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f
git -C /Users/admin/phonefarm-worker-releases/abf0ebf-f93c501-navigation-consolidated-v1 status --short
```

Do not reuse a dirty directory. Verify required files:

```bash
test -f /Users/admin/phonefarm-worker-releases/abf0ebf-f93c501-navigation-consolidated-v1/phonefarm_runtime_control.py
test -x /Users/admin/phonefarm-worker-releases/abf0ebf-f93c501-navigation-consolidated-v1/scripts/run_control_dispatcher_service.sh
test -f /Users/admin/phonefarm-worker-releases/abf0ebf-f93c501-navigation-consolidated-v1/ops/launchd/com.boost.phonefarm.dispatcher.plist
```

## 3. Restore external runtime state

Provision these from the owner-controlled secure backup, never from Git:

- `/Users/admin/phonefarm-runtime/env/run-control-dispatcher.env`;
- `/Users/admin/phonefarm-runtime/env/device-heartbeat.env`;
- `/Users/admin/phonefarm-runtime/env/incident-notifier.env`;
- Vercel, Supabase and notification credentials;
- Android SDK/ADB installation and trusted USB authorization.

Do not print secret values while validating their presence/permissions.

## 4. Pre-switch read-only gate

Confirm:

- active requests/runs/locks are zero;
- no account session or Auto Login subprocess is active;
- the dispatcher process count is one or zero;
- the candidate release is clean and exact;
- the existing active symlink target is recorded for rollback.

## 5. Atomic activation — requires explicit GO

```bash
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl \
  switch-release abf0ebf-f93c501-navigation-consolidated-v1 --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher restart --json
```

Do not start Python directly and do not restart twice. Only reload heartbeat or
notifier if their runtime root is actually wrong and the GO includes them.

## 6. Certification

For at least 12 minutes verify:

- one stable dispatcher PID and correct CWD;
- `runtimeRootOk=true`, `preflightOk=true`, queue zero;
- one stable heartbeat PID and expected publish cadence;
- no timeout, periodic SIGTERM, duplicate process or root mismatch;
- request/run totals unchanged and active locks zero;
- zero device action.

## 7. Cross-repository restore

Worker restoration does not restore Backend, Supabase or BotApp. Use their own
artifact registries/backups. The live references captured here are:

- Vercel deployment `dpl_Ab6AKB5rXxvuGyuUiXe7f2tZc5K4`;
- installed BotApp `app.asar` SHA-256
  `8b956c792bfdc08a0663bc952a883fe77d4010a2dcb7de5c95ce5cd523c38253`.

These identify artifacts but do not replace source-level recovery procedures.
