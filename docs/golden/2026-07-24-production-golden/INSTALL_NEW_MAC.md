# Install on a new Mac, server, phone or clone

This is a controlled build guide. It does not authorize production activation.

## New Mac prerequisites

- supported macOS account with FileVault and automatic security updates;
- Git and Apple Command Line Tools;
- Python version compatible with the Worker lockfile/tests;
- Android SDK platform tools (`adb`);
- access to the three Git remotes;
- owner-provisioned runtime secrets via secure transfer;
- physical access to phones for USB authorization;
- launchd user session for the runtime account.

## Source and release

```bash
git clone https://github.com/Xstoneekwa/instagram-worker-python.git
cd instagram-worker-python
git fetch --tags origin
git show golden-phonefarm-production-2026-07-24
git worktree add --detach \
  /Users/admin/phonefarm-worker-releases/abf0ebf-f93c501-navigation-consolidated-v1 \
  e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f
```

Create runtime directories with least-privilege ownership:

```bash
mkdir -p /Users/admin/phonefarm-runtime/{bin,env,logs,run}
```

Install the stable controller from the verified release and set executable
permissions. Provision env files out of band; do not copy `.env` files from old
mutable checkouts.

## Offline validation before services

- run the Auto Login 07ee suite;
- run Follow, Welcome, Unfollow, runner, navigation and Golden Follow suites;
- run full Worker discovery (`1850` expected at this checkpoint);
- run Python compilation, `git diff --check` and no-leak scans;
- verify release HEAD and clean status.

Do not connect phones merely to satisfy unit tests.

## Install launchd services

Use the active release templates for:

- `com.boost.phonefarm.dispatcher`;
- `com.boost.phonefarm.device-heartbeat`;
- incident notifier where configured.

LaunchAgents must call `/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl`,
never a checkout or hardcoded release. Bootstrap services only after the queue,
runs and locks are proven empty and Liam approves activation.

## Restore Backend/frontend on a new server

The current backend is Vercel-hosted. A replacement environment requires:

1. verified source commit and lockfile;
2. Vercel project/domain ownership;
3. production environment variables restored through Vercel, not Git;
4. Supabase project URL and scoped keys;
5. migration inventory and effective grant/RLS audit;
6. Stripe webhook/product/price reconciliation;
7. authenticated route, relay, scheduler and dashboard tests;
8. deployment ID recorded before alias promotion.

The live deployment captured here lacks exact Git metadata, so identify and
certify a source commit before rebuilding it.

## Install BotApp

- build from a clean, certified BotApp commit;
- embed package provenance in the artifact;
- sign/notarize for production distribution;
- compare the produced `app.asar` hash with the release registry;
- install at `/Applications/BotApp.app`;
- verify relay/health views read-only before enabling write controls.

The current installed package is ad-hoc signed and therefore is reference
evidence, not the desired distribution model.

## Add a new phone

1. register the physical device in the backend inventory;
2. connect and explicitly accept ADB authorization on-device;
3. verify serial/device identity without logging sensitive identifiers;
4. publish heartbeat and confirm the intended inventory row only;
5. keep all account assignments disabled until the phone is certified;
6. test capacity/lease isolation without business actions.

## Add a new Instagram clone

1. create the clone through the approved AppCloner process;
2. record exact package name, clone index and immutable `app_instance_id`;
3. verify the package is installed and independently launchable;
4. bind a single account assignment with a new binding version;
5. verify foreground package and empty/expected identity manually;
6. never infer mapping from suffix or visual label;
7. perform Auto Login only after an account-specific GO;
8. verify no fallback touched another package.

## Acceptance gate

The new environment is not production-ready until provenance, offline suites,
secrets custody, service supervision, phone authorization, zero active objects
and a twelve-minute no-action runtime observation all pass.
