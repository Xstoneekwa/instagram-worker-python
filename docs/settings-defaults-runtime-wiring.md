# Settings Defaults + Runtime Wiring Audit

Status: audit only. No runtime patch, no dashboard patch, no dispatcher change, no run.

> **Implemented checkpoint — 2026-07-23:** Follow configuration now has a
> package-bounded active-day resolver. Package defaults initialize account
> values; package maxima bound writes; the Worker applies warmup and remaining
> quota independently. See
> [Active SAST Days V1](./checkpoints/2026-07-23-follow-warmup-active-sast-days-v1.md).

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
| `welcome_per_day_limit` | Welcome/Quotas | `ig_account_dm_settings` | yes | no | admin domain API | client later if entitled | 10 | product cap; lower values allowed | Welcome/Full | Welcome preset | high | source of truth; no UI-only clamp |
| `total_dm_per_day_limit` | Welcome/Outreach | `ig_account_dm_settings` | yes | no | admin domain API | client later read-only | 100 | package cap | DM packages | DM quota preset | medium | expose effective read |
| `check_chat_before_welcome` | Welcome/Safety | `ig_account_dm_settings` | yes | legacy field | admin domain API | client later | true | true | Welcome/Full | Welcome preset | low | wire |
| `welcome_skip_if_existing_thread` | Welcome/Safety | `ig_account_dm_settings` | yes | no | admin domain API | no | true | true | Welcome/Full | Welcome preset | low | expose read-only |
| `outreach_enabled` | Outreach | `ig_account_dm_settings` | yes | legacy `cold_dm_enabled` | admin domain API | client if add-on entitled | false | add-on dependent | Outreach | Outreach preset | critical | wire with entitlement |
| `default_outreach_template_id` | Outreach | `ig_account_dm_settings` -> `ig_dm_templates` | yes | legacy message | admin template API | client if add-on entitled | null | required if Outreach enabled | Outreach | Outreach preset | high | wire template API |
| `outreach_per_session_limit` | Outreach/Quotas | `ig_account_dm_settings` | yes | legacy `max_dm_per_run` conflates | admin domain API | client if add-on entitled | 25 | package/add-on cap | Outreach | Outreach preset | high | split from Welcome |
| `outreach_per_day_limit` | Outreach/Quotas | `ig_account_dm_settings` | yes | no | admin domain API | client later read/write if entitled | 30 | product/add-on cap; lower values allowed | Outreach | Outreach preset | high | source of truth; no UI-only clamp |
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
| device internals (raw device/app/clone identifiers) | Device | legacy settings/account rows | worker uses assignments/config, not UI value | partly read-only | no | no | n/a | device assignment | all | Assignment preset | critical | hide/read-only |

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
- raw device identifiers
- direct app package / clone-mode writes

### 6. Non-consumed runtime / db_only

Most Settings drawer fields persisted to `ig_account_settings` are still draft-only. They must not be described as live runtime settings until mapped to a domain table/API and verified by worker consumption.

## Package Defaults Matrix

Product defaults to encode in package/preset resolvers:

- Global default for all accounts: Follow and Unfollow in standard Follow mode are ON; DM and Outreach are OFF.
- Growth: 80 follows/day, 80 unfollows/day, Welcome DM OFF, advanced CT research OFF/future, AI comments OFF/future, AI targeting OFF/future, Outreach OFF unless purchased as an add-on.
- Pro: 120 follows/day, 120 unfollows/day, Welcome DM ON, advanced CT research planned/future, Outreach OFF unless purchased as an add-on.
- Premium: 120 follows/day, 120 unfollows/day, Welcome DM ON, advanced CT research ON/future, AI comments ON/future, AI targeting ON/future, Outreach OFF unless purchased as an add-on.
- Outreach DM is standalone or add-on/upsell. It can be purchased alone or added to Growth, Pro or Premium.
- Outreach does not depend on Welcome. Welcome ON must never enable Outreach, and Outreach ON must never require Welcome.
- When a package is applied, runtime domain rows and hidden gates must be aligned automatically. When an admin manually changes a setting, dependent runtime gates must realign or the write/start must be refused with a stable reason.

| package | included domains | default runtime sources | default ON | add-on support | required hidden gates | notes |
|---|---|---|---|---|---|---|
| Growth | Follow + Unfollow | follow/unfollow domain rows + package cap resolver | Follow ON, Unfollow ON, Welcome OFF, Outreach OFF | Outreach add-on optional | Follow caps, handoff readiness if Unfollow is required | 80 follows/day and 80 unfollows/day. Future CT/AI modules OFF. |
| Pro | Follow + Unfollow + Welcome | DM + follow/unfollow domain rows + package cap resolver | Follow ON, Unfollow ON, Welcome ON, Outreach OFF | Outreach add-on optional | Welcome real-send, Follow caps, handoff readiness | 120 follows/day and 120 unfollows/day. Future advanced CT dashboard planned. |
| Premium | Follow + Unfollow + Welcome + future advanced modules | DM + follow/unfollow domain rows + package cap resolver | Follow ON, Unfollow ON, Welcome ON, Outreach OFF | Outreach add-on optional | Welcome real-send, Follow caps, handoff readiness | 120 follows/day and 120 unfollows/day. Future CT/AI modules ON when implemented. |
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
| Welcome DM message/template | legacy `welcome_dm_message` in `ig_account_settings` | no | `ig_dm_templates` + `welcome_template_id` via shared client/admin template API | editable after domain API |
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
| Outreach DM message/template | legacy `cold_dm_message` in `ig_account_settings` | no | `ig_dm_templates` + `default_outreach_template_id` via shared client/admin template API | editable after entitlement/domain API |
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
- all raw credential fields except safe status.

## Legacy UI Fields to Remove or Deprecate

- raw legacy credential columns
- raw device identifier columns
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

This document intentionally avoids raw env values, credentials, auth material, raw XML, screenshot paths and device serials.

## Supabase Package / Entitlement Model Audit

Audit scope for Patch 1B:

- Supabase public schema: 20 product/settings/runtime tables, 400 columns.
- RPC/function surface: 36 relevant functions.
- Edge Functions: 5 deployed functions and 5 local source folders.
- Dashboard API routes: 18 `instagram-dashboard` routes.
- Settings/buttons/filters/gates represented below: 86 rows.

### Commercial Package vs Runtime Profile Taxonomy

Do not treat `full_cycle` or `outreach_only` as primary client commercial packages.

| concept | examples | source / owner | purpose | runtime effect |
|---|---|---|---|---|
| Commercial package | Growth, Pro, Premium | product/billing/admin package resolver | Defines included business features and default caps | Should apply domain defaults and entitlements. |
| Commercial add-on | Outreach add-on | product/billing/admin package resolver | Adds a paid feature to a base package | Should enable Outreach entitlement and preset without mutating Welcome. |
| Standalone commercial product | Outreach standalone | product/billing/admin package resolver | Sells Outreach without Growth/Pro/Premium | Should grant Outreach entitlement and an Outreach runtime path only. |
| Entitlement | `welcome`, `follow`, `unfollow`, `outreach` | `client_subscription_modules`, `client_entitlements` | Feature-level permission | Start/routes should verify feature permission before active runtime use. |
| Runtime profile | `full_cycle`, `outreach_only`, `account_session`, `outreach_session` | dispatcher/RunControl/assignment policy | Controls which worker/run path is allowed | Constrains `requested_run_type` and dispatcher allowed run types. |
| Assignment profile | phone assignment, clone assignment, timeslot/window, host/device | `account_assignments`, `phone_devices`, `phone_clones`, dispatcher env | Places an account on a device/clone/window | Dispatcher may refuse or skip launches without valid assignment. |

Example: a client can have the Pro commercial package plus Outreach add-on. The account can then be assigned to a `full_cycle` or `outreach_only` runtime profile depending on the phone, clone, timeslot and intended worker run type.

### Supabase Tables Found

| area | tables | status | notes |
|---|---|---|---|
| Product subscriptions | `client_subscriptions`, `client_subscription_accounts`, `client_subscription_modules` | partial | Existing model supports account-scoped subscriptions and per-feature modules. `subscription_type` currently stores runtime/assignment profiles such as `full_cycle` or `outreach_only`; no native Growth/Pro/Premium commercial package enum yet. |
| Entitlements | `client_entitlements` | partial | Existing feature codes are `outreach`, `welcome`, `follow`, `unfollow`; entitlement types are `standalone`, `addon`, `bundle`. This is useful for Outreach add-on/standalone, but not yet tied to runtime presets. |
| Account ownership/status | `clients`, `client_instagram_accounts`, `ig_accounts` | partial | Safe account/client linkage exists. Package labels are projections, not runtime default drivers. |
| Legacy dashboard settings | `ig_account_settings`, `ig_account_filters` | legacy / duplicate | Dashboard writes here. Many fields duplicate domain settings or are not consumed by the worker. |
| Domain settings | `ig_account_dm_settings`, `ig_account_follow_settings`, `ig_account_unfollow_settings`, `ig_targets` | partial ready | Worker consumes these for DM, private-follow policy, Unfollow and targets. Follow caps are still env-only. |
| DM runtime | `ig_dm_templates`, `ig_dm_jobs` | ready / partial | Templates/jobs exist. Outreach enqueue is entitlement-guarded. Welcome template readiness is not fully start-gated. |
| Run control | `account_run_requests`, `worker_heartbeats`, `ig_runs` | ready / partial | Queue/RPC/dispatcher path exists. Preflight still does not mirror all worker/device/package gates. |
| Actions and credentials | `account_dashboard_actions`, `account_credentials` | ready | Used for support/credential/reauth blocks. Keep safe status visible, never raw credential data. |
| Device assignment | `account_assignments`, `phone_devices`, `phone_clones` | partial | Dispatcher can require assignment; dashboard shows projections. Start preflight should enforce assignment/window when dispatcher requires it. |

### Supabase Columns and Constraints Found

| table | important columns / constraints | interpretation |
|---|---|---|
| `client_subscriptions` | `subscription_type` in `full_cycle`, `outreach_only`; `status`; `starts_at`; `ends_at`; `metadata` | Operational subscription/runtime profile exists, but it is not the commercial package. It does not encode Growth/Pro/Premium limits. |
| `client_subscription_modules` | `feature_code` in `welcome`, `follow`, `unfollow`, `outreach`; `enabled`; `entitlement_type` in `included`, `addon` | Good basis for feature-level presets. Missing package default resolver. |
| `client_entitlements` | `feature_code` in `outreach`, `welcome`, `follow`, `unfollow`; `entitlement_type` in `standalone`, `addon`, `bundle`; optional `account_id` | Good basis for account-scoped Outreach add-on/standalone and package-gated domains. |
| `ig_account_dm_settings` | `welcome_enabled`, `outreach_enabled`, template IDs, Welcome/Outreach session/day caps, total DM cap, skip flags | Worker source for Welcome/Outreach. Dashboard Settings does not write it yet. |
| `ig_account_follow_settings` | `dont_follow_private_accounts` | Worker source for private-follow policy only. Follow enable/caps are not here. |
| `ig_account_unfollow_settings` | `unfollow_enabled`, `unfollow_mode`, caps, sort, delay, `package_default_snapshot` | Worker source for Unfollow. DB accepts `unfollow-any`, but H3 real does not support it without future proof gates. |
| `ig_account_filters` | follower/following/business/private/post/word filters | Dashboard writes this table. Worker consumption is not proven for all filters; private-follow runtime uses `ig_account_follow_settings`, not `follow_private_profiles`. |
| `ig_account_settings` | many dashboard fields including `follow_enabled`, `welcome_dm_enabled`, `cold_dm_enabled`, `unfollow_any`, `send_enabled`, `dry_run_enabled` | Legacy draft settings; duplicate or misleading for Phone Farm runtime. |
| `account_run_requests` | safe request metadata, requested run type, status, cancel fields | Queue source. Must only be created after preflight passes. |
| `account_dashboard_actions` | action type/status/severity/audience/blocking flags/safe messages | Correct source for credential/checkpoint/support blockers. |

