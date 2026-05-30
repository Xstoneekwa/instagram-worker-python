# Settings Defaults + Runtime Wiring Audit

Status: audit only. No runtime patch, no dashboard patch, no dispatcher change, no run.

## Executive Summary

The current admin dashboard still contains two different classes of settings:

- draft/admin settings persisted in legacy `ig_account_settings`;
- runtime settings and gates actually consumed by the Python worker.

The important mismatch is that some dashboard controls can make a feature look enabled while the worker still blocks the path through an invisible runtime gate. The clearest example is Unfollow: `unfollow_mode=unfollow-any` can be displayed/configured as a dashboard setting, but account-session Unfollow will not run unless the worker env gate `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED` is also enabled and the mode is supported by the H3 real handoff path.

Product rule for future patches:

> A dashboard button must never write an incomplete setting. It must apply a complete runtime preset or refuse with a clear reason.

## Source Inventory

Audited sources:

- Worker config: `config.py`
- Runtime caps: `runtime_caps.py`
- Account session: `account_session_orchestrator.py`
- Welcome session: `welcome_session_orchestrator.py`, `welcome_scan_producer.py`, `welcome_list_sender.py`
- Generic DM sender: `dm_sender_engine.py`, `dm_real_send_flags.py`
- Outreach session: `outreach_session_orchestrator.py`
- Follow settings: `follow_settings.py`
- Unfollow settings/runtime: `unfollow_settings.py`, `unfollow_eligibility_engine.py`, `unfollow_session_orchestrator.py`
- RunControl dispatcher: `account_run_request_consumer.py`, `account_run_control.py`
- Dashboard registry/API/UI: `docs/dashboard-settings-registry.md`, frontend `run-control.ts`, settings route, Settings drawer, Growth Settings projection.

## Runtime Settings Matrix

