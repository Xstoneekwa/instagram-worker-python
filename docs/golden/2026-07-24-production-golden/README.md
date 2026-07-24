# Phone Farm Production Golden — 24 July 2026

Verdict: `GOLDEN_PHONEFARM_PRODUCTION_2026_07_24_LOCKED`.

This directory is the five-minute entry point for developers, operators,
auditors and prospective acquirers. It locks the Worker runtime and records the
live boundaries of the surrounding Backend, BotApp and frontend systems.

## Five-minute map

```text
Client dashboard / Admin dashboard / BotApp
                 │ authenticated APIs and relay
                 ▼
Backend + Supabase control plane
  requests · runs · assignments · locks · incidents · packages · schedules
                 │ durable queue
                 ▼
launchd → phonefarm-runtimectl → account_run_request_consumer.py
                 │ bound device + app instance + package
                 ▼
Worker orchestrators → Instagram on physical phones/clones
  Auto Login 07ee · Welcome · Follow · Like/Mute/Return CT · Unfollow · Outreach
```

## Locked production reference

- Worker code: `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f`.
- Parent Auto Login baseline: `abf0ebf90bcccd063355b41505d4d6541e870047`.
- Integrated navigation source: `f93c501c334b6c5b87ab04b72759bc60a452f5a8`.
- Release: `/Users/admin/phonefarm-worker-releases/abf0ebf-f93c501-navigation-consolidated-v1`.
- Pointer: `/Users/admin/phonefarm-worker-current`.
- Tag: `golden-phonefarm-production-2026-07-24`.
- Worker suite: `1850/1850`.
- Active requests/runs/locks at capture: `0/0/0`.

The documentation commit tagged by the Golden tag contains docs only. The
runtime continues to execute the immutable business commit above.

## What works and is certified

- isolated historical Auto Login 07ee routing;
- package/clone/app-instance binding and identity guard;
- bounded late post-login popup stabilization using existing handlers;
- f93c501 Follow list continuation, `See more`, true Suggestions boundary and
  CT rotation, certified offline and active;
- Golden Follow/Mute/verified Like/Return CT invariants;
- Welcome and Unfollow use of the shared list-boundary contract;
- active-SAST-day warmup and configured/package/ops cap resolution;
- durable dispatcher, preflight, locks, heartbeat, incidents and notification
  control planes.

## What remains bounded or pending

- no physical replay of f93c501 navigation was performed during activation;
- no post-patch physical replay of the late popup dismissal was performed;
- Welcome current-release outbound bubble and handoff remain pending physical
  completion;
- simultaneous two-phone business execution is not certified;
- one registered phone was ADB-unauthorized at the documentation snapshot;
- Vercel and installed BotApp live artifacts do not expose an exact Git commit
  in their inspected provenance;
- this checkpoint is not a final consolidated Frontend/Stripe commercial
  handover; those product domains require their own final certification.

## Repositories and artifacts

| Component | Repository/artifact | Golden treatment |
|---|---|---|
| Worker | `instagram-worker-python` | Exact commit, release and tag locked here |
| Backend/frontend | `boost-my-businesses-frontend` | Live Vercel deployment recorded; source SHA boundary explicit |
| BotApp | `phone-farm-botapp` and `/Applications/BotApp.app` | Installed package hash recorded; source provenance gap explicit |
| Supabase | Backend control plane | Read-only counts recorded; no migration in this checkpoint |
| Phones/clones | Physical Android inventory | Binding model documented; no device action in this checkpoint |

## System responsibility map

| Domain | Role at this checkpoint | Detailed source |
|---|---|---|
| Dispatcher | Claims queued requests, enforces preflight and starts one bound Worker run | [Architecture](ARCHITECTURE.md) |
| Scheduler | Embedded due-session producer; manual-only and natural scheduling rules remain authoritative | [Architecture](ARCHITECTURE.md) |
| Phones and Instagram packages | Physical execution targets selected through device, app-instance, package and clone binding | [Auto Login](AUTO_LOGIN.md) |
| Auto Login | Isolated historical 07ee engine through the production adapter | [Auto Login](AUTO_LOGIN.md) |
| Follow | Golden interaction flow plus f93c501 list continuation and CT rotation | [Follow navigation](FOLLOW_NAVIGATION.md) |
| Welcome | Uses the shared list-boundary contract; current-release physical outbound completion is pending | [Feature matrix](FEATURE_STATUS_MATRIX.md) |
| Unfollow | Uses the shared boundary contract with its existing J+3, time and cap rules | [Feature matrix](FEATURE_STATUS_MATRIX.md) |
| Outreach | Implemented Worker domain; not re-certified in this Golden sequence | [Known limitations](KNOWN_LIMITATIONS.md) |
| Incidents and notifications | Canonical operator-review lifecycle with Slack/Discord delivery projection | [Incident system](INCIDENT_SYSTEM.md) |
| Warmup | Active-day progression; effective caps remain the minimum of applicable sources | [Locked decisions](LOCKED_DECISIONS.md) |
| Commercial packages | Backend entitlement and cap source; Worker consumes the resolved contract | [Architecture](ARCHITECTURE.md) |
| Dashboard and BotApp | Client/admin control and operational projection surfaces; not Worker source of truth | [Handover](HANDOVER.md) |
| Stripe | External commercial lifecycle; intentionally outside this Worker Golden certification | [Technical due diligence](TECHNICAL_DUE_DILIGENCE.md) |

## Document index

- [Current production state](CURRENT_PRODUCTION_STATE.md)
- [Release registry](RELEASE_REGISTRY.md)
- [Locked decisions](LOCKED_DECISIONS.md)
- [Feature status matrix](FEATURE_STATUS_MATRIX.md)
- [Golden changelog](CHANGELOG_GOLDEN.md)
- [Architecture](ARCHITECTURE.md)
- [Handover](HANDOVER.md)
- [Technical due diligence](TECHNICAL_DUE_DILIGENCE.md)
- [Restore procedure](RESTORE_PROCEDURE.md)
- [Disaster recovery](DISASTER_RECOVERY.md)
- [Install on a new Mac](INSTALL_NEW_MAC.md)
- [Auto Login](AUTO_LOGIN.md)
- [Follow navigation](FOLLOW_NAVIGATION.md)
- [Incident system](INCIDENT_SYSTEM.md)
- [Known limitations](KNOWN_LIMITATIONS.md)
- [Test certification](TEST_CERTIFICATION.md)
- [Rollback](ROLLBACK.md)
- [Golden evidence](golden-evidence/README.md)

Start with `CURRENT_PRODUCTION_STATE.md` during an incident, `HANDOVER.md` when
joining the project, and `TECHNICAL_DUE_DILIGENCE.md` for acquisition review.