### RPCs / Functions Found

| RPC group | functions found | readiness |
|---|---|---|
| RunControl | `create_account_run_request`, `claim_next_account_run_request`, `mark_account_run_request_starting`, `link_account_run_request_run`, `complete_account_run_request`, `cancel_account_run_request`, `reclaim_stale_account_run_requests`, `is_account_run_request_cancel_requested` | Ready queue primitives; start route must remain the preflight boundary. |
| Subscriptions/entitlements | `sync_client_subscription_entitlements`, `client_account_has_outreach_entitlement`, `client_can_enqueue_outreach`, validation functions | Useful for package wiring. Missing Growth/Pro/Premium package default application RPC. |
| Assignment/device | `auto_assign_account_from_subscription`, `validate_account_assignment` | Assignment foundation exists; start preflight still needs parity with dispatcher assignment requirements. |
| Outreach | `enqueue_outreach_dm_job`, `dm_job_idempotency_key_outreach` | Ready for Outreach queue, isolated from Welcome. |
| Dashboard actions | `upsert_account_dashboard_action`, `transition_account_dashboard_action`, status/incident sync helpers | Ready for blockers/actions. |
| Credentials | credential rotate/read/revoke/ingestion helpers | Existing safe credential pipeline. Keep outside settings presets. |
| CT/targets | target verification claim/scheduler helpers | CT quality foundation exists; package CT defaults are future. |

### Edge Functions Found

| Edge Function | writes / reads | status |
|---|---|---|
| `outreach-enqueue` | validates Outreach producer and calls `enqueue_outreach_dm_job`; reads DM settings and entitlement helper | Ready for Outreach add-on/standalone boundary. Does not depend on Welcome. |
| `instagram-credentials` | credential ingestion/rotation helpers | Ready for credential status flow; not a settings surface. |
| `dashboard-actions` | safe dashboard action count/list/mutations | Ready for support blockers. |
| `instagram-account-status` | status publisher into account/client status RPCs | Ready for status projection. |
| `admin-dashboard` | read-only/admin projection | Ready as projection only; no settings mutation. |

### Dashboard Routes Found

| route | current role | runtime truth status |
|---|---|---|
| `/settings` | reads/writes `ig_account_settings`; now also projects Unfollow runtime status in current working tree | Legacy write surface; needs domain preset APIs. |
| `/filters` | reads/writes `ig_account_filters` | Partial; worker consumption unproven for most filters. |
| `/runs/start` | enqueues `account_run_requests` after preflight | Correct boundary; needs global package/domain readiness checks. |
| `/runs/health` | reads dispatcher heartbeat | Ready for status, not a settings write. |
| `/stop` | cancels request/run | Ready but should remain action-only. |
| `/targets*` | manages `ig_targets` and verification | Useful for CT/Follow readiness. |
| `/templates*` | dashboard settings/filter templates | Draft replay only; not package/runtime presets. |
| `/devices` | admin device catalog/projection | Not dispatcher source of truth yet. |
| `/accounts/create`, `/accounts/lifecycle` | account creation/lifecycle/credential helpers | Keep separate from package presets. |
| `/logs`, `/stats`, `/statistics` | projections | Read-only. |

## Product Package Defaults

Target product defaults to encode into package/preset resolvers:

| package / mode | Follow | Unfollow | Welcome | Outreach | daily follows | daily unfollows | future modules |
|---|---:|---:|---:|---:|---:|---:|---|
| Global default | ON | ON, mode `unfollow` | OFF | OFF | package/env capped | package/env capped | none |
| Growth | ON | ON, mode `unfollow` | OFF | OFF unless add-on | 80 | 80 | advanced CT OFF/future, AI comments OFF/future, AI targeting OFF/future |
| Pro | ON | ON, mode `unfollow` | ON | OFF unless add-on | 120 | 120 | advanced CT planned/future |
| Premium | ON | ON, mode `unfollow` | ON | OFF unless add-on | 120 | 120 | advanced CT ON/future, AI comments ON/future, AI targeting ON/future |
| Outreach add-on | unchanged | unchanged | unchanged | ON | unchanged | unchanged | Outreach caps/template/queue only |
| Outreach standalone | OFF unless explicitly bundled | OFF unless explicitly bundled | OFF | ON | n/a | n/a | Outreach only |

Runtime profiles such as `full_cycle`, `outreach_only`, `account_session` and `outreach_session` are intentionally not rows in this commercial package table. They are operational profiles used by assignment/RunControl to decide where and how an entitled account may run.

Rules:

- Outreach can be purchased alone or added to Growth, Pro or Premium.
- Outreach does not depend on Welcome.
- Welcome ON must never enable Outreach.
- Outreach ON must never require Welcome.
- Applying a package must align domain rows and hidden runtime gates or mark the package as blocked.
- Manual admin changes must either realign dependencies atomically or be refused with a stable reason.

Current Supabase fit:

- Existing `client_subscriptions`/`client_subscription_modules`/`client_entitlements` can represent feature-level inclusion and add-ons.
- Existing model cannot yet distinguish Growth vs Pro vs Premium except through labels/projections/metadata.
- Existing `subscription_type` values such as `full_cycle` and `outreach_only` should be treated as runtime/assignment profiles, not as commercial packages.
- Existing model does not currently apply package defaults to `ig_account_dm_settings`, `ig_account_follow_settings`, `ig_account_unfollow_settings`, `ig_targets`, env caps or RunControl gates.

## Comprehensive Runtime Settings Matrix

Status legend: `ready` means current source is consumed and reasonably represented; `needs wiring` means a target exists but dashboard/preflight is incomplete; `read-only` means visible projection only; `hide` means do not present as a setting; `legacy`/`db_only`/`duplicate` means misleading if treated as runtime truth; `no-go` blocks product-safe run; `ops-only` means env/internal.