| setting name | domain | source actuelle | worker consumes | visible dashboard | admin configurable | client configurable | global default | package default | package | preset required | risk | action |
|---|---|---|---:|---:|---:|---:|---|---|---|---|---|---|
| `INSTAGRAM_RUN_CONTROL_PLAY_ENABLED` | RunControl | dashboard env | frontend only | yes | ops/admin env | no | false | n/a | all | Play preset | high | keep ops-visible |
| `RUN_CONTROL_DISPATCHER_ENABLED` | RunControl | worker env | yes | health only | ops env | no | false | n/a | all | Dispatcher preset | high | keep ops-only |
| `RUN_CONTROL_DISPATCHER_HEALTH_ONLY` | RunControl | worker env | yes | status only | ops env | no | true | n/a | all | Dispatcher preset | high | read-only UI |
| `RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED` | RunControl | worker env + heartbeat metadata | yes | status only | ops env | no | false | n/a | all | Dispatcher preset | critical | read-only UI |
| `RUN_CONTROL_DISPATCHER_ALLOWED_RUN_TYPES` | RunControl | worker env | yes | no | ops env | no | account/outreach sessions | package-scoped | all | Package run preset | high | expose read-only |
| `RUN_CONTROL_DISPATCHER_TEST_ACCOUNT_IDS` | RunControl | worker env | yes | no | ops env | no | empty | n/a | smoke only | Run allowlist preset | high | ops-only |
| `RUN_CONTROL_DISPATCHER_REQUIRE_ASSIGNMENT` | Device/Safety | worker env | yes | no | ops env | no | false | true for prod packages | all | Assignment preset | high | wire preflight |
| `RUN_CONTROL_DISPATCHER_ENFORCE_ASSIGNMENT_WINDOW` | Device/Safety | worker env | yes | no | ops env | no | false | true for prod packages | all | Assignment preset | high | wire preflight |
| `INSTAGRAM_RUN_CONTROL_MINI_RUN_CAPS_REQUIRED` | Safety | dashboard env | frontend only | no | ops env | no | false | true for controlled mini-run | mini-run | Mini-run preset | high | keep ops-only |
| `WELCOME_DM_REAL_SEND_ENABLED` | Welcome | worker/frontend env | yes | status only | ops env | no | false | package dependent | Welcome/Full | Welcome preset | critical | keep ops-only status |
| `OUTREACH_DM_REAL_SEND_ENABLED` | Outreach | worker/frontend env | yes | status only | ops env | no | false | package/add-on dependent | Outreach | Outreach preset | critical | keep ops-only status |
| `DM_SENDER_REAL_SEND_ENABLED` | Legacy DM | worker env legacy fallback | unknown/generic only | no | ops env | no | false | n/a | legacy | none | critical | hide/deprecate |
| `ENABLE_REAL_DM_SEND` | Legacy DM | config/env legacy | partial internal compatibility | no | ops env | no | false | n/a | legacy | none | high | hide/deprecate |
| `welcome_enabled` | Welcome | `ig_account_dm_settings` | yes | shown via legacy key | admin domain API target | later entitlement-gated | false | package dependent | Welcome/Full | Welcome preset | high | wire |
| `welcome_template_id` | Welcome | `ig_account_dm_settings` -> `ig_dm_templates` | yes via settings/templates | message UI legacy | admin template API | client later | null | required if Welcome enabled | Welcome/Full | Welcome preset | high | wire template API |
| `welcome_per_session_limit` | Welcome/Quotas | `ig_account_dm_settings` | yes | legacy `max_dm_per_run` only | admin domain API | client later read/write if entitled | 10 | package cap | Welcome/Full | Welcome preset | medium | wire |
| `welcome_per_day_limit` | Welcome/Quotas | `ig_account_dm_settings` | yes | no | admin domain API | client later if entitled | 50 | package cap | Welcome/Full | Welcome preset | medium | expose effective read |
| `total_dm_per_day_limit` | Welcome/Outreach | `ig_account_dm_settings` | yes | no | admin domain API | client later read-only | 100 | package cap | DM packages | DM quota preset | medium | expose effective read |
| `check_chat_before_welcome` | Welcome/Safety | `ig_account_dm_settings` | yes | legacy field | admin domain API | client later | true | true | Welcome/Full | Welcome preset | low | wire |
| `welcome_skip_if_existing_thread` | Welcome/Safety | `ig_account_dm_settings` | yes | no | admin domain API | no | true | true | Welcome/Full | Welcome preset | low | expose read-only |
| `outreach_enabled` | Outreach | `ig_account_dm_settings` | yes | legacy `cold_dm_enabled` | admin domain API | client if add-on entitled | false | add-on dependent | Outreach | Outreach preset | critical | wire with entitlement |
| `default_outreach_template_id` | Outreach | `ig_account_dm_settings` -> `ig_dm_templates` | yes | legacy message | admin template API | client if add-on entitled | null | required if Outreach enabled | Outreach | Outreach preset | high | wire template API |
| `outreach_per_session_limit` | Outreach/Quotas | `ig_account_dm_settings` | yes | legacy `max_dm_per_run` conflates | admin domain API | client if add-on entitled | 25 | package/add-on cap | Outreach | Outreach preset | high | split from Welcome |
| `outreach_per_day_limit` | Outreach/Quotas | `ig_account_dm_settings` | yes | no | admin domain API | client later read/write if entitled | 80 | package/add-on cap | Outreach | Outreach preset | high | wire |
| `outreach_skip_if_existing_thread` | Outreach/Safety | `ig_account_dm_settings` | yes | no | admin domain API | no | true | true | Outreach | Outreach preset | low | expose read-only |
| `OUTREACH_HARD_MAX_PER_SESSION` | Outreach/Safety | worker env/config | yes | no | ops env | no | 5 | n/a | Outreach | Outreach preset | high | ops-only |
| `OUTREACH_HARD_MAX_PER_DAY` | Outreach/Safety | worker env/config | yes | no | ops env | no | 40 | n/a | Outreach | Outreach preset | high | ops-only |
| `STALE_OUTREACH_JOB_MINUTES` | Outreach/Safety | worker env/config | yes | no | ops env | no | 30 | n/a | Outreach | Outreach worker preset | medium | ops-only |
| `FOLLOW_MAX_PER_RUN` | Follow/Quotas | worker env/config | yes | preflight env only | ops env | no | 2 | package cap | Follow/Full | Follow preset | high | expose effective read |
| `FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN` | Follow/Safety | worker env/config | yes | preflight env only | ops env | no | 5 | package cap | Follow/Full | Follow preset | high | expose effective read |
| `follow_enabled` | Follow | legacy `ig_account_settings` | no proven consumer | visible | currently draft only | no | false | package dependent | Follow/Full | Follow preset | critical | hide until wired |
| `follow_limit` | Follow/Quotas | legacy `ig_account_settings` | no | visible | draft only | no | 20 | package cap | Follow/Full | Follow preset | high | hide/wire |
| `total_follows_limit` | Follow/Quotas | legacy `ig_account_settings` | no | visible | draft only | no | 100 | package cap | Follow/Full | Follow preset | high | hide/wire |
| `dont_follow_private_accounts` | Follow/Safety | `ig_account_follow_settings` | yes | filter semantic mismatch | admin domain API | client later | true | true | Follow/Full | Follow filters preset | medium | wire with renamed UI |
| `unfollow_enabled` | Unfollow | `ig_account_unfollow_settings` | yes | legacy duplicate | admin domain API | client later if entitled | false | package dependent | Follow/Full | Unfollow preset | critical | wire |
| `unfollow_mode` | Unfollow | `ig_account_unfollow_settings` | yes | split toggles `unfollow_any`, `unfollow_non_followers` | admin domain API | client later if entitled | `unfollow` | `unfollow` | Follow/Full | Unfollow mode preset | critical | replace toggles |
| `unfollow_after_days` | Unfollow/Safety | `ig_account_unfollow_settings` | yes | legacy delay | admin domain API | client later if entitled | 3 | 3 | Follow/Full | Unfollow preset | medium | wire |
| `unfollow_per_session_limit` | Unfollow/Quotas | `ig_account_unfollow_settings` | yes | legacy skip/total conflated | admin domain API | client later if entitled | 50 | package cap | Follow/Full | Unfollow preset | high | wire effective cap |
| `unfollow_per_day_limit` | Unfollow/Quotas | `ig_account_unfollow_settings` | yes | legacy total | admin domain API | client later if entitled | 200 | package cap | Follow/Full | Unfollow preset | high | wire |
| `unfollow_sort_mode` | Unfollow | `ig_account_unfollow_settings` | yes | legacy `sort_followers_mode` mismatch | admin domain API | client later if entitled | default | default | Follow/Full | Unfollow preset | medium | replace UI key |
| `UNFOLLOW_SESSION_REAL_ACTION_ENABLED` | Unfollow/Standalone | worker env/config | yes for standalone `unfollow_session` | no | ops env | no | false | n/a | Unfollow ops | Standalone Unfollow preset | critical | ops-only |
| `UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN` | Unfollow/Standalone | worker env/config | yes | no | ops env | no | 1 | n/a | Unfollow ops | Standalone Unfollow preset | high | ops-only |
| `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED` | Unfollow/Handoff | worker env/config | yes | no | ops env | no | false | package dependent | Follow/Full | Handoff preset | critical | wire preflight/status |
| `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS` | Unfollow/Handoff | worker env/config | yes | no | ops env | no | 1 | package cap | Follow/Full | Handoff preset | high | expose effective read |
| `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX` | Unfollow/Handoff | worker env/config | yes | no | ops env | no | 3 capped 10 | n/a | Follow/Full | Handoff preset | high | ops-only |
| `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_PROBE_ENABLED` | Unfollow/Diagnostics | worker env/config | yes | no | ops env | no | false | n/a | all | Diagnostics preset | medium | ops-only |
| `AUTO_RESTART_ENABLED` | Safety | worker env/config | yes | no | ops env | no | false | n/a | all | Recovery preset | high | ops-only |
| `RUNTIME_EVENTS_ENABLED` | Observability | worker env/config | yes | no | ops env | no | false | n/a | all | Observability preset | medium | ops-only |
| `RUNTIME_HEARTBEATS_ENABLED` | Observability | worker env/config | yes | health indirectly | ops env | no | false | n/a | all | Dispatcher preset | high | ops-only |
| `RUNTIME_INCIDENTS_ENABLED` | Safety/Incidents | worker env/config | yes | no | ops env | no | false | n/a | all | Incidents preset | medium | ops-only |
| `account_credentials.status` / `reauth_required` | Safety | DB | frontend preflight | status only | credentials API only | no | n/a | n/a | all | Credentials preset | critical | keep read-only |
| `account_dashboard_actions` blockers | Safety | DB | frontend preflight | action UI | action API | no | none | n/a | all | Support action preset | high | keep |
| `account_assignments` window | Device/Safety | DB + worker env enforcement | dispatcher/assignment resolver | partial | assignment API | no | n/a | package/device dependent | all | Assignment preset | high | wire preflight |
| `client_entitlements` / subscription modules | Package | DB | not directly in worker starts | growth dashboard projection | package admin API | client read later | n/a | package dependent | all | Package preset | critical | wire before client |
| `source_accounts` | Sources | legacy settings | no | visible | draft only | no | blank | n/a | legacy | none | medium | hide; use `ig_targets` |
| `ig_targets` | Follow/Sources | DB | yes | Targets panel | admin target API | client later | n/a | package dependent | Follow/Full | Target preset | high | keep/wire |
| `safe_review_mode` | Safety | legacy settings | no real-send gate | visible | draft only | no | true | n/a | admin | Review preset | high | rename/read-only |
| `dry_run_enabled` | Safety | legacy settings | no current run gate | visible/growth | draft only | no | true | n/a | admin | none | high | hide/ops-only |
| device internals (`device_udid`, app package, clone mode) | Device | legacy settings/account rows | worker uses assignments/config, not UI value | partly read-only | no | no | n/a | device assignment | all | Assignment preset | critical | hide/read-only |

