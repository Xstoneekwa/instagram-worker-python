# Rollback

Rollback means changing the active immutable Worker pointer; it does not rewrite
Git history, reverse database migrations or reinstall BotApp.

## Preconditions

- explicit Liam GO naming the target release;
- current and target symlink paths recorded;
- target commit and clean release verified;
- active requests, runs and locks are zero;
- no Auto Login/account session/device action is running;
- only one dispatcher chain exists;
- backend/BotApp compatibility reviewed.

## Candidate targets

| Purpose | Release |
|---|---|
| Current Golden | `abf0ebf-f93c501-navigation-consolidated-v1` (`e7f54a9`) |
| Auto Login baseline without f93 navigation | `abf0ebf-auto-login-07ee-post-login-stabilization-v1` (`abf0ebf`) |

The second target removes the active f93 navigation behavior and is therefore a
functional rollback requiring an explicit incident decision.

## Controlled sequence

```bash
readlink /Users/admin/phonefarm-worker-current
git -C /Users/admin/phonefarm-worker-releases/<target> rev-parse HEAD
git -C /Users/admin/phonefarm-worker-releases/<target> status --short
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl \
  switch-release <target> --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher restart --json
```

Exactly one dispatcher restart. Do not restart heartbeat/notifier unless their
root is wrong and the incident GO includes them. Never use `kill -9`, start
Python directly or run from `/Users/admin/instagram-worker-python`.

## Post-rollback gate

- active pointer and process CWD equal target release;
- `runtimeRootOk=true`, `preflightOk=true`;
- one stable dispatcher process and zero queue;
- request/run totals unchanged, active locks zero;
- heartbeat/notifier healthy;
- twelve-minute stability window clean;
- no phone/ADB action unless separately authorized.

## Database and external artifacts

Do not reverse additive Supabase migrations during Worker rollback. Do not
rollback Vercel or BotApp merely because Worker was rolled back. Each artifact
has a separate compatibility and evidence boundary.