| # | UI / setting / gate | DB source today | env/runtime source | domain | package | current SoT | target SoT | real worker consumer | dashboard route/API | admin/client | status | dependencies / preflight | action |
|---:|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Play RunControl | none | `INSTAGRAM_RUN_CONTROL_PLAY_ENABLED` | RunControl | all | dashboard env | ops preset status | frontend only | `/runs/health`, `/runs/start` | admin | ready | dispatcher healthy | keep visible |
| 2 | Dispatcher health | `worker_heartbeats` | `RUN_CONTROL_DISPATCHER_*` | RunControl | all | worker heartbeat | worker heartbeat | dispatcher | `/runs/health` | admin | ready | freshness/status | keep visible |
| 3 | Dispatcher launch | heartbeat metadata | `RUN_CONTROL_DISPATCHER_LAUNCH_ENABLED` | RunControl | all | worker env/heartbeat | worker env/heartbeat | dispatcher | `/runs/start` | admin | ready | block `dispatcher_launch_disabled` | read-only |
| 4 | Allowed run types | none | `RUN_CONTROL_DISPATCHER_ALLOWED_RUN_TYPES` | RunControl | runtime profile | worker env | ops preset | dispatcher | preflight env mirror | admin | needs wiring | Outreach isolation | expose read-only |
| 5 | Manual start | `account_run_requests` | dispatcher queue | RunControl | all | start route | start route | dispatcher/runner | `/runs/start` | admin | ready | all global gates | keep |
| 6 | Stop run | `account_run_requests`, `ig_runs` | runner cancel polling | RunControl | all | stop route | action route | runner | `/stop` | admin | ready | active request/run | keep |
| 7 | Active request/run | `account_run_requests`, `ig_runs` | n/a | RunControl | all | DB | DB | runner/route | `/runs/start` | admin | ready | block duplicate | keep |
| 8 | Credentials status | `account_credentials` | n/a | Safety | all | DB | DB | preflight | `/runs/start`, actions | admin/client status | ready | block reauth/review | read-only |
| 9 | Dashboard action blockers | `account_dashboard_actions` | n/a | Safety | all | DB | DB | preflight | `/runs/start`, actions Edge | admin/client safe | ready | support/credential actions | keep |
| 10 | Assignment required | `account_assignments` | `RUN_CONTROL_DISPATCHER_REQUIRE_ASSIGNMENT` | Device | all | dispatcher | dispatcher + start mirror | dispatcher resolver | none/preflight missing | admin | needs wiring | missing/expired assignment | add preflight |
| 11 | Assignment window | `account_assignments` | `RUN_CONTROL_DISPATCHER_ENFORCE_ASSIGNMENT_WINDOW` | Device | all | dispatcher | dispatcher + start mirror | dispatcher resolver | none/preflight missing | admin | needs wiring | window valid | add preflight |
| 12 | Device internals | `phone_devices`, `phone_clones` | device config | Device | all | ops tables | ops tables | assignment resolver | `/devices` projection | admin | read-only | never raw client | keep projection |
| 13 | Commercial package label | subscriptions/metadata/projection | n/a | Package | Growth/Pro/Premium/Outreach | projection | package resolver | none | Manage/Growth projection | admin/client summary | needs wiring | package default exists | keep visible |
| 14 | Entitlement summary | `client_entitlements`, modules | n/a | Package | all/add-on | DB partial | DB entitlement + package preset | Outreach enqueue only | Manage/Growth projection | admin/client summary | needs wiring | entitlement active | keep visible |
| 15 | Growth package | not native | n/a | Package | Growth | label/metadata | package resolver | none | projection | admin | no-go | defaults not applied | implement preset |
| 16 | Pro package | not native | n/a | Package | Pro | label/metadata | package resolver | none | projection | admin | no-go | defaults not applied | implement preset |
| 17 | Premium package | not native | n/a | Package | Premium | label/metadata | package resolver | none | projection | admin | no-go | defaults not applied | implement preset |
| 18 | Outreach add-on | `client_entitlements`/modules | n/a | Package/Outreach | add-on | DB partial | entitlement + Outreach preset | `client_can_enqueue_outreach` | outreach Edge/projection | admin/client if entitled | needs wiring | template/caps/real-send | wire |
| 19 | Welcome enabled | legacy `welcome_dm_enabled`; domain `welcome_enabled` | n/a | Welcome | Pro/Premium | split | `ig_account_dm_settings.welcome_enabled` | account/welcome orchestrators | `/settings` legacy, `/runs/start` domain | admin | needs wiring | template + real-send + caps | domain preset |
| 20 | Welcome real-send | none | `WELCOME_DM_REAL_SEND_ENABLED` | Welcome | package | worker/dashboard env | ops status + preflight | DM sender/orchestrator | `/runs/start` | ops/admin status | ops-only | block if Welcome ON and off | read-only |
| 21 | Welcome template | `ig_dm_templates`, `welcome_template_id` | n/a | Welcome | Pro/Premium | domain | domain/template API | sender | templates routes draft/domain partial | admin/client later | needs wiring | active template | add preflight |
| 22 | Welcome session cap | domain `welcome_per_session_limit`; legacy `max_dm_per_run` | `WELCOME_SESSION_SEND_MAX_JOBS` | Welcome | Pro/Premium/mini | split | min(domain/env/day) | `runtime_caps`, sender | `/settings` legacy | admin | needs wiring | cap proven | expose effective |
| 23 | Welcome day cap | domain `welcome_per_day_limit`, total DM | n/a | Welcome | Pro/Premium | domain | domain + package | counters/sender | none | admin/client later | needs wiring | remaining >0 | expose effective |
| 24 | Check chat before welcome | legacy `check_chat_before_welcoming`; domain `check_chat_before_welcome` | n/a | Welcome | Pro/Premium | split | domain | DM sender | `/settings` legacy | admin | needs wiring | none | domain API |
| 25 | Welcome baseline | `welcome_baseline_completed_at` | n/a | Welcome | Welcome | domain | domain | welcome scan/send | none | admin | read-only | baseline completed | show status |
| 26 | Outreach enabled | legacy `cold_dm_enabled`; domain `outreach_enabled` | n/a | Outreach | add-on/standalone | split | domain + entitlement | outreach orchestrator/Edge | `/settings` legacy, Edge | admin/client if entitled | needs wiring | entitlement/template/real-send | preset |
| 27 | Outreach real-send | none | `OUTREACH_DM_REAL_SEND_ENABLED` | Outreach | add-on/standalone | worker/dashboard env | ops status + preflight | sender/orchestrator | `/runs/start` | ops/admin status | ops-only | block if Outreach run real-send off | read-only |
| 28 | Outreach template | `ig_dm_templates`, `default_outreach_template_id` | n/a | Outreach | add-on/standalone | domain | domain/template API | outreach Edge/orchestrator | templates routes | admin/client if entitled | needs wiring | active template | add preflight |
| 29 | Outreach session cap | domain `outreach_per_session_limit`; legacy `max_dm_per_run` | `OUTREACH_HARD_MAX_PER_SESSION` | Outreach | add-on/standalone | split | min(domain/env/day) | outreach orchestrator | `/settings` legacy | admin | needs wiring | cap proven | expose effective |
| 30 | Outreach day cap | domain `outreach_per_day_limit`, total DM | `OUTREACH_HARD_MAX_PER_DAY` | Outreach | add-on/standalone | domain/env | min(domain/env/day) | outreach orchestrator | none | admin/client later | needs wiring | remaining >0 | expose effective |
| 31 | Outreach queue | `ig_dm_jobs` | queue workers | Outreach | add-on/standalone | DB/RPC | DB/RPC | `enqueue_outreach_dm_job`, sender | Edge `outreach-enqueue` | admin/client if entitled | ready | entitlement + idempotency | keep |
| 32 | Outreach standalone run | `account_run_requests.requested_run_type` | allowed run types | Outreach | Outreach standalone + runtime profile | route/dispatcher support | explicit button/preset | dispatcher/orchestrator | no dashboard button | admin | needs wiring | entitlement/run type/template | add explicit control |
| 33 | Follow enabled | legacy `follow_enabled` | `ENABLE_FOLLOWERS_LIST_ENGINE`/orchestration | Follow | all packages | legacy/env | package preset + worker gate | account session/runner | `/settings` legacy | admin | needs wiring | targets/caps | replace with preset |
| 34 | Follow cap/run | legacy `follow_limit` | `FOLLOW_MAX_PER_RUN` | Follow | all | env | package cap + env hard cap | runner/runtime caps | preflight env mirror | admin | needs wiring | cap proven | expose effective |
| 35 | Follow daily cap | legacy `total_follows_limit` | none proven | Follow | all | legacy | package/domain cap model | not fully proven | `/settings` legacy | admin/client later | db_only | daily quota model | implement |
| 36 | Follow iterations | none | `FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN` | Follow | all/mini | env | env hard cap + preset | runtime caps/follow engine | preflight env mirror | admin | ops-only | cap proven | read-only |
| 37 | Follow private policy | `ig_account_follow_settings.dont_follow_private_accounts` | n/a | Follow filter | all | domain | domain | runner | none / legacy filter mismatch | admin | needs wiring | semantic mapping | domain API |
| 38 | Follow target accounts | `ig_targets` | n/a | Follow/CT | all | domain | domain | runner/supabase_client | `/targets*` | admin/client later | ready | target active/quality | keep |
| 39 | CT quality min/max | `ig_targets` metadata/columns | app constants | CT | future | targets route | CT policy resolver | target selection | `/targets*` | admin | needs wiring | verified/quality status | expose readiness |
| 40 | Legacy source accounts | `ig_account_settings.source_accounts` | none | Sources | legacy | legacy | `ig_targets` | no | `/settings` | admin | legacy | duplicate source model | hide/replace |
| 41 | Filters disable | `ig_account_filters.disable_filters` | none proven | Filters | all | DB | filter policy resolver | unproven | `/filters` | admin | db_only | filter engine | verify/wire |
| 42 | Skip followers/following | `ig_account_filters` | none proven | Filters | all | DB | filter policy resolver | unproven | `/filters` | admin | db_only | filter engine | verify/wire |
| 43 | Business filters | `ig_account_filters` | none proven | Filters | all | DB | filter policy resolver | unproven | `/filters` | admin | db_only | filter engine | verify/wire |
| 44 | Private filters | `ig_account_filters.follow_private_profiles` | n/a | Filters/Follow | all | DB duplicate | `ig_account_follow_settings` | runner uses inverse domain flag | `/filters` | admin | duplicate | semantic conflict | reconcile |
| 45 | Follower/following ranges | `ig_account_filters` | none proven | Filters | all | DB | filter policy resolver | unproven | `/filters` | admin | db_only | filter engine | verify/wire |
| 46 | Word/account filters | `ig_account_filters` | none proven | Filters | all | DB | filter policy resolver | unproven | `/filters` | admin | db_only | filter engine | verify/wire |
| 47 | Unfollow enabled | legacy `unfollow_enabled`; domain `unfollow_enabled` | handoff env | Unfollow | all | split | domain + handoff preset | account/unfollow sessions | `/settings` legacy, `/runs/start` domain | admin | needs wiring | real handoff/caps | preset |
| 48 | Unfollow mode | legacy booleans; domain `unfollow_mode` | H3 support | Unfollow | all | domain for worker | domain preset | account/unfollow sessions | `/settings` projection | admin | needs wiring | supported mode | replace raw toggles |
| 49 | Unfollow-any | legacy boolean; domain accepts mode | H3 support flags future | Unfollow | future/ops | unsafe split | complete preset or blocked | H3 rejects DB-strict unsupported | `/runs/start` gate | admin | no-go | real handoff/support/candidate | keep read-only/blocked |
| 50 | Unfollow real handoff | none | `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED` | Unfollow | all | worker env | ops preset + preflight | account session H3 | `/runs/start` mirror | ops/admin status | ops-only | block when required | read-only |
| 51 | Unfollow real max | none | `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS` | Unfollow | all/mini | worker env | ops hard cap + preflight | account session H3 | `/runs/start` mirror | ops/admin status | ops-only | >=1 when Unfollow mandatory | read-only |
| 52 | Unfollow hard max | none | `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX` | Unfollow | all | worker env | ops hard cap | account session H3 | `/runs/start` mirror | ops | ops-only | >=1 | read-only |
| 53 | Unfollow session cap | domain `unfollow_per_session_limit`; legacy skip/total | standalone/handoff caps | Unfollow | all | domain/env split | effective min(domain/env/day) | eligibility/session | `/settings` projection | admin | needs wiring | cap proven | expose effective |
| 54 | Unfollow day cap | domain `unfollow_per_day_limit`; legacy total | none | Unfollow | all | domain | package/domain | eligibility/session | legacy | admin/client later | needs wiring | daily remaining | wire |
| 55 | Unfollow delay | domain `unfollow_after_days`; legacy delay | n/a | Unfollow | all | split | domain | eligibility engine | legacy | admin | needs wiring | candidate plan | domain API |
| 56 | Unfollow candidate strategy | `ig_interacted_users` eligibility | H3 UI support | Unfollow | all | worker planning | readiness resolver | eligibility engine | none | admin | needs wiring | safe candidate exists | add preflight/status |
| 57 | Standalone Unfollow real action | domain settings | `UNFOLLOW_SESSION_REAL_ACTION_ENABLED` | Unfollow | ops | worker env | ops preset | `unfollow_session_orchestrator` | no UI | ops | ops-only | destructive real action | keep hidden |
| 58 | Mini-run required | none | `INSTAGRAM_RUN_CONTROL_MINI_RUN_CAPS_REQUIRED` | Safety | mini | dashboard env | ops preset | frontend preflight | `/runs/start` | ops/admin status | ops-only | cap proof | keep hidden/status |
| 59 | Mini-run Welcome cap | domain/env | env mirror | Safety | mini | dashboard env | worker + dashboard parity | welcome sender | `/runs/start` | ops | needs wiring | worker host parity | expose status |
| 60 | Mini-run Follow cap | env | env mirror | Safety | mini | dashboard env | worker + dashboard parity | runner | `/runs/start` | ops | needs wiring | worker host parity | expose status |
| 61 | Mini-run Outreach isolation | allowed run types | env mirror | Safety | mini | dashboard env | dispatcher + dashboard parity | dispatcher | `/runs/start` | ops | needs wiring | account_session only | expose status |
| 62 | Safe review mode | legacy `safe_review_mode` | none | Safety | legacy | legacy | dashboard-only note | no | `/settings` | admin | legacy | not real-send gate | hide/rename |
| 63 | Dry run | legacy `dry_run_enabled` | run type/env dry-run caps | Safety | legacy | legacy | ops/session mode | partial | `/settings` | admin | legacy | not runtime gate | hide/read-only |
| 64 | Send enabled | legacy `send_enabled` | domain real-send envs | DM | legacy | legacy | ops status only | no | `/settings`/Growth | admin | duplicate | misleading | hide |
| 65 | Session quotas | legacy settings | config session caps | Quotas | all | mixed | package quota resolver | partial | `/settings` | admin | needs wiring | quota policy | resolver |
| 66 | Daily quotas | legacy/domain | counters | Quotas | all | mixed | package/domain/counter | partial | multiple | admin/client later | needs wiring | remaining proof | resolver |
| 67 | Login challenge gates | `account_dashboard_actions`, credentials/status | recovery signals | Safety | all | DB/status | DB/status | preflight/worker | `/runs/start` | admin/client status | ready/partial | precise login/action reasons | keep |
| 68 | Identity mismatch gate | incidents/actions/status | worker incident publisher | Safety | all | DB action | DB action | preflight | `/runs/start` | admin | needs wiring | review action | keep |
| 69 | Recovery/auto-restart | incidents/events | `AUTO_RESTART_*` | Safety | all | env | ops preset | recovery/resume | none | ops | ops-only | bounded retries | hide |
| 70 | Runtime events/incidents | runtime tables/events | `RUNTIME_*` | Observability | all | env | ops preset | publishers | projections | ops/admin status | ops-only | observability | read-only |
| 71 | Templates apply | dashboard template tables/routes | n/a | Dashboard drafts | admin | draft payload | preset templates separate | no | `/templates*` | admin | db_only | no runtime proof | label draft |
| 72 | Account lifecycle | account/status/actions | n/a | Lifecycle | all | DB/actions | lifecycle API | preflight reads status | `/accounts/lifecycle` | admin | ready | archived/trashed/canceled | keep |
| 73 | Add profile/create | accounts/settings/credentials | credentials Edge | Onboarding | all | route + Edge | onboarding workflow | status/preflight | `/accounts/create` | admin | ready/partial | credentials configured | keep |
| 74 | Stats/logs | logs/runs | n/a | Observability | all | projections | projections | no control | `/stats`, `/logs` | admin | read-only | no settings writes | keep |
| 75 | Devices page | Manage/Radar projections | n/a | Device | all | projection | assignment inventory | no direct | `/devices` | admin | read-only | not dispatcher SoT | keep projection |
| 76 | Growth Settings page | legacy settings + filters + Manage | n/a | Package/settings | all | projections | package/domain effective values | no | Growth page | admin/client later | needs wiring | effective values | refactor |
| 77 | Outreach Edge producer | `ig_dm_jobs`, settings, entitlements | n/a | Outreach | add-on/standalone | Edge/RPC | Edge/RPC | sender | Edge | admin/client/n8n | ready | entitlement/queue cap | keep |
| 78 | CT verification jobs | `ct_target_verification_jobs`, `ig_targets` | scheduler | CT | future | DB/RPC | DB/RPC | verifier | targets routes | admin | partial | provider proof | keep |
| 79 | Package default snapshot | `ig_account_unfollow_settings.package_default_snapshot` | n/a | Package/Unfollow | all | static snapshot | package resolver snapshot | loader only | none | admin | needs wiring | apply at package change | use |
| 80 | Welcome package default | none native | n/a | Package/Welcome | Pro/Premium | doc only | package resolver | none | none | admin | no-go | domain row/template | implement |
| 81 | Follow package default | none native | env | Package/Follow | Growth/Pro/Premium | doc/env | package resolver + env cap | runner | none | admin | no-go | cap/targets | implement |
| 82 | Unfollow package default | domain defaults | handoff env | Package/Unfollow | Growth/Pro/Premium | partial | package resolver + handoff status | loader/H3 | none | admin | no-go | cap/handoff | implement |
| 83 | Outreach package default | entitlement partial | real-send env | Package/Outreach | add-on/standalone | partial | entitlement + preset | Edge/orchestrator | Edge/routes | admin/client entitled | needs wiring | real-send/template/caps | implement |
| 84 | Client visibility | entitlements/projections | n/a | Dashboard | all | mixed | client-safe projection | no | Growth/Manage | client/admin | needs wiring | hide ops/legacy | classify |
| 85 | Admin manual setting change | legacy route | n/a | Dashboard | all | `ig_account_settings` | domain preset API | partial/no | `/settings` | admin | no-go | dependencies | refuse or apply preset |
| 86 | Package apply action | none | n/a | Package | all | missing | package preset API/RPC | none | missing | admin | no-go | complete domain/env readiness | build |