Audited setting count: 57.

## Classification Summary

### 1. Visible dashboard and configurable

These can remain visible only after writes are routed to domain APIs:

- Welcome enabled, Welcome template, Welcome per-session/day caps.
- Outreach enabled, Outreach template, Outreach per-session/day caps.
- Follow private-account policy, once UI naming matches `dont_follow_private_accounts`.
- Unfollow enabled, mode, delay, per-session/day caps, once represented as one preset.
- Targets/CT rows through the Targets panel.

### 2. Invisible but default global mandatory

These must be visible as status or included in preflight, not writable as ordinary settings:

- `WELCOME_DM_REAL_SEND_ENABLED`
- `OUTREACH_DM_REAL_SEND_ENABLED`
- `FOLLOW_MAX_PER_RUN`
- `FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN`
- `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED`
- `RUN_CONTROL_DISPATCHER_*`
- assignment enforcement flags
- runtime heartbeat/events controls

### 3. Default by package

There is no single package defaults table consumed directly by the worker for all domains today. Existing defaults are split:

- DM defaults in `ig_account_dm_settings` schema.
- Follow private-account default in `follow_settings.py`.
- Unfollow defaults in `unfollow_settings.py` and `ig_account_unfollow_settings`.
- Package/entitlement metadata exists in subscriptions/modules/entitlements, but start preflight does not yet enforce complete package presets.

