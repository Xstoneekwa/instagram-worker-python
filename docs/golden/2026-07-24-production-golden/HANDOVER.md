# Developer handover

## First hour

1. Read [README](README.md), [Architecture](ARCHITECTURE.md),
   [Locked decisions](LOCKED_DECISIONS.md) and
   [Known limitations](KNOWN_LIMITATIONS.md).
2. Verify the Golden tag and its parent relationship.
3. Verify the active symlink and release commit without restarting anything.
4. Run only `phonefarm-runtimectl ... status --json` for live checks.
5. Inspect queue/runs/locks before proposing any runtime action.
6. Read the flow-specific repository docs before touching implementation.

## Source of truth order

1. immutable release commit and Git tag;
2. active runtime symlink and process CWD;
3. live backend control-plane rows;
4. current Vercel deployment / installed BotApp artifact;
5. dated checkpoint documents;
6. historical conversations and screenshots.

A UI badge, stale heartbeat row or mutable checkout never outranks runtime
process/root evidence.

## Local layout

| Path | Purpose |
|---|---|
| `/Users/admin/phonefarm-worker-releases/` | immutable Worker releases |
| `/Users/admin/phonefarm-worker-current` | active atomic symlink |
| `/Users/admin/phonefarm-runtime/bin/` | stable runtime controller |
| `/Users/admin/phonefarm-runtime/env/` | out-of-repo secrets/config |
| `/Users/admin/phonefarm-runtime/logs/` | persistent service logs |
| `/Users/admin/phonefarm-runtime/run/` | PID, locks and state |
| `/Applications/BotApp.app` | installed operations application |

Never copy env files into Git or a release. Never run production from a mutable
checkout.

## Safe read-only orientation

```bash
git show golden-phonefarm-production-2026-07-24
git rev-parse golden-phonefarm-production-2026-07-24^{}
readlink /Users/admin/phonefarm-worker-current
git -C /Users/admin/phonefarm-worker-current rev-parse HEAD
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher status --json
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl heartbeat status --json
```

Do not use `dispatcher start/restart`, scheduler ticks, ADB or BotApp actions for
orientation.

## Development workflow

1. Create a dedicated clean worktree from the current business commit.
2. Define an exact file allowlist and non-regression boundaries.
3. Keep physical activation separate from code implementation.
4. Run targeted suites, protected Golden suites, full discovery, compilation,
   `git diff --check` and no-leak scans.
5. Commit/push non-forcibly.
6. Create a new immutable release only after explicit rollout approval.
7. Verify zero active request/run/lock before switching.
8. Switch atomically, restart only the required service once, then observe a
   minimum twelve-minute stability window.

## Flow ownership

| Topic | Start here |
|---|---|
| Auto Login | [AUTO_LOGIN.md](AUTO_LOGIN.md), `historical_auto_login_07ee/README.md` |
| Follow navigation | [FOLLOW_NAVIGATION.md](FOLLOW_NAVIGATION.md), `docs/navigation-engine.md` |
| Runtime | `docs/run-control-production.md` |
| Incidents | [INCIDENT_SYSTEM.md](INCIDENT_SYSTEM.md) |
| Restore/rollback | [RESTORE_PROCEDURE.md](RESTORE_PROCEDURE.md), [ROLLBACK.md](ROLLBACK.md) |
| New host | [INSTALL_NEW_MAC.md](INSTALL_NEW_MAC.md) |

## Escalation rules

Stop and obtain Liam approval before any phone action, credential use, migration,
entitlement change, package reassignment, scheduler tick, replay, release switch,
dispatcher restart or history rewrite. Preserve all exact identifiers but never
paste tokens, cookies, passwords, raw XML or service-role keys into tickets/docs.

## Handover completeness boundary

This document is complete for the Worker Golden checkpoint. The final commercial
Frontend/Stripe handover remains a separate deliverable after package changes,
cancellation, multi-account agency onboarding, occupied-phone Auto Login and
final production validations are closed.