## Dashboard Buttons To True Runtime Sources

| button / setting | displays today | writes today | worker consumes? | should read/write instead | recommendation |
|---|---|---|---:|---|---|
| Play RunControl | dispatcher health/play state | no write | yes, via start route/dispatcher | health + launch + package readiness | keep visible |
| Dispatcher launch/status | heartbeat metadata | no write | yes | worker heartbeat only | read-only |
| Welcome DM enabled | legacy toggle | `ig_account_settings.welcome_dm_enabled` | no | `ig_account_dm_settings.welcome_enabled` via Welcome preset | replace with preset |
| Welcome real-send | not normal field | no write | yes env | status from ops env/health | read-only ops |
| Welcome cap/session | legacy max DM field | `ig_account_settings.max_dm_per_run` | no direct | `welcome_per_session_limit` + env hard cap + remaining quota | replace/expose effective |
| Follow enabled | legacy Growth/Settings field | `ig_account_settings.follow_enabled` | no proven DB consumer | package Follow preset + targets + env gate | replace with preset |
| Follow cap/run | legacy cap | `ig_account_settings.follow_limit` | no, worker uses env | package cap + `FOLLOW_MAX_PER_RUN` | read-only effective until wired |
| Follow iterations cap | not normal field | no write | yes env | `FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN` status | read-only ops |
| Follow filters | filters drawer | `ig_account_filters` | unproven/mixed | filter policy resolver; private policy to `ig_account_follow_settings` | needs wiring |
| Unfollow mode | legacy booleans/runtime projection | legacy settings, partial projection | worker consumes domain | `ig_account_unfollow_settings.unfollow_mode` via preset | replace raw toggles |
| Unfollow-any | no longer safe as toggle in current working tree | legacy existed | H3 rejects unsupported mode | complete preset or blocked status | read-only/blocked until supported |
| Unfollow real handoff | not normal field | no write | yes env | `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_*` status | ops-only read-only |
| Unfollow cap | legacy fields/runtime projection | legacy settings | worker consumes domain/env | effective domain + handoff cap | expose effective |
| Outreach enabled | legacy Cold DM toggle | `ig_account_settings.cold_dm_enabled` | no | entitlement + `ig_account_dm_settings.outreach_enabled` | replace with Outreach preset |
| Outreach real-send | not normal field | no write | yes env | status from `OUTREACH_DM_REAL_SEND_ENABLED` | read-only ops |
| Outreach standalone | no button | would use run type | worker supports run type | explicit entitlement-gated run/preset | add later |
| Outreach caps/templates/queues | legacy DM + templates partial | mixed | worker/Edge consumes domain/jobs | Outreach preset + template + queue status | wire |
| Mini-run caps required | not normal field | no write | frontend only plus worker env | dashboard/worker env parity status | ops-only |
| Assignment/device window | safe labels | no runtime write | dispatcher can enforce | assignment resolver/readiness | read-only + preflight |
| Package/entitlements | Manage/Growth labels | no write | Outreach Edge partially | package model + entitlements + domain defaults | keep visible, not authoritative yet |
| CT filters/quality | Targets panel | `ig_targets` | yes for targets, quality partial | CT readiness resolver | keep, add readiness |
| Credentials/reauth actions | status/actions | actions route/Edge | preflight consumes | actions + credential status | keep safe status |

No dashboard button should be removed before operator validation. Buttons that are misleading should be relabeled read-only, marked draft-only, or replaced by complete preset flows.

## Package Presets

| preset | DB writes | env/gates required | caps | domain tables | blockers / message | visibility |
|---|---|---|---|---|---|---|
| Global default account | `ig_account_dm_settings` Welcome/Outreach false; `ig_account_unfollow_settings` enabled true mode `unfollow`; `ig_account_follow_settings` safe defaults | dispatcher/assignment status only | package/env defaults | DM/Follow/Unfollow/Targets | `package_preset_incomplete` if any domain write fails | admin preset, client summary |
| Growth | global + Follow/Unfollow enabled; Welcome/Outreach false; entitlement modules follow/unfollow | Follow env cap; handoff status if Unfollow mandatory | 80/day follow, 80/day unfollow | follow/unfollow/domain rows | `package_preset_incomplete`, `follow_cap_unproven`, `unfollow_cap_unproven` | admin preset, client-safe summary |
| Pro | Growth + Welcome enabled | `WELCOME_DM_REAL_SEND_ENABLED` status, template | 120/day follow, 120/day unfollow, Welcome cap | DM/follow/unfollow | `welcome_template_missing`, `welcome_real_send_disabled` | admin preset |
| Premium | Pro + future CT/AI modules when implemented | same + future module gates | 120/120 | same + future CT/AI | future modules blocked until implemented | admin preset |
| Outreach add-on | `client_entitlements`/module Outreach active, `ig_account_dm_settings.outreach_enabled=true`, template/caps | `OUTREACH_DM_REAL_SEND_ENABLED`, allowed run type/queue | Outreach session/day caps | DM settings/templates/jobs | `outreach_entitlement_missing`, `outreach_template_missing`, `outreach_real_send_disabled` | admin/client if entitled |
| Outreach standalone | Outreach commercial product + Outreach entitlement; optional runtime profile `outreach_only`; base domains off unless separately entitled | dispatcher allows `outreach_session` | Outreach only | DM/jobs | same as add-on + `outreach_run_type_not_allowed` | admin/client if purchased |
| Welcome ON | `welcome_enabled=true`, template id, caps | Welcome real-send status | Welcome session/day/total DM | DM settings/templates | `welcome_template_missing`, `welcome_real_send_disabled`, `welcome_cap_unproven` | admin preset |
| Outreach ON | `outreach_enabled=true`, template id, caps | Outreach real-send, entitlement | Outreach session/day/total DM | DM settings/templates/jobs | `outreach_entitlement_missing`, `outreach_template_missing`, `outreach_real_send_disabled` | admin/client if entitled |
| Follow ON | package/module active, target readiness, private policy | `FOLLOW_MAX_PER_RUN`, iterations | package daily + env hard cap | follow settings/targets | `follow_entitlement_missing`, `follow_targets_missing`, `follow_cap_unproven` | admin preset |
| Unfollow mode | `unfollow_enabled=true`, mode, delay, caps | handoff status if account-session Unfollow required | session/day + real max | unfollow settings/interacted users | `real_handoff_disabled`, `unfollow_cap_unproven`, `no_safe_unfollow_strategy` | admin preset |
| Unfollow-any | mode `unfollow-any` only if supported | real handoff, H3 support proof, safe strategy proof | cap >= 1 | unfollow settings/interacted users | `real_handoff_disabled`, `unfollow_any_not_supported`, `unfollow_cap_unproven`, `no_safe_unfollow_strategy` | read-only blocked until supported |
| Full Cycle | compose Welcome + Follow + Unfollow; no Outreach unless add-on | all domain gates | min(package, DB, env, remaining) | all domain rows | first failing domain reason | admin preset |
| Mini-run safety | no product writes; ops env parity proof | mini caps, allowed run types | 1/1/1 as requested | preflight only | `mini_run_*_unproven` | ops-only |
| Dispatcher/RunControl ops | no account writes | dispatcher health/launch/allowed types/assignment | n/a | worker heartbeats/requests | `dispatcher_unhealthy`, `dispatcher_launch_disabled` | ops/admin read-only |

Runtime/assignment profiles are separate from package presets:

| operational profile | writes / sources | gates required | commercial package relationship |
|---|---|---|---|
| `full_cycle` | `client_subscriptions.subscription_type` today, dispatcher/assignment metadata target | valid phone/clone/window, allowed `account_session` | Can be used for Growth, Pro, Premium or any entitled account that should run full account sessions. |
| `outreach_only` | `client_subscriptions.subscription_type` today, dispatcher/assignment metadata target | valid phone/clone/window, allowed `outreach_session` | Can be used for Outreach standalone or Pro/Premium/Growth + Outreach add-on when only Outreach should run. |
| `account_session` | `account_run_requests.requested_run_type`, dispatcher allowed run types | account-session gates | Runtime request type, not a commercial package. |
| `outreach_session` | `account_run_requests.requested_run_type`, dispatcher allowed run types | Outreach entitlement/template/real-send/queue gates | Runtime request type, not a commercial package. |
| phone/clone/timeslot assignment | `account_assignments`, `phone_devices`, `phone_clones` | assignment present, assignment window valid | Operational capacity/placement, not a commercial package. |

## Manual Settings Presets

Manual admin changes should not write isolated legacy keys as if runtime were active.

| manual intent | allowed behavior | refusal / blocked reason |
|---|---|---|
| Enable Welcome | Apply Welcome ON preset or refuse before saving as active | `welcome_template_missing`, `welcome_real_send_disabled`, `welcome_cap_unproven` |
| Disable Welcome | Set `ig_account_dm_settings.welcome_enabled=false`; do not touch Outreach | n/a |
| Enable Outreach | Require entitlement/add-on and apply Outreach ON preset | `outreach_entitlement_missing`, `outreach_template_missing`, `outreach_real_send_disabled` |
| Disable Outreach | Set `outreach_enabled=false`; do not touch Welcome | n/a |
| Enable Follow | Apply Follow ON preset; require targets/caps | `follow_targets_missing`, `follow_cap_unproven` |
| Enable Unfollow standard | Apply Unfollow mode preset with `unfollow` and proven caps | `real_handoff_disabled` if real handoff is mandatory for the selected run mode |
| Enable Unfollow-any | Apply complete Unfollow-any preset only when H3 support and strategy are proven | `real_handoff_disabled`, `unfollow_any_not_supported`, `unfollow_cap_unproven`, `no_safe_unfollow_strategy` |
| Change filters | Save filter policy only after mapping to worker consumer or mark draft-only | `filter_policy_not_wired` |
| Apply package | Use package resolver/RPC based on existing subscription/entitlement model | `package_preset_incomplete` |

## Ops / Env Kill Switches

Keep these ops-only and expose only sanitized status/readiness:

- DM real-send: `WELCOME_DM_REAL_SEND_ENABLED`, `OUTREACH_DM_REAL_SEND_ENABLED`; legacy `DM_SENDER_REAL_SEND_ENABLED` hidden/deprecated.
- Follow hard caps: `FOLLOW_MAX_PER_RUN`, `FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN`.
- Welcome hard cap: `WELCOME_SESSION_SEND_MAX_JOBS`.
- Outreach hard caps: `OUTREACH_HARD_MAX_PER_SESSION`, `OUTREACH_HARD_MAX_PER_DAY`.
- Unfollow real actions: `UNFOLLOW_SESSION_REAL_ACTION_ENABLED`, `UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN`.
- Account-session Unfollow handoff: `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED`, `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS`, `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX`.
- RunControl: `RUN_CONTROL_DISPATCHER_*`, `INSTAGRAM_RUN_CONTROL_*`.
- Recovery/observability: `AUTO_RESTART_*`, `RUNTIME_*`.
- Device internals and clone/package identifiers.

## Cap Ownership And Editability