### 4. Ops/env kill switches only

Keep ops-only:

- real-send flags;
- dispatcher launch/health;
- hard caps;
- auto-restart;
- runtime events/incidents/heartbeats;
- device internals and clone/package assignment internals.

### 5. Legacy / should not be exposed

- `DM_SENDER_REAL_SEND_ENABLED`
- `ENABLE_REAL_DM_SEND`
- `send_enabled`
- `dry_run_enabled`
- `source_accounts`
- plaintext or legacy credential fields
- `device_udid`
- direct app package / clone-mode writes

### 6. Non-consumed runtime / db_only

Most Settings drawer fields persisted to `ig_account_settings` are still draft-only. They must not be described as live runtime settings until mapped to a domain table/API and verified by worker consumption.

## Package Defaults Matrix

| package | included domains | default runtime sources | default ON | add-on support | required hidden gates | notes |
|---|---|---|---|---|---|---|
| Package without Outreach | Follow/Unfollow or account management only | follow/unfollow domain rows + env caps | Outreach false | Outreach can be added | dispatcher/caps/assignment gates | Must not create Outreach jobs or require Outreach template. |
| Package with Welcome | Welcome | `ig_account_dm_settings`, `ig_dm_templates`, Welcome env gate | `welcome_enabled` per package | Outreach add-on optional | `WELCOME_DM_REAL_SEND_ENABLED`, Welcome caps | Welcome must not imply Outreach. |
| Follow/Unfollow | Follow + Unfollow | `ig_targets`, `ig_account_follow_settings`, `ig_account_unfollow_settings`, env caps | follow/unfollow by package | Outreach add-on optional | Follow caps, handoff real gate for account-session unfollow | Unfollow UI must show handoff readiness. |
| Full Cycle | Welcome + Follow + Unfollow | DM + Follow + Unfollow domain rows | package dependent | Outreach add-on optional | Welcome real-send, Follow caps, Handoff real gate | Preset must verify all enabled domains. |
| Outreach add-on alone | Outreach | `ig_account_dm_settings.outreach_*`, Outreach template, queue | Outreach true | n/a | `OUTREACH_DM_REAL_SEND_ENABLED`, queue/caps | Must work without Welcome. |
| Existing package + Outreach add-on | Base package + Outreach | base rows + Outreach rows/modules | base unchanged | Outreach true | separate Outreach real-send/caps/template | Must not change Welcome settings. |

## Dashboard Buttons and Current Wiring

| button/setting | what it changes today | worker consumes this field? | should modify/read instead | visibility/action |
|---|---|---:|---|---|
| Play RunControl | frontend env gates route availability | yes, frontend health only | `INSTAGRAM_RUN_CONTROL_PLAY_ENABLED` + heartbeat | keep admin/ops |
| Dispatcher launch/status | reads heartbeat `metadata.launch_enabled` | yes | status only from worker heartbeat | read-only |
| Start run | POST `/runs/start` with `account_session` | yes | keep route; add preset preflight | keep |
| Stop run | POST `/stop` | yes | keep; audited dashboard action later | keep |
| Welcome DM enabled | legacy `welcome_dm_enabled` in `ig_account_settings` | no | `ig_account_dm_settings.welcome_enabled` via domain API | wire |
| Welcome DM real-send | env only | yes | status from env/health; not normal setting | read-only ops |
| Welcome cap/session | legacy `max_dm_per_run` conflates DM domains | no direct | `welcome_per_session_limit` + hard cap | wire/split |
| Follow enabled | legacy `follow_enabled` | no proven worker gate | package entitlement + follow preset | hide until wired |
| Follow cap/run | legacy `follow_limit`; env cap actually used | legacy no | effective `FOLLOW_MAX_PER_RUN` + package cap | expose effective read |
| Follow iterations cap | env only | yes | status/effective cap | read-only ops |
| Unfollow mode | split legacy toggles | no direct | `ig_account_unfollow_settings.unfollow_mode` | replace with mode preset |
| Unfollow-any | legacy boolean | no direct | preset: mode + supported runtime + real handoff gate | hide until preset |
| Unfollow real handoff | env only | yes | `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED` status/preflight | read-only ops + preflight |
| Unfollow cap | legacy fields | partial/no | `unfollow_per_session_limit`, `unfollow_per_day_limit`, handoff caps | wire |
| Outreach enabled | legacy `cold_dm_enabled` | no | `ig_account_dm_settings.outreach_enabled` | wire with entitlement |
| Outreach real-send | env only | yes | status from env/health | read-only ops |
| Outreach standalone | run type exists | yes via dispatcher allowed run types | package/add-on entitlement + allowed run type | preset |
| Mini-run caps required | frontend env | yes frontend only | keep env preflight | ops-only |
| Assignment/device window | DB + worker env | dispatcher consumes only if env gates true | assignment projection + preflight check | wire |
| Package/entitlements | projections only | not start-gating fully | `client_entitlements` + subscription modules + package defaults | patch needed |