Caps shown to operators must distinguish business intent from ops safety ceilings. A dashboard/Supabase cap is the account or package setting. A runtime hard cap is an ops guardrail and must not silently keep production traffic at mini-run values. The user-facing effective cap is a computed read-only value: the lowest active limit for the next run.

For Followback/Unfollow today:

- `Unfollow cap/session` writes `ig_account_unfollow_settings.unfollow_per_session_limit`.
- `Unfollow cap/day` writes `ig_account_unfollow_settings.unfollow_per_day_limit`.
- `Effective cap now` is read-only and resolves from `min(unfollow_per_session_limit, remaining day quota, runtime mode cap when active, runtime gates)`.
- `Runtime cap mode` writes `ig_account_unfollow_settings.runtime_cap_mode` with values `prod_normal`, `mini_run`, or `incident_safety`.
- `Runtime safety cap` writes `ig_account_unfollow_settings.runtime_safety_cap`; it is used only in `mini_run` or `incident_safety`.
- `Limiting reason` must be visible when the effective cap is lower than the configured session cap.

Current Unfollow runtime caps:

- In `prod_normal`, standalone `unfollow_session`, account-session H3, `/runs/start`, and the Followback drawer align to the Supabase Unfollow domain caps and daily remaining quota. Env mini-run values are not allowed to silently lower production caps.
- In `mini_run` or `incident_safety`, runtime cap resolution uses `runtime_safety_cap`; when that DB value is missing, env values remain fallback/bootstrap/emergency caps.

| cap | business cap Supabase | package cap | ops hard cap | effective cap / limiting reason | editable by client? | editable by admin? | ops-only? |
|---|---|---|---|---|---|---|---|
| Follow cap/session | future Follow domain; legacy `ig_account_settings.follow_limit` is not runtime truth | package/module follow allowance | `FOLLOW_MAX_PER_RUN` | min(package, domain, env, assignment/runtime gates) | no until domain preset | yes via Follow preset later | env/status only today |
| Follow iterations cap | none | package/runtime profile | `FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN` | min(runtime profile, env, assignment/runtime gates) | no | read-only status today | yes |
| Welcome DM cap/session | `ig_account_dm_settings.welcome_per_session_limit` | package Welcome allowance | `WELCOME_SESSION_SEND_MAX_JOBS` | min(domain, package, env, daily remaining, real-send gate) | no/client later by entitlement | yes via Welcome domain | env/status only |
| Welcome DM cap/day | `ig_account_dm_settings.welcome_per_day_limit` | package Welcome daily allowance | none direct; total DM/runtime gates still apply | min(domain, package, daily remaining, total DM remaining) | no/client later by entitlement | yes via Welcome domain | no |
| Outreach cap/session | `ig_account_dm_settings.outreach_per_session_limit` | Outreach add-on allowance | `OUTREACH_HARD_MAX_PER_SESSION` | min(domain, package, env, daily remaining, real-send gate) | no/client later by entitlement | yes via Outreach domain | env/status only |
| Outreach cap/day | `ig_account_dm_settings.outreach_per_day_limit` | Outreach add-on daily allowance | `OUTREACH_HARD_MAX_PER_DAY` | min(domain, package, env, daily remaining) | no/client later by entitlement | yes via Outreach domain | env/status only |
| Unfollow cap/session | `ig_account_unfollow_settings.unfollow_per_session_limit` | package Unfollow allowance pending resolver | `runtime_safety_cap` only in `mini_run`/`incident_safety`; env fallback if DB safety cap missing | min(domain, package when wired, remaining day quota, runtime mode cap when active, handoff/runtime gates) | no/client later by entitlement | yes via Unfollow domain | no hidden prod cap |
| Unfollow cap/day | `ig_account_unfollow_settings.unfollow_per_day_limit` | package Unfollow daily allowance pending resolver | none direct; runtime safety cap still applies per run | min(domain, package when wired, daily remaining) | no/client later by entitlement | yes via Unfollow domain | no |

Admin/ops runtime cap editing now starts in the Unfollow domain with sanitized fields and audit payloads. A broader ops-only preset surface can still add actor-scoped reasons, approvals, and incident expiry timestamps later.

## Unfollow Caps, Hard Caps, Quota Resume

Product meaning:

- `Unfollow cap/day` is the maximum Unfollow quota for the account in one UTC day.
- `Unfollow cap/session` is the maximum Unfollow quota for one run/session.
- `Effective cap now` is not "already done"; it is the remaining safe amount the next run can attempt after all active limits.

Recommended package defaults:

- Growth: `unfollow_per_day_limit=80`.
- Pro: `unfollow_per_day_limit=120`.
- Premium: `unfollow_per_day_limit=120`.
- For normal production, set `unfollow_per_session_limit` equal to the package day cap unless ops intentionally wants split sessions. The worker still applies daily remaining before each run, so session 2 can only consume `day_cap - done_today`.
- Use a lower session cap only when the product intentionally wants multiple smaller sessions for phone rest or pacing. That is a business/runtime-profile decision, not a hidden env safety cap.

Current resume and quota behavior:

- Done-today source: `ig_interacted_users.unfollowed_at` with `unfollow_result='success'`, counted by `supabase_client.count_successful_unfollows_today()`.
- `run_unfollow_session()` loads `ig_account_unfollow_settings`, counts done-today, computes `unfollow_day_remaining_today`, then applies `min(db_session, runtime_action_cap, db_day_remaining)`.
- H3 account-session handoff passes its effective H3 runtime cap as `real_action_max_override`; `run_unfollow_session()` still applies DB session and daily remaining after that.
- `account_session_resume_engine.py` is passive. It builds `quota_remaining` metadata from the account-session summary; it does not schedule or execute the next run.
- `account_session_manual_resume.py` also remains a preview. It explicitly marks quota overrides as planned metadata because runtime flags are not wired yet.
- Therefore, current protection against doing 120 + 120 on the same UTC day comes from the next worker run recalculating `done_today` from Supabase before acting, not from an active resume override.

Current production behavior after the ops/admin cap patch:

- `prod_normal` is the default runtime cap mode.
- H3 account-session handoff and standalone `unfollow_session` no longer treat `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS=1`, `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX=3`, or `UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN=1` as production truth.
- In `prod_normal`, Follow-to-Unfollow handoff is enabled from valid Supabase Unfollow domain settings (`unfollow_enabled=true`, supported mode, positive day/session caps). `ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED` remains a mini-run/incident fallback gate, not hidden production truth.
- `mini_run` intentionally lowers runtime cap using `ig_account_unfollow_settings.runtime_safety_cap` (for example `1`).
- `incident_safety` uses the same DB safety cap for temporary ops reductions.
- Env caps remain fallback/bootstrap/emergency when mini/safety mode is active and no DB safety cap is set.
- The dashboard should show `limited_by_mini_run_mode` or `limited_by_safety_cap` only when the selected runtime mode is explicitly mini/safety or daily remaining is lower.

Remaining design target:

1. Move broader ops presets to an ops-only DB table or RPC-backed model if multiple domains need shared profile management.
2. Add explicit package resolver/preset rows for Growth/Pro/Premium instead of inferring package from runtime subscription profiles.
3. Include safe candidate availability in `/runs/start` where cheap enough, or report it as a post-start worker limiting reason.
4. Wire active auto-restart scheduling so resume runs enqueue automatically with `remaining_today` semantics; the worker already recalculates daily remaining before acting.
5. Keep client/customer editing limited to business caps allowed by entitlement/package.

## Legacy / DB-Only Fields

Fields that must not be treated as runtime truth:

- `ig_account_settings.send_enabled`, `dry_run_enabled`, `follow_enabled`, `welcome_dm_enabled`, `cold_dm_enabled`, `max_dm_per_run`, `follow_limit`, `total_follows_limit`, `unfollow_any`, `unfollow_non_followers`, `total_unfollows_limit`, `unfollow_delay_days`.
- `ig_account_settings.source_accounts`; use `ig_targets`.
- `ig_account_filters.follow_private_profiles`; reconcile with `ig_account_follow_settings.dont_follow_private_accounts`.
- Dashboard templates under `/templates*`; these replay draft settings and are not runtime package presets.
- Device raw/internal columns; keep safe labels only.

## Auto Restart Admin Tab V1

The admin dashboard exposes an `Auto Restart` tab as a read-only/dry-run planning surface until the scheduler contract is fully wired.

Current V1 contract:

- `GET /api/instagram-dashboard/auto-restart/overview` reads existing Supabase sources and returns scheduler status, preview rules, candidates, quota remaining, safety gates, and last restart-like runtime events.
- No `PATCH` route is active yet. Editable controls are disabled and labelled `configuration API pending`.
- No run or `account_run_requests` row is created by the page or overview API.
- Candidate planning uses the no-overrun rule: `planned_next_run_quota = min(session cap, day cap - done_today)` per service.
- Follow and Unfollow daily remaining use account/domain caps plus `ig_interacted_users` daily success markers.
- Welcome and Outreach preview currently derive daily counts from DM markers in `ig_interacted_users`; a dedicated DM event counter remains recommended before active scheduling.
- Active mode requires a persisted settings source such as `auto_restart_settings`, a decision/audit sink such as `auto_restart_decisions` or structured `runtime_events`, idempotency keys, max restart counters, phone-rest checks, 6h session-window checks, and dispatcher/run-control gates.

Required active-mode invariant:

- Auto Restart must never restart the full daily quota. For example, if an account has `unfollow_per_day_limit=120` and `50` successful unfollows already counted today, the next planned run can request at most `70` Unfollow actions.

## `/runs/start` Global Preflight Plan

Before `create_account_run_request`, start must block with HTTP 403, no `request_id`, no run, and a stable reason when any enabled package/domain is incoherent.

| domain | required block reasons |
|---|---|
| RunControl | `play_disabled`, `dispatcher_unhealthy`, `dispatcher_launch_disabled`, `invalid_run_type`, `already_running`, `already_requested` |
| Package/entitlement | `package_preset_incomplete`, `package_entitlement_missing`, `outreach_entitlement_missing` |
| Assignment/device | `assignment_missing`, `assignment_window_expired`, `device_assignment_unavailable` |
| Credentials/status | `credentials_review_required`, `reauth_required`, `login_verification_required`, `identity_mismatch_review_required`, `eligibility_query_failed` |
| Welcome | `welcome_real_send_disabled`, `welcome_template_missing`, `welcome_cap_unproven`, `welcome_baseline_missing` |
| Follow | `follow_targets_missing`, `follow_cap_unproven`, `follow_iterations_unproven`, `filter_policy_not_wired` |
| Unfollow | `real_handoff_disabled`, `unfollow_any_not_supported`, `unfollow_cap_unproven`, `no_safe_unfollow_strategy` |
| Outreach | `outreach_real_send_disabled`, `outreach_template_missing`, `outreach_cap_unproven`, `outreach_run_type_not_allowed` |
| Mini-run | `mini_run_welcome_cap_unproven`, `mini_run_follow_cap_unproven`, `mini_run_outreach_off_unproven`, `mini_run_unfollow_cap_unproven` |

Current implemented subset includes dispatcher/play/launch, account status, credential/action blockers, Welcome real-send, Outreach real-send for outreach sessions, mini-run Welcome/Follow/Outreach isolation, active run/request, and current working-tree Unfollow-any gates. Missing package/assignment/template/target/filter/global package preset checks are NO-GO for product-signoff.

## Patch Plan Prioritized

### Phase 1 - Docs + Supabase/Runtime/Dashboard Matrix

- Complete this audit and keep it as the source of truth before the next patch.
- No migration, no run, no dispatcher launch.

### Phase 2 - Package Preset Model From Existing Supabase Model

- Reuse `client_subscriptions`, `client_subscription_modules`, and `client_entitlements`.
- Add a resolver/API layer for Growth/Pro/Premium defaults without inventing a parallel model.
- Define how package labels map to modules and domain rows.

### Phase 3 - Dashboard Buttons To Domain APIs / Presets

- Replace legacy writes with domain preset APIs.
- Keep buttons visible but label draft-only/read-only until wired.
- Do not delete controls before operator validation.