## Coherent Presets

### Preset: `unfollow-any ON`

Required checks/actions:

- Set `ig_account_unfollow_settings.unfollow_enabled=true`.
- Set `ig_account_unfollow_settings.unfollow_mode=unfollow-any`.
- Set a non-zero session/day cap.
- Confirm `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED=true` for account-session handoff, or refuse.
- Confirm runtime supports H3 real handoff for `unfollow-any`; current diagnostic treats `unfollow-any` as UI-dependent and not DB-strict.
- Confirm safe candidate strategy exists before claiming the UI is active.
- `/runs/start` must block if the preset is visually ON but the real gate is OFF.

If not possible, refuse activation with one of:

- `real_handoff_disabled`
- `unfollow_any_not_supported`
- `unfollow_cap_unproven`
- `no_safe_unfollow_strategy`

### Preset: `Welcome ON`

Required checks/actions:

- Set `ig_account_dm_settings.welcome_enabled=true`.
- Require active Welcome template.
- Require `WELCOME_DM_REAL_SEND_ENABLED=true` for real-send runs, or block start.
- Resolve effective cap from `min(DB per-session, env hard cap, daily remaining, total DM remaining)`.
- Confirm Welcome baseline state.
- Do not write or infer any Outreach field.

### Preset: `Outreach ON`

Required checks/actions:

- Confirm Outreach entitlement/add-on.
- Set `ig_account_dm_settings.outreach_enabled=true`.
- Require active Outreach template.
- Require `OUTREACH_DM_REAL_SEND_ENABLED=true` for real-send outreach sessions, or block start.
- Confirm queue and caps are ready.
- Do not require or mutate Welcome settings.

### Preset: `Follow ON`

Required checks/actions:

- Confirm package entitlement and CT/Targets.
- Resolve effective `FOLLOW_MAX_PER_RUN`.
- Resolve `FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN`.
- Wire private-account policy through `ig_account_follow_settings`.
- Block if caps are unproven for mini-run.

### Preset: `Full Cycle ON`

Required checks/actions:

- Compose Welcome + Follow + Unfollow presets.
- Enforce all real-send/real-action gates.
- Surface incomplete domains before request creation.
- Do not include Outreach unless Outreach add-on preset is explicitly active.

## `/runs/start` Preflight Gaps

Already checked:

- Play/dispatcher health.
- Dispatcher launch disabled.
- Account archived/trashed/canceled.
- Welcome real-send disabled when Welcome is enabled.
- Outreach real-send disabled for `outreach_session`.
- Mini-run Welcome/Follow caps.
- Outreach isolation for controlled account-session mini-runs.
- Dashboard credential/checkpoint actions.
- Credential status/reauth.
- Active run/request.

Must be added before next product-safe run:

- `unfollow-any` configured but `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED=false`.
- `unfollow-any` not supported by H3 real path.
- `unfollow_enabled=true` but effective handoff cap is 0.
- Follow enabled in UI but no CT/Targets or package entitlement.
- Assignment window expired or missing, when dispatcher requires assignment.
- Package/entitlement mismatch: UI enabled domain not entitled.
- Welcome enabled but active template missing.
- Outreach enabled but active Outreach template missing.
- Outreach enabled but dispatcher allowed run types do not allow `outreach_session` for standalone.
- Legacy `ig_account_settings` toggle differs from domain table runtime source.

## Blocking Mismatches Before Next Run