### Phase 4 - RunControl Global Preflight

- Add package/domain/template/assignment/target/filter readiness gates before request creation.
- All blocked cases must return stable reasons and no request/run.

### Phase 5 - Mini-Run Readiness

- Add dashboard/worker env parity status for mini-run caps and allowed run types.
- Add DB postcheck before and after any future test run.

### Phase 6 - Real Test Run

- Only after presets and preflight agree with worker runtime.
- Operator must state expected Unfollow behavior before launch.

### Phase 7 - Outreach Standalone / Welcome + Outreach / Outreach Without Welcome

- Add explicit Outreach standalone UX.
- Prove Outreach add-on does not depend on Welcome and Welcome does not mutate Outreach.

## GO / NO-GO Updated

NO-GO before next mini-run.

Reasons:

- Growth/Pro/Premium package defaults are documented but not yet applied by a resolver or API.
- Dashboard settings still mostly write legacy `ig_account_settings`.
- Many filters are DB-only or semantically duplicate runtime settings.
- Package/entitlement checks are partial and mostly only enforced in Outreach enqueue, not `/runs/start`.
- Assignment/window/template/target readiness are not globally enforced before request creation.
- Unfollow-any remains unsupported by H3 real unless future support and safe strategy proof gates are enabled.

GO only for audit, preset design, and read-only status work.

## Drawer-by-drawer Settings Wiring Roadmap

Scope: 9 Settings drawer tabs in mandatory order, 88 visible fields classified. This is a roadmap only: no button removal, no runtime patch, no migration and no run.

Product defaults carried into this roadmap:

- Global default: Follow ON, standard Unfollow ON, DM OFF, Outreach OFF.
- Growth: 80 follows/day, 80 unfollows/day, Welcome OFF, Outreach OFF unless add-on; advanced CT/AI modules future OFF.
- Pro: 120 follows/day, 120 unfollows/day, Welcome ON, Outreach OFF unless add-on; advanced CT dashboard future.
- Premium: 120 follows/day, 120 unfollows/day, Welcome ON, Outreach OFF unless add-on; future advanced CT/AI modules ON when implemented.
- Outreach: standalone or add-on, independent from Welcome in both directions.
- Runtime profiles (`full_cycle`, `outreach_only`, `account_session`, `outreach_session`) and device/clone/timeslot assignments are operational routing concepts, not commercial packages. A Pro account with Outreach add-on may use different runtime profiles depending on the intended worker path and assigned phone/clone/window.

Table shorthand:

- Current source: dashboard drawer via `/api/instagram-dashboard/settings` unless noted; filter tab via `/api/instagram-dashboard/filters`.
- Current table: mostly `ig_account_settings`; filter tab uses `ig_account_filters`; runtime projections may read domain tables.
- Target source: package preset/domain API/effective read model.

### 1. General

| UI field | type | current source / table | route/API | target runtime source | worker consumer | package / entitlement | status | priority | action |
|---|---|---|---|---|---|---|---|---|---|
| Username | read-only | safe account/settings projection | `/settings` | account identity projection | preflight account row | all / n/a | read-only | P1 | Keep visible as identity label. |
| Display name | read-only | safe account/settings projection | `/settings` | account profile projection | none | all / n/a | read-only | P2 | Keep read-only. |
| Device name | read-only | legacy device label | `/settings` | assignment/device projection | assignment resolver | all / assignment | read-only | P0 | Replace with assignment readiness status. |
| Email display | read-only | safe credential projection | `/settings` | credential status API | credential preflight | all / n/a | read-only | P0 | Keep safe status only. |
| Credential status | read-only | safe credential projection | `/settings` | `account_credentials.status` | `/runs/start` credential gate | all / n/a | ready | P0 | Keep safe status; no raw credential material. |
| Two-factor enabled | toggle | `ig_account_settings.two_fa_enabled` | `/settings` | account login/status model | status publishers/preflight | all / n/a | needs wiring | P1 | Move to status API or read-only until proven. |
| Device assignment | read-only | synthetic label | `/settings` | `account_assignments` + `phone_devices`/`phone_clones` | assignment resolver | all / assignment | needs wiring | P0 | Add start preflight for missing/window expired. |
| App package status | read-only | hidden projection | `/settings` | assignment clone/package projection | assignment resolver/device launcher | all / assignment | read-only | P1 | Keep as safe status only. |
| Clone assignment | read-only | hidden projection | `/settings` | assignment clone projection | assignment resolver/device launcher | all / assignment | read-only | P1 | Keep as safe status only. |
| Account status | read-only select | `ig_account_settings.account_status` + account row | `/settings` | lifecycle/status model | `/runs/start` account status gate | all / n/a | replace by preset | P0 | Replace with lifecycle/status API, not editable setting. |
| Campaign name | input | `ig_account_settings.campaign_name` | `/settings` | package/account policy metadata | none proven | package label / entitlement summary | legacy | P2 | Keep admin label only; do not drive runtime. |

### 2. Schedule

| UI field | type | current source / table | route/API | target runtime source | worker consumer | package / entitlement | status | priority | action |
|---|---|---|---|---|---|---|---|---|---|
| Timeslot start | input | `ig_account_settings.timeslot_start` | `/settings` | session scheduler + assignment window | dispatcher/assignment future | all | db_only | P1 | Replace with scheduler policy; not P0 run gate yet. |
| Timeslot end | input | `ig_account_settings.timeslot_end` | `/settings` | session scheduler + assignment window | dispatcher/assignment future | all | db_only | P1 | Same as start. |
| Total sessions | input | `ig_account_settings.total_sessions` | `/settings` | package/day session quota resolver | none proven | package | db_only | P1 | Move to package quota preset. |
| Stop after minutes | input | `ig_account_settings.stop_interactions_after_minutes` | `/settings` | session runtime cap policy | partial config/session behavior only | all | needs wiring | P1 | Expose effective cap only after consumer proof. |
| Pause account days | input | `ig_account_settings.pause_account_days` | `/settings` | lifecycle pause action API | preflight account status after action | all | replace by preset | P1 | Use lifecycle action, not settings write. |
| Pause account until | input | `ig_account_settings.pause_account_until` | `/settings` | lifecycle pause action API | preflight account status after action | all | replace by preset | P1 | Same. |
| Randomize start | toggle | `ig_account_settings.randomize_start_enabled` | `/settings` | scheduler policy | none proven | all | db_only | P2 | Keep admin-only draft until scheduler exists. |

Schedule product note: do not keep legacy fields as runtime promises. Later mapping should cover session windows, phone rest, assignment windows and package day limits.

### 3. Actions

| UI field | type | current source / table | route/API | target runtime source | worker consumer | package / entitlement | status | priority | action |
|---|---|---|---|---|---|---|---|---|---|
| Follow limit | input | `ig_account_settings.follow_limit` | `/settings` | min(package cap, domain cap, `FOLLOW_MAX_PER_RUN`) | `runner.py`, `runtime_caps.py` env only today | Growth/Pro/Premium follow | replace by preset | P0 | Show effective Follow cap; write through Follow preset later. |
| Total follows limit | input | `ig_account_settings.total_follows_limit` | `/settings` | package/day follow quota | not fully proven | Growth/Pro/Premium follow | needs wiring | P0 | Add package quota resolver before run. |
| Total unfollows limit | input | `ig_account_settings.total_unfollows_limit` | `/settings` | `ig_account_unfollow_settings.unfollow_per_day_limit` + package cap | unfollow settings/eligibility | Growth/Pro/Premium unfollow | replace by preset | P0 | Wire to Unfollow preset/effective cap. |
| Unfollow delay days | input | `ig_account_settings.unfollow_delay_days` | `/settings` | `ig_account_unfollow_settings.unfollow_after_days` | eligibility engine | Growth/Pro/Premium unfollow | needs wiring | P0 | Wire via Unfollow mode preset. |
| Total likes limit | input | `ig_account_settings.total_likes_limit` | `/settings` | post-follow action policy | post-follow consumer not in package model | future | db_only | P3 | Hide or mark future until policy exists. |
| Likes per follow min | input | `ig_account_settings.likes_per_follow_min` | `/settings` | post-follow action policy | post-follow consumer not in package model | future | db_only | P3 | Same. |
| Likes per follow max | input | `ig_account_settings.likes_per_follow_max` | `/settings` | post-follow action policy | post-follow consumer not in package model | future | db_only | P3 | Same. |

P0 rule: no action field should appear active unless its env/domain/package gate is active or the UI says blocked.

### 4. DM

| UI field | type | current source / table | route/API | target runtime source | worker consumer | package / entitlement | status | priority | action |
|---|---|---|---|---|---|---|---|---|---|
| Welcome DM enabled | toggle | `ig_account_settings.welcome_dm_enabled` | `/settings` | `ig_account_dm_settings.welcome_enabled` | account/welcome orchestrators | Pro/Premium Welcome | target editable | P0 | Editable through Welcome domain API/preset; must not touch Outreach. |
| Welcome DM message/template | textarea/template selector | `ig_account_settings.welcome_dm_message` | `/settings` | `ig_dm_templates` + `welcome_template_id` | DM sender | Pro/Premium Welcome | target editable | P0 | Admin/client write same template source; preflight `welcome_template_missing`. |
| Outreach DM enabled | toggle | `ig_account_settings.cold_dm_enabled` | `/settings` | `ig_account_dm_settings.outreach_enabled` + entitlement | Outreach orchestrator/Edge | Outreach add-on/standalone | target editable | P0 | Editable through Outreach domain API/preset only when entitlement allows; independent from Welcome. |
| Outreach DM message/template | textarea/template selector | `ig_account_settings.cold_dm_message` | `/settings` | `ig_dm_templates` + `default_outreach_template_id` | Outreach Edge/orchestrator | Outreach add-on/standalone | target editable | P0 | Admin/client write same Outreach template source; reject without entitlement or valid template. |
| Welcome cap/session | input | legacy shared `ig_account_settings.max_dm_per_run` | `/settings` | `ig_account_dm_settings.welcome_per_session_limit` + effective cap resolver | Welcome orchestrators | Welcome | target editable if admin-configurable | P0 | Split from Outreach; save through domain API and show effective cap. |
| Outreach session/day caps | inputs | legacy shared `ig_account_settings.max_dm_per_run` | `/settings` | `ig_account_dm_settings.outreach_per_session_limit`, `outreach_per_day_limit` + effective cap resolver | Outreach orchestrator | Outreach add-on/standalone | target editable if admin-configurable | P0 | Split from Welcome; enforce package/add-on caps. |
| Legacy max DMs per run | input | `ig_account_settings.max_dm_per_run` | `/settings` | none as shared domain control | no valid shared consumer | legacy | no-go | P0 | Keep read-only legacy or hide; never use as common Welcome + Outreach cap. |
| Max consecutive DMs | input | `ig_account_settings.max_consecutive_dms` | `/settings` | DM pacing policy | not proven as runtime cap | DM packages | db_only | P1 | Keep admin draft until pacing consumer proven. |
| Check chat before welcoming | toggle | `ig_account_settings.check_chat_before_welcoming` | `/settings` | `ig_account_dm_settings.check_chat_before_welcome` | DM sender | Welcome | needs wiring | P1 | Wire to domain setting. |
| Safe review mode | toggle | `ig_account_settings.safe_review_mode` | `/settings` | none; ops/status only | no real-send gate | legacy | legacy | P0 | Do not present as real-send protection. |

DM product rule: Welcome and Outreach must remain separate. `WELCOME_DM_REAL_SEND_ENABLED` and `OUTREACH_DM_REAL_SEND_ENABLED` are ops-only status gates; neither dashboard toggle may imply them.

DM drawer target state:

- Current read-only runtime projection is an acceptable DM-1 transitional state only. It is not the final product state for useful DM controls.
- Final admin dashboard must let an authorized admin see, edit, save and apply the same Welcome/Outreach messages and toggles that the client dashboard uses.
- Client dashboard, admin dashboard and BotApp must share one source of truth: `ig_account_dm_settings` for toggles/caps and `ig_dm_templates` for Welcome/Outreach message bodies. No divergent admin-only legacy copy is allowed.
- Save must return only as a real domain save: update `ig_account_dm_settings`, upsert/version the selected `ig_dm_templates` row, audit actor/surface/field/old-new redacted summary, and make the effect visible to the next RunControl preflight and worker run.
- Save must reject invalid templates, missing Outreach entitlement, package cap violations, or cross-domain writes that would mutate Welcome while editing Outreach or mutate Outreach while editing Welcome.
- Ops-only/read-only fields can remain visible as projections: Welcome real-send status, Outreach real-send status, legacy `DM_SENDER_REAL_SEND_ENABLED`, legacy shared `max_dm_per_run`, and Outreach entitlement status.

### 5. Followback

| UI field | type | current source / table | route/API | target runtime source | worker consumer | package / entitlement | status | priority | action |
|---|---|---|---|---|---|---|---|---|---|
| Followback on followers | toggle | `ig_account_settings.followback_on_followers` | `/settings` | followback policy, if retained | not proven | future/follow | db_only | P2 | Verify consumer before exposing as active. |
| Max followback skips | input | `ig_account_settings.max_followback_skips` | `/settings` | followback/unfollow policy | not proven | future/follow | db_only | P2 | Same. |
| Max followback ignore | input | `ig_account_settings.max_followback_ignore` | `/settings` | followback/unfollow policy | not proven | future/follow | db_only | P2 | Same. |
| Sort followers mode | select | `ig_account_settings.sort_followers_mode` | `/settings` | `ig_account_unfollow_settings.unfollow_sort_mode` or scan policy | unfollow sort partial | Unfollow | duplicate | P1 | Reconcile with Unfollow preset. |
| Unfollow non-followers | toggle | `ig_account_settings.unfollow_non_followers` | `/settings` | `ig_account_unfollow_settings.unfollow_mode` | unfollow settings/eligibility | Unfollow | replace by preset | P0 | Replace with Unfollow mode preset. |
| Runtime Unfollow mode | read-only | `ig_account_unfollow_settings.unfollow_mode` projection | `/settings` | same | account/unfollow sessions | Unfollow | read-only | P0 | Keep until preset API exists. |
| Unfollow-any runtime state | read-only | readiness projection | `/settings` | H3 readiness resolver | account session H3 | Unfollow-any | read-only | P0 | Show configured/blocked state. |
| Unfollow-any block reason | read-only | readiness projection | `/settings` | `/runs/start` stable reason | account session H3 | Unfollow-any | read-only | P0 | Keep stable reason visible. |
| Runtime Unfollow session cap | read-only | `ig_account_unfollow_settings.unfollow_per_session_limit` projection | `/settings` | effective cap resolver | eligibility/session | Unfollow | read-only | P0 | Later show min(domain/env/package). |
| Unfollow skip limit | input | `ig_account_settings.unfollow_skip_limit` | `/settings` | candidate/skip policy if retained | not proven | Unfollow | db_only | P1 | Hide or map after consumer proof. |
| Mute posts after follow | toggle | `ig_account_settings.mute_posts_after_follow` | `/settings` | post-follow mute policy | post-follow flow partial | future | needs wiring | P2 | Keep future/admin-only. |
| Mute stories after follow | toggle | `ig_account_settings.mute_stories_after_follow` | `/settings` | post-follow mute policy | post-follow flow partial | future | needs wiring | P2 | Same. |
| Do follows first | toggle | `ig_account_settings.do_follows_first` | `/settings` | account session phase policy | not current runtime SoT | Full Cycle | db_only | P1 | Replace by Full Cycle preset if needed. |

Followback rule: distinguish metrics/performance from real action controls. Unfollow modes must be presets, not isolated toggles.

### 6. Sources

| UI field | type | current source / table | route/API | target runtime source | worker consumer | package / entitlement | status | priority | action |
|---|---|---|---|---|---|---|---|---|---|
| Truncate sources min | input | `ig_account_settings.truncate_sources_min` | `/settings` | CT/source policy | not proven | Follow/CT | legacy | P1 | Replace with CT target policy if needed. |
| Truncate sources max | input | `ig_account_settings.truncate_sources_max` | `/settings` | CT/source policy | not proven | Follow/CT | legacy | P1 | Same. |
| Change source if crash | toggle | `ig_account_settings.change_source_if_crash` | `/settings` | recovery/source policy | partial recovery behavior not domain wired | all | needs wiring | P1 | Move to recovery policy/status. |
| Skipped posts limit | input | `ig_account_settings.skipped_posts_limit` | `/settings` | navigation/source skip policy | not proven | Follow/CT | db_only | P2 | Keep draft only. |
| Fling when skipped | toggle | `ig_account_settings.fling_when_skipped` | `/settings` | navigation heuristic policy | not proven | Follow/CT | db_only | P2 | Keep draft only. |

Sources rule: `source_accounts` legacy should stay replaced by CT/`ig_targets`; do not maintain two CT truths.

### 7. Filters

| UI field | type | current source / table | route/API | target runtime source | worker consumer | package / entitlement | status | priority | action |
|---|---|---|---|---|---|---|---|---|---|
| Disable filters | toggle | `ig_account_filters.disable_filters` | `/filters` | filter policy resolver | not proven globally | Follow/CT | db_only | P1 | Verify consumer or label draft. |
| Skip followers | toggle | `ig_account_filters.skip_followers` | `/filters` | filter policy resolver | not proven | Follow/CT | db_only | P1 | Same. |
| Skip following | toggle | `ig_account_filters.skip_following` | `/filters` | filter policy resolver | not proven | Follow/CT | db_only | P1 | Same. |
| Skip business profiles | toggle | `ig_account_filters.skip_business_profiles` | `/filters` | filter policy resolver | not proven | Follow/CT | db_only | P1 | Same. |
| Skip non-business profiles | toggle | `ig_account_filters.skip_non_business_profiles` | `/filters` | filter policy resolver | not proven | Follow/CT | db_only | P1 | Same. |
| Follow private profiles | toggle | `ig_account_filters.follow_private_profiles` | `/filters` | inverse of `ig_account_follow_settings.dont_follow_private_accounts` | runner private skip | Follow | duplicate | P0 | Reconcile semantic conflict before run. |
| Follow only private profiles | toggle | `ig_account_filters.follow_only_private_profiles` | `/filters` | filter policy resolver | not proven | Follow | db_only | P1 | Hide/mark draft until consumer proof. |
| DM private profiles | toggle | `ig_account_filters.dm_private_profiles` | `/filters` | DM targeting policy | not proven | DM/Outreach | db_only | P2 | Future domain policy. |
| Minimum followers | input | `ig_account_filters.min_followers` | `/filters` | CT/filter policy + `ig_targets` quality | target route quality partial | Follow/CT | needs wiring | P1 | Align with CT quality resolver. |
| Maximum followers | input | `ig_account_filters.max_followers` | `/filters` | CT/filter policy + `ig_targets` quality | target route quality partial | Follow/CT | needs wiring | P1 | Same. |
| Minimum following | input | `ig_account_filters.min_following` | `/filters` | CT/filter policy | not proven | Follow/CT | db_only | P2 | Verify consumer. |
| Maximum following | input | `ig_account_filters.max_following` | `/filters` | CT/filter policy | not proven | Follow/CT | db_only | P2 | Verify consumer. |
| Minimum posts | input | `ig_account_filters.min_posts` | `/filters` | CT/filter policy | not proven | Follow/CT | db_only | P2 | Verify consumer. |
| Blacklisted words | textarea | `ig_account_filters.blacklisted_words` | `/filters` | content filter policy | not proven | Follow/CT | db_only | P2 | Keep admin-only draft. |
| Mandatory words | textarea | `ig_account_filters.mandatory_words` | `/filters` | content filter policy | not proven | Follow/CT | db_only | P2 | Same. |
| Whitelist words | textarea | `ig_account_filters.whitelist_words` | `/filters` | content filter policy | not proven | Follow/CT | db_only | P2 | Same. |
| Blacklist accounts | textarea | `ig_account_filters.blacklist_accounts` | `/filters` | target blacklist policy | not proven | Follow/CT | db_only | P2 | Same. |

Filters P0: private profile policy must have one truth before any product-signoff run.

### 8. Safety

| UI field | type | current source / table | route/API | target runtime source | worker consumer | package / entitlement | status | priority | action |
|---|---|---|---|---|---|---|---|---|---|
| Total interactions limit | input | `ig_account_settings.total_interactions_limit` | `/settings` | package/session quota resolver | config/session caps partial | all | needs wiring | P1 | Expose effective quota only. |
| Total successful interactions limit | input | `ig_account_settings.total_successful_interactions_limit` | `/settings` | package/session quota resolver | config/session caps partial | all | needs wiring | P1 | Same. |
| Interactions count | read-only | legacy counter projection | `/settings` | logs/domain counters | logs/runtime | all | read-only | P1 | Keep projection only. |
| End if follow limit reached | toggle | `ig_account_settings.end_if_follow_limit_reached` | `/settings` | session quota policy | not proven | Follow | db_only | P2 | Draft until consumer proof. |
| End if DM limit reached | toggle | `ig_account_settings.end_if_dm_limit_reached` | `/settings` | DM quota policy | not proven | DM | db_only | P2 | Draft until consumer proof. |
| End if likes limit reached | toggle | `ig_account_settings.end_if_likes_limit_reached` | `/settings` | post-follow quota policy | not proven | future | db_only | P3 | Future. |
| Max actions per hour | input | `ig_account_settings.max_actions_per_hour` | `/settings` | package/safety cap resolver | not proven | all | needs wiring | P1 | Package quota resolver. |
| Max actions per day | input | `ig_account_settings.max_actions_per_day` | `/settings` | package/safety cap resolver | not proven | all | needs wiring | P1 | Package quota resolver. |
| Random delay min seconds | input | `ig_account_settings.random_delay_min_seconds` | `/settings` | pacing domain policy | mixed config/DM settings | all | duplicate | P1 | Consolidate per domain. |
| Random delay max seconds | input | `ig_account_settings.random_delay_max_seconds` | `/settings` | pacing domain policy | mixed config/DM settings | all | duplicate | P1 | Consolidate per domain. |
| Warmup mode | toggle | `ig_account_settings.warmup_mode` | `/settings` | warmup/session policy | not proven | all | db_only | P2 | Draft only. |
| Stop on suspicious screen | toggle | `ig_account_settings.stop_on_suspicious_screen` | `/settings` | recovery/incident policy | recovery engine partial | all | needs wiring | P1 | Move to recovery policy or read-only. |
| Stop on login challenge | toggle | `ig_account_settings.stop_on_login_challenge` | `/settings` | credential/status action gates | worker/preflight status | all | duplicate | P0 | Use dashboard actions/status, not draft setting. |
| Stop on checkpoint | toggle | `ig_account_settings.stop_on_checkpoint` | `/settings` | credential/status action gates | worker/preflight status | all | duplicate | P0 | Same. |
| Stop on repeated navigation failure | toggle | `ig_account_settings.stop_on_repeated_navigation_failure` | `/settings` | recovery policy | recovery engine partial | all | needs wiring | P1 | Wire as recovery preset. |
| Max repeated errors | input | `ig_account_settings.max_repeated_errors` | `/settings` | recovery policy | recovery engine partial | all | needs wiring | P1 | Wire as recovery preset. |

Safety rule: admin-configurable policy must be separated from ops-only kill switches. Legacy `send_enabled`/`dry_run_enabled` must not claim real-send control.

### 9. Advanced