1. **Unfollow UI vs runtime gate**: dashboard can show `unfollow-any`, but account-session real Unfollow is blocked by `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED=false`.
2. **Unfollow mode support**: `unfollow-any` is accepted in DB settings, but account-session diagnostic treats it as UI-dependent and not launchable from DB-strict offline plan.
3. **Legacy settings route writes drafts**: `/api/instagram-dashboard/settings` writes `ig_account_settings`, while worker consumes `ig_account_dm_settings`, `ig_account_unfollow_settings`, `ig_account_follow_settings`, env caps and env kill switches.
4. **DM cap conflation**: UI `max_dm_per_run` does not distinguish Welcome vs Outreach caps.
5. **Real-send controls are invisible env gates**: current UI settings can imply send readiness while real-send env gates remain OFF.
6. **Package entitlements are not full start gates**: subscription modules exist, but `/runs/start` does not yet validate all enabled domains against entitlements/default presets.
7. **Assignment enforcement is partly hidden**: dispatcher can enforce assignment/window, but frontend preflight does not fully mirror the same checks.

## Settings to Keep Visible

Keep visible as admin controls after domain wiring:

- RunControl Play and dispatcher health/status.
- Start/Stop run.
- Welcome enabled/template/effective caps.
- Outreach enabled/template/effective caps, only with entitlement/add-on.
- Follow effective caps and target readiness.
- Unfollow preset readiness, not raw dangerous toggles.
- Assignment status and account/device window, as safe labels.
- Package/entitlement summary.

## Settings to Hide or Make Read-Only

Hide or make read-only:

- `send_enabled`, `dry_run_enabled`, `safe_review_mode` as real-send substitutes.
- `DM_SENDER_REAL_SEND_ENABLED`, `ENABLE_REAL_DM_SEND`.
- raw device/app/clone internals.
- `source_accounts` textarea.
- `unfollow_any` boolean toggle until replaced by preset.
- `follow_enabled` until a real Follow package preset exists.
- generic `max_dm_per_run` until split by domain.
- all credentials/password fields except safe status.

## Legacy UI Fields to Remove or Deprecate

- `ig_account_settings.password`
- `device_udid`
- `app_package`
- `cloned_app_mode`
- `send_enabled`
- `dry_run_enabled`
- `source_accounts`
- `current_run_status`
- raw counters like `interactions_count`
- follow/story/like percentages until runtime verified

## Prioritized Patch Plan

### P0 - No Incomplete Activation

1. Add `/runs/start` checks for Unfollow handoff:
   - `real_handoff_disabled`
   - `unfollow_any_not_supported`
   - `unfollow_cap_unproven`
   - `no_safe_unfollow_strategy`
2. Make dashboard Unfollow controls read-only or hidden until preset API exists.
3. Add status chips for hidden runtime gates: Welcome real-send, Outreach real-send, Follow cap, Unfollow real handoff, dispatcher launch.

### P1 - Domain Settings APIs

1. Create DM settings API that writes `ig_account_dm_settings` and template ids.
2. Create Unfollow settings API that writes `ig_account_unfollow_settings` as one preset.
3. Create Follow settings API for package entitlement + `ig_account_follow_settings`.
4. Stop treating `ig_account_settings` as runtime truth.

### P2 - Package Defaults

1. Define package/default preset rows or a resolver module.
2. Sync package modules to runtime domain rows explicitly.
3. Keep Outreach as separate add-on with independent template/caps/queue/metrics.

### P3 - Dashboard Refactor

1. Replace raw Settings drawer toggles with domain cards.
2. Show effective values, source labels and blockers.
3. Split admin-only status from client-safe controls.

## GO / NO-GO Before Next Mini-Run

NO-GO for a product-signoff run until the P0 Unfollow preflight gap is patched or the operator explicitly accepts that Unfollow real handoff is OFF and mandatory Unfollow is expected to be skipped.

GO for read-only dashboard/status work and domain preset design.

GO for another controlled engineering mini-run only if the operator states the expected Unfollow behavior:

- "Unfollow real handoff OFF, skip expected"; or
- "Unfollow real handoff ON, real Unfollow must execute or fail before request".

## No-Leak Notes

This document intentionally avoids raw env values, credentials, tokens, cookies, raw XML, screenshot paths and device serials.