| UI field | type | current source / table | route/API | target runtime source | worker consumer | package / entitlement | status | priority | action |
|---|---|---|---|---|---|---|---|---|---|
| Current run status | read-only select | `ig_account_settings.current_run_status` | `/settings` | `ig_runs`, `account_run_requests`, worker heartbeat | RunControl | all | read-only | P0 | Replace legacy projection with real RunControl status. |
| Last error | read-only textarea | `ig_account_settings.last_error` | `/settings` | safe logs/incidents/actions | logs/incidents | all | read-only | P1 | Keep sanitized status only. |
| Last successful action | read-only | `ig_account_settings.last_successful_action` | `/settings` | action logs/domain counters | logs | all | read-only | P1 | Keep projection only. |
| Manual stop requested | read-only toggle | `ig_account_settings.manual_stop_requested` | `/settings` | Stop action / cancel request | runner cancel polling | all | legacy | P0 | Use Stop button; hide legacy field later. |

Advanced rule: projections/read-only only. No field here should be a fake runtime control.

## Preset Roadmap Details

| preset | tables it writes | runtime gates required | caps applied | entitlements checked | blocked reason |
|---|---|---|---|---|---|
| Growth preset | subscription/modules/entitlements; `ig_account_dm_settings`; `ig_account_follow_settings`; `ig_account_unfollow_settings` | dispatcher ready; Follow cap; Unfollow handoff status if mandatory | 80 follows/day, 80 unfollows/day, Welcome OFF, Outreach OFF | follow, unfollow | `package_preset_incomplete`, `follow_cap_unproven`, `unfollow_cap_unproven` |
| Pro preset | Growth writes + Welcome domain rows/template requirement | Welcome real-send status, template readiness | 120 follows/day, 120 unfollows/day, Welcome ON, Outreach OFF | follow, unfollow, welcome | `welcome_template_missing`, `welcome_real_send_disabled`, `package_preset_incomplete` |
| Premium preset | Pro writes + future advanced module flags when implemented | same as Pro plus future module readiness | 120/120 plus future CT/AI defaults | follow, unfollow, welcome, future modules | `future_module_not_supported`, `package_preset_incomplete` |
| Outreach add-on preset | entitlement/module Outreach; `ig_account_dm_settings.outreach_*`; template id | Outreach real-send, queue, dispatcher allowed run type if standalone | Outreach session/day/total DM caps | outreach addon | `outreach_entitlement_missing`, `outreach_template_missing`, `outreach_real_send_disabled` |
| Outreach standalone preset | Outreach add-on writes; base package domains OFF unless separately entitled | `outreach_session` allowed, queue ready | Outreach-only caps | outreach standalone | `outreach_run_type_not_allowed`, `outreach_template_missing` |
| Welcome ON preset | `ig_account_dm_settings.welcome_*`; `ig_dm_templates` selection | Welcome real-send, baseline/template readiness | Welcome session/day/total DM caps | welcome | `welcome_template_missing`, `welcome_real_send_disabled`, `welcome_cap_unproven` |
| Outreach ON preset | `ig_account_dm_settings.outreach_*`; `ig_dm_templates` selection | Outreach real-send, queue ready | Outreach session/day/total DM caps | outreach | `outreach_entitlement_missing`, `outreach_template_missing`, `outreach_cap_unproven` |
| Follow ON preset | package/entitlement link; `ig_account_follow_settings`; `ig_targets` readiness | Follow cap and iterations status | package daily + env hard caps | follow | `follow_targets_missing`, `follow_cap_unproven`, `follow_iterations_unproven` |
| Unfollow mode preset | `ig_account_unfollow_settings` mode/delay/caps | handoff status when account-session Unfollow required | session/day + real handoff max | unfollow | `real_handoff_disabled`, `unfollow_cap_unproven`, `no_safe_unfollow_strategy` |
| Mini-run safety preset | no product writes; ops readiness record/status | mini caps, allowed run types, dispatcher launch | 1 Welcome, 1 Follow, 1 Unfollow when required | relevant active domains | `mini_run_welcome_cap_unproven`, `mini_run_follow_cap_unproven`, `mini_run_unfollow_cap_unproven` |
| Full Cycle preset | compose Welcome + Follow + Unfollow; never include Outreach unless add-on active | all active domain gates | min(package, domain, env, remaining quota) | follow, unfollow, optional welcome | first failing domain reason |

### P0 DM Domain Presets - Implementation Notes

Welcome and Outreach remain separate product/runtime domains:

- `Welcome DM ON`: requires `ig_account_dm_settings.welcome_enabled=true`, an active Welcome template (`welcome_template_id` or active default `template_type='welcome'`), `WELCOME_DM_REAL_SEND_ENABLED=true` for real manual starts, and an effective Welcome cap >= 1. It never toggles or requires Outreach.
- `Welcome DM OFF`: `welcome_enabled=false`; account sessions do not require a Welcome template, Welcome real-send, or Welcome caps. It never modifies Outreach.
- `Outreach ON`: requires active Outreach entitlement, `ig_account_dm_settings.outreach_enabled=true`, an active Outreach template (`default_outreach_template_id` or active default `template_type='outreach'`), `OUTREACH_DM_REAL_SEND_ENABLED=true`, and Outreach session/day caps >= 1. It never depends on Welcome.
- `Outreach OFF`: `outreach_enabled=false` blocks `outreach_session` starts and sends no Outreach. It must not delete pending jobs unless a separate cleanup policy is explicitly requested, and it never modifies Welcome.

Legacy `max_dm_per_run`, `send_enabled`, `dry_run_enabled`, and `DM_SENDER_REAL_SEND_ENABLED` are not domain controls. Dashboard state should expose them only as read-only legacy/ops signals or hide them after operator validation. Welcome caps resolve from `welcome_per_session_limit` plus worker hard cap; Outreach caps resolve from `outreach_per_session_limit`, `outreach_per_day_limit`, total DM quota, and worker hard caps.

### DM Cap Source-Of-Truth Rule

Frontend, Supabase and runtime gates must stay aligned. The dashboard must not silently clamp an invalid database value for display only: if Supabase stores `welcome_per_day_limit=50`, the UI must either show `50` as invalid/requiring correction or the row must be corrected by an explicit, audited, targeted reset before validation. `/runs/start` must consume the same Supabase values and block invalid caps instead of relying on a frontend projection.

Product daily caps:

- Welcome DM defaults to `welcome_per_day_limit=10`; values lower than 10 are allowed, values above 10 are refused with `welcome_daily_cap_exceeded`.
- Outreach DM defaults to `outreach_per_day_limit=30`; values lower than 30 are allowed, values above 30 are refused with `outreach_daily_cap_exceeded`.
- Welcome and Outreach session caps must not exceed their day cap; violations are refused with `session_cap_exceeds_day_cap`.
- `max_dm_per_run` is never a source for Welcome or Outreach caps.

These defaults must be written into `ig_account_dm_settings` when a relevant service is created or enabled: account creation with a subscribed package, package application/reset, Welcome add-on activation, Outreach standalone/add-on activation, and an explicit admin reset-to-defaults action. Future package/reset APIs should use least-restrictive-preserving writes: insert defaults for missing rows, set `welcome_per_day_limit=10` only when null or above 10, set `outreach_per_day_limit=30` only when null or above 30, and preserve already-lower manual choices.

Existing-account backfill must be a separate operator-approved action. It should be idempotent, scoped to accounts with the relevant entitlement/service, audited, and avoid overwriting lower manual values. Safe pattern:

- `welcome_per_day_limit = 10` only where Welcome is subscribed/enabled and `welcome_per_day_limit is null or welcome_per_day_limit > 10`.
- `outreach_per_day_limit = 30` only where Outreach is subscribed/enabled and `outreach_per_day_limit is null or outreach_per_day_limit > 30`.
- Keep `welcome_per_day_limit=5` and `outreach_per_day_limit=20`.
- Record an audit row per account or per batch with actor, reason, old values and new values, without template bodies or credentials.

### P0 DM Drawer Delivery Phases

| phase | purpose | expected state |
|---|---|---|
| DM-1 read-only projection | expose current runtime truth without pretending legacy fields are live controls | Welcome/Outreach enabled status, template status, effective caps, real-send status, entitlement status shown read-only |
| DM-2 domain read/write API | replace legacy `/settings` writes with domain writes | admin/client-safe API writes `ig_account_dm_settings` independently for Welcome and Outreach, with entitlement/package validation |
| DM-3 editable messages/templates + real Save | make the useful DM fields editable again through the domain API | Welcome/Outreach message/template editors write `ig_dm_templates`, maintain selected template ids, audit changes, and avoid cross-domain mutation |
| DM-4 RunControl preflight on true values | block starts from real runtime sources | `/runs/start` consumes `ig_account_dm_settings`, `ig_dm_templates`, entitlements, real-send env status, and effective caps |
| DM-5 client/admin sync + audit/versioning | guarantee all surfaces operate on one source of truth | client dashboard, admin dashboard and BotApp read/write the same rows; template edits are versioned or auditable with actor/surface/redacted old-new summaries |

DM Save target contract:

- `Welcome DM enabled`, Welcome message/template and Welcome session cap are editable in the final target state through the Welcome domain API.
- `Outreach DM enabled`, Outreach message/template and Outreach session/day caps are editable in the final target state through the Outreach domain API when entitlement/package rules allow it.
- Real-send status, legacy global flags, legacy shared caps and entitlement status remain read-only projections or are hidden.
- The Save button must not be reintroduced as the legacy draft save for DM. It must commit to the true runtime tables and make the next preflight reflect the saved values.

## Required APIs / Domain Routes

| API / resolver | purpose | priority |
|---|---|---|
| Package preset resolver/API | map Growth/Pro/Premium/Outreach to entitlements and domain rows | P0 |
| DM domain settings API | write Welcome and Outreach independently, split templates and caps | P0 |
| Follow preset/API | write private policy and expose effective caps/targets readiness | P0 |
| Unfollow preset/API | write mode/delay/caps atomically and expose readiness | P0 |
| Filter policy resolver | prove which filters are consumed and resolve duplicates | P1 |
| Assignment readiness API | expose assignment/window status used by dispatcher | P0 |
| Effective caps API | return min(package, domain, env, remaining quota) without raw internals | P0 |
| Recovery/safety policy API | separate admin policy from ops-only controls | P1 |

## Required RunControl Preflights

P0 before next run:

- Package preset incomplete or missing entitlement for enabled domains.
- Welcome ON but missing template, real-send off, or cap not proven.
- Outreach ON but entitlement/template/real-send/cap/allowed run type not proven.
- Follow ON but target readiness, cap or iterations not proven.
- Private profile filter conflict between filter drawer and Follow domain setting.
- Unfollow mode/cap/handoff/candidate strategy not proven.
- Assignment missing or assignment window expired when dispatcher requires it.
- Credential/status/dashboard action blockers.
- Mini-run cap parity for dashboard and worker host.
- Active request/run duplicate.

## Drawer Roadmap Summary

Fields classified by tab:

- General: 11 fields.
- Schedule: 7 fields.
- Actions: 7 fields.
- DM: 8 legacy fields classified; target state splits them into editable Welcome/Outreach domain controls plus read-only ops projections.
- Followback: 13 fields.
- Sources: 5 fields.
- Filters: 17 fields.
- Safety: 16 fields.
- Advanced: 4 fields.

Total: 88 fields.

Fields to keep visible: identity/status projections, package/entitlement summary, RunControl status, effective caps, target readiness, credential/action safe status.

Fields to replace by presets/domain APIs: package selection, Welcome ON/OFF, editable Welcome template/message, Outreach ON/OFF, editable Outreach template/message, Follow ON, Unfollow mode, Full Cycle, mini-run safety.

Fields to keep read-only: device/clone/app package status, runtime Unfollow state until preset API exists, Advanced projections, dispatcher/real-send/env gate status, DM legacy global/shared controls, entitlement status projections.

Hide/remove candidates after operator validation: legacy draft fields that are not consumed, legacy source accounts, isolated destructive toggles, legacy real-send substitutes, duplicate filter fields.

GO / NO-GO before next patch:

- GO for focused docs and preset/API design.
- GO for a small P0 implementation patch only after operator chooses the first drawer/domain to wire.
- NO-GO for next test run until P0 preflights and package/domain preset alignment are implemented.

The Follow portion of the former P0 mismatch is resolved by the linked
checkpoint. Other domains and legacy drawer fields retain their existing
status; this checkpoint does not certify them.
