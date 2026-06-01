# Dashboard Settings Registry

This document is the source of truth for reconnecting the existing admin
`/instagram-dashboard` UI to the real Phone Farm backend.

Scope of this registry:

- read-only documentation only;
- no runtime patch;
- no UI patch;
- no migration;
- no Edge Function;
- no device run;
- no backend publish.

The audited UI currently lives outside this worker repository, now at:
`/Users/admin/Projects/boost-ai-frontend`. Do not modify that repo as part of
this registry.

## 1. Executive Summary

The current `/instagram-dashboard` is a rich admin prototype inherited from a
legacy GramBot/Appium model. It should not be discarded: it already contains
useful admin surfaces for account lists, KPIs, lifecycle tabs, recent runs,
statistics, logs, targets and a complete Settings drawer.

However, it must not be connected as-is to a client dashboard or treated as the
source of truth for Phone Farm runtime.

Key decisions:

- keep the UI as an admin prototype and migrate it progressively;
- do not expose this Settings surface to clients;
- do not keep `ig_account_settings` as the main backend source;
- do not assume that a setting is runtime-verified because it exists in the UI;
- reconnect reads first through safe admin projections;
- reconnect writes later through domain APIs with validation and audit;
- build the client dashboard separately from client-safe projections only.

Dashboard Foundation 1A already provides the first backend read-only foundation:

- `public.get_admin_account_overview(...)`;
- `public.get_admin_radar_overview(...)`;
- `service_role` only;
- no-leak output contract.

## 2. Target Architecture

### Admin Reads

Admin reads should move away from direct table reads and `select *`.

```text
Admin UI Next.js
  -> future admin-dashboard Edge/API
  -> RPC 1A and later safe RPCs
  -> safe projections / domain tables
```

The UI should receive explicit shapes. It should not receive password fields,
raw device internals, raw logs, raw metadata, XML, screenshot paths or service
role-only material.

### Admin Writes

Admin writes must be domain-specific and audited.

```text
Admin UI Next.js
  -> domain API / Edge Function
  -> role, entitlement and runtime-support validation
  -> audit log / dashboard action
  -> domain table
```

Examples:

- credentials -> `instagram-credentials` -> `account_credentials` / Vault;
- DM settings -> DM domain API -> `ig_account_dm_settings` and templates;
- dashboard stop/retry/pause -> `dashboard-actions` / audited action table;
- targets -> target API -> `ig_targets`.

### Client

The future client dashboard is separate.

```text
Client dashboard
  -> client-safe API only
  -> ownership-checked projections
  -> no direct legacy table access
```

The client must never read legacy settings tables directly and must never see
secrets, device internals, raw logs, raw metadata, raw XML, screenshot paths,
webhook URLs or service-role scoped data.

## 3. Legacy Tables vs Phone Farm Tables

| Table | Current role | Status | Risk | Future target | Recommended action |
|---|---|---|---|---|---|
| `ig_account_settings` | Monolithic UI settings store | legacy UI / db-only | Contains plaintext `password`, device internals and unverified toggles | Domain tables and safe projections | Deprecate as primary source; migrate field-by-field |
| `ig_account_filters` | UI filters store | legacy UI / db-only | May conflict with runtime follow private settings | Filter domain after runtime proof | Reconcile before client exposure |
| `ig_accounts` | UI account inventory and lifecycle | legacy operational table | `select *` can expose sensitive operational columns | RPC 1A / `client_instagram_accounts` / assignments | Read via admin API; keep legacy writes scoped |
| `ig_runs` | UI recent runs and stop mutation | partial runtime | Raw run rows may include operational fields | Runtime projection / Radar | Read sanitized; mutate via dashboard action |
| `ig_action_logs` | UI KPIs, logs, exports and stop log | partial runtime | Raw payload/metadata may leak XML, paths or secrets | Sanitized log projection | Redact and scope exports |
| `ig_account_templates` | UI reusable settings/filter templates | admin prototype | Can preserve unsafe legacy fields | Domain templates by feature | Keep admin-only; validate payload shape |
| `ig_devices` | UI setup device catalog | legacy admin prototype | Device internals | `phone_devices`, `phone_clones`, assignments | Move to ops-only device model |
| `client_instagram_accounts` | Client/account ownership and status axes | Phone Farm target | Safe if projected correctly | Manage/client-safe APIs | Use through RPC/API, not direct client reads |
| `account_credentials` | Credential status and encrypted refs | Phone Farm target | Secret metadata if exposed raw | `instagram-credentials` API + safe status | Never expose raw rows |
| `ig_account_dm_settings` | Runtime DM settings | Phone Farm runtime | Real-send and quota controls are dangerous | DM domain API | Map verified DM fields here |
| `ig_dm_templates` | Runtime DM template content | Phone Farm runtime | Content moderation/versioning needed | Template API | Use approved/versioned templates |
| `ig_account_unfollow_settings` | Runtime unfollow settings | Phone Farm runtime | Can cause destructive account actions | Unfollow domain API | Map verified unfollow fields only |
| `ig_account_follow_settings` | Runtime follow private-profile behavior | Phone Farm runtime | Semantic conflict with UI filters | Follow settings API | Reconcile before UI exposure |
| `ig_targets` | Runtime source/target accounts | Runtime verified | CT quality and bad source handling | Targets/CT API | Keep as source of truth for sources |
| `ig_interacted_users` | Interaction memory, FBR, DM state | Runtime verified | Can expose target/user history | Aggregated projections | Never expose raw client-side |
| `account_dashboard_actions` | Audited admin actions | Phone Farm target | Action replay / incorrect status if unvalidated | `dashboard-actions` Edge/API | Use for mutations and stop/pause/retry |
| `account_incidents` | Runtime/admin incident tracking | Phone Farm target | Raw incident metadata may leak internals | Radar/server-check projection | Use sanitized incident summaries |
| `runtime_events` | Runtime heartbeat/events | Phone Farm target | Raw events may reveal internals | Radar/server-check projection | Aggregate only |
| `phone_devices` | Physical phone inventory | Phone Farm target | Device internals | Ops-only APIs | Never client |
| `phone_clones` | Clone/runtime slot inventory | Phone Farm target | Clone internals and package state | Ops-only APIs | Never client |
| `account_assignments` | Account-to-device/clone assignments | Phone Farm target | Device assignment internals | Admin/ops assignment API | Safe display only via projection |

## 4. Settings Registry by UI Tab

Runtime status values:

- `runtime_verified`: current Python runtime consumes or produces this domain.
- `partial`: some backend/runtime support exists but the UI field is not wired
  cleanly.
- `db_only`: persisted in a UI table but not proven runtime-consumed.
- `legacy_unknown`: inherited UI setting with unclear runtime behavior.
- `planned`: target backend concept exists or is expected but not wired.
- `ops_only`: operational control that must stay out of client surfaces.

Visibility values:

- `admin`;
- `client_safe_later`;
- `ops_only`;
- `forbidden_client`.

Security classes:

- `safe`;
- `masked_only`;
- `secret_forbidden`;
- `device_internal`;
- `runtime_dangerous`.

Actions:

- `keep`;
- `map_to_RPC_1A`;
- `map_to_domain_table`;
- `needs_backend_api`;
- `hide_until_runtime_verified`;
- `move_to_ops_only`;
- `deprecate`;
- `forbidden_client`.

### 4.1 General

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Username | `username` | `ig_account_settings`, fallback `ig_accounts` | PATCH `/api/instagram-dashboard/settings` | RPC 1A `username`, `client_instagram_accounts.label` | db_only | admin | safe | map_to_RPC_1A |
| Display name | `display_name` | `ig_account_settings`, fallback `ig_accounts.display_name` | PATCH settings | `ig_accounts.display_name` / safe projection | db_only | admin | safe | keep |
| Device name | `device_name` | `ig_account_settings`, fallback `ig_accounts.device_name` | PATCH settings | `phone_devices` + `account_assignments` | planned | ops_only | device_internal | move_to_ops_only |
| Device UDID | `device_udid` | `ig_account_settings`, fallback `ig_accounts.device_udid` | PATCH settings | `phone_devices` ops projection only | legacy_unknown | forbidden_client | device_internal | forbidden_client |
| Email | `email` | `ig_account_settings`, fallback `ig_accounts.email` | PATCH settings | RPC 1A `email_display` | db_only | admin | masked_only | map_to_RPC_1A |
| Password | `password` | `ig_account_settings.password` | PATCH settings | `account_credentials` + `instagram-credentials` + Vault | planned | forbidden_client | secret_forbidden | forbidden_client |
| Two-factor enabled | `two_fa_enabled` | `ig_account_settings` | PATCH settings | RPC 1A `two_factor_display`, `login_status=needs_2fa` | db_only | admin | safe | map_to_RPC_1A |
| App package | `app_package` | `ig_account_settings` | PATCH settings | `phone_clones.package_name` / clone assignment | planned | ops_only | device_internal | move_to_ops_only |
| Cloned app mode | `cloned_app_mode` | `ig_account_settings` | PATCH settings | `phone_clones`, `account_assignments`, legacy `ig_accounts.clone_mode` | planned | ops_only | device_internal | move_to_ops_only |
| Account status | `account_status` | `ig_account_settings`, legacy `ig_accounts.status` | PATCH settings / lifecycle route | Split into `admin_status`, `login_status`, `provisioning_status`, `subscription_status`, `automation_health` | db_only | admin | safe | deprecate |
| Campaign name | `campaign_name` | `ig_account_settings`, fallback `ig_targets` | PATCH settings | Future campaign/dashboard action model | legacy_unknown | admin | safe | needs_backend_api |
| Save as Template | UI action | `ig_account_templates` | POST `/api/instagram-dashboard/templates` | Admin-only settings templates | db_only | admin | safe | keep |
| Apply Template | UI action | `ig_account_templates` | PATCH `/api/instagram-dashboard/templates/apply` | Validated domain template application | db_only | admin | safe | needs_backend_api |

### 4.2 Schedule

As of checkpoint 2026-06-01, the Schedule tab is no longer a legacy
`ig_account_settings` promise. It is wired through the Schedule domain API and
validated against `phone_app_instances`, `account_assignments`, phone rest and
runtime gates.

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Slot selection | `starts_at` / `ends_at` | `list_available_assignment_slots` / `account_assignments` | `/api/instagram-dashboard/settings/schedule` | `account_assignments` + `phone_app_instances` | runtime_verified | admin | safe | keep |
| Runtime profile | `assignment_type` | `resolve_account_schedule_assignment_type` | read-only projection | subscription/package resolver | runtime_verified | admin | safe | keep |
| Slot kind | `slot_kind` | Schedule RPC projection | read-only projection | `full_cycle_6h` / `outreach_short` | runtime_verified | admin | safe | keep |
| App instances summary | `app_instance_availability` | `phone_app_instances` summary | read-only projection | assignable app inventory | runtime_verified | admin | ops_summary | keep |
| Total sessions | `total_sessions` | `ig_account_settings` | PATCH settings | Session policy / package limits | planned | admin | safe | map_to_domain_table |
| Stop after minutes | `stop_interactions_after_minutes` | `ig_account_settings` | PATCH settings | Session cap / package limit | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Startup timeout seconds | `timeout_startup_seconds` | `ig_account_settings` | PATCH settings | Provisioner/app-start ops config | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |
| Pause account days | `pause_account_days` | `ig_account_settings` | PATCH settings | `account_dashboard_actions` pause request | planned | admin | safe | needs_backend_api |
| Pause account until | `pause_account_until` | `ig_account_settings` | PATCH settings | `account_dashboard_actions` pause request | planned | admin | safe | needs_backend_api |
| Randomize start | `randomize_start_enabled` | `ig_account_settings` | PATCH settings | Scheduler jitter | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Speed multiplier | `speed_multiplier` | `ig_account_settings` | PATCH settings | Runtime pacing caps | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |

Schedule settings must account for one-device-one-active-UI-action, long
sessions, clone buffers, phone rest windows and package runtime limits before
they are exposed outside admin.

Validated behavior:

- `full_cycle` accounts show four `full_cycle_6h` slots and no outreach slots;
- app instance capacity counts inventory rows once;
- a Schedule slot change reuses an instance already occupied by the same
  account before choosing a free clone;
- `no_app_instance_available` blocks only when no compatible free/reusable
  instance exists.

### 4.3 Actions

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Follow enabled | `follow_enabled` | `ig_account_settings` | PATCH settings | Package follow settings / follow runtime | legacy_unknown | admin | runtime_dangerous | hide_until_runtime_verified |
| Follow limit | `follow_limit` | `ig_account_settings` | PATCH settings | Per-session package cap | legacy_unknown | admin | safe | map_to_domain_table |
| Total follows limit | `total_follows_limit` | `ig_account_settings` | PATCH settings | Daily package cap, `min(db_setting, env_hard_cap)` | legacy_unknown | admin | safe | map_to_domain_table |
| Follow percentage | `follow_percentage` | `ig_account_settings` | PATCH settings | Sampling policy if retained | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Unfollow enabled | `unfollow_enabled` | `ig_account_settings` | PATCH settings | `ig_account_unfollow_settings.unfollow_enabled` | partial | admin | runtime_dangerous | map_to_domain_table |
| Total unfollows limit | `total_unfollows_limit` | `ig_account_settings` | PATCH settings | `ig_account_unfollow_settings.unfollow_per_day_limit` | partial | admin | safe | map_to_domain_table |
| Unfollow delay days | `unfollow_delay_days` | `ig_account_settings` | PATCH settings | `ig_account_unfollow_settings.unfollow_after_days` | runtime_verified | admin | safe | map_to_domain_table |
| Like enabled | `like_enabled` | `ig_account_settings` | PATCH settings | Post-follow likes runtime | legacy_unknown | admin | runtime_dangerous | hide_until_runtime_verified |
| Total likes limit | `total_likes_limit` | `ig_account_settings` | PATCH settings | Package cap, env hard cap | legacy_unknown | admin | safe | map_to_domain_table |
| Likes per follow min/max | `likes_per_follow_min`, `likes_per_follow_max` | `ig_account_settings` | PATCH settings | Post-follow likes policy | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Likes percentage | `likes_percentage` | `ig_account_settings` | PATCH settings | Sampling policy if retained | legacy_unknown | admin | safe | deprecate |
| Story watch enabled | `story_watch_enabled` | `ig_account_settings` | PATCH settings | Story runtime module | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Watch photo/video time min/max | `watch_photo_time_min`, `watch_photo_time_max`, `watch_video_time_min`, `watch_video_time_max` | `ig_account_settings` | PATCH settings | Runtime timing policy | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |

All action limits must eventually expose an effective value:
`effective_value = min(db_setting, env_hard_cap, package_cap)`.

### 4.4 DM

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Welcome DM enabled | `welcome_dm_enabled` | `ig_account_settings` | PATCH settings | `ig_account_dm_settings.welcome_enabled` | runtime_verified | admin | runtime_dangerous | map_to_domain_table |
| Welcome DM message | `welcome_dm_message` | `ig_account_settings` | PATCH settings | `ig_dm_templates` via `welcome_template_id` | runtime_verified | admin | safe | needs_backend_api |
| Cold DM enabled | `cold_dm_enabled` | `ig_account_settings` | PATCH settings | `ig_account_dm_settings.outreach_enabled` | runtime_verified | admin | runtime_dangerous | map_to_domain_table |
| Cold DM message | `cold_dm_message` | `ig_account_settings` | PATCH settings | `ig_dm_templates` via `default_outreach_template_id` | runtime_verified | admin | safe | needs_backend_api |
| Max DMs per run | `max_dm_per_run` | `ig_account_settings` | PATCH settings | `welcome_per_session_limit`, `outreach_per_session_limit` | partial | admin | safe | map_to_domain_table |
| Max consecutive DMs | `max_consecutive_dms` | `ig_account_settings` | PATCH settings | Sender pacing / safety policy | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Check chat before welcoming | `check_chat_before_welcoming` | `ig_account_settings` | PATCH settings | `ig_account_dm_settings.check_chat_before_welcome` | runtime_verified | admin | safe | map_to_domain_table |
| Send enabled | `send_enabled` | `ig_account_settings` | PATCH settings | Real-send gate / environment-controlled sender | runtime_verified | ops_only | runtime_dangerous | move_to_ops_only |
| Safe review mode | `safe_review_mode` | `ig_account_settings` | PATCH settings | Review/dry-run workflow | db_only | admin | safe | needs_backend_api |

DM content must move from inline settings fields to approved, versioned
templates with moderation/audit history and entitlement checks.

### 4.5 Followback

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Followback on followers | `followback_on_followers` | `ig_account_settings` | PATCH settings | FBR / dynamic follow-back logic | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Max followback skips | `max_followback_skips` | `ig_account_settings` | PATCH settings | DM/followback skip policy | partial | admin | safe | map_to_domain_table |
| Max followback ignore | `max_followback_ignore` | `ig_account_settings` | PATCH settings | Followback ignore policy | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Sort followers mode | `sort_followers_mode` | `ig_account_settings` | PATCH settings | Followback/unfollow sort policy | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Unfollow non-followers | `unfollow_non_followers` | `ig_account_settings` | PATCH settings | `ig_account_unfollow_settings.unfollow_mode` | runtime_verified | admin | runtime_dangerous | map_to_domain_table |
| Unfollow any | `unfollow_any` | `ig_account_settings` | PATCH settings | `ig_account_unfollow_settings.unfollow_mode` | runtime_verified | admin | runtime_dangerous | map_to_domain_table |
| Unfollow skip limit | `unfollow_skip_limit` | `ig_account_settings` | PATCH settings | Unfollow session/day limits | partial | admin | safe | map_to_domain_table |
| Mute posts after follow | `mute_posts_after_follow` | `ig_account_settings` | PATCH settings | Post-follow mute runtime | partial | ops_only | runtime_dangerous | move_to_ops_only |
| Mute stories after follow | `mute_stories_after_follow` | `ig_account_settings` | PATCH settings | Post-follow mute runtime | partial | ops_only | runtime_dangerous | move_to_ops_only |
| Do follows first | `do_follows_first` | `ig_account_settings` | PATCH settings | Session ordering policy | legacy_unknown | admin | safe | hide_until_runtime_verified |

Feature #13 dynamic follow-back checks, auto restart and quota resume should be
documented separately before this tab is exposed broadly.

### 4.6 Sources

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Source accounts | `source_accounts` | `ig_account_settings` textarea | PATCH settings | `ig_targets.target_username` | legacy_unknown | admin | safe | deprecate |
| Max follows per target per run | `max_follows_per_target_per_run` | `account_follow_source_settings` | PATCH `/api/instagram-dashboard/settings/follow-sources` | Worker P1b target budget | runtime_verified | admin | safe | keep |
| Max targets per run | `max_targets_per_run` | `account_follow_source_settings` | PATCH `/api/instagram-dashboard/settings/follow-sources` | Worker P1b target rotation bound | runtime_verified | admin | safe | keep |
| Truncate sources min/max | `truncate_sources_min`, `truncate_sources_max` | `ig_account_settings` | PATCH settings | Target queue policy | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Delete interacted users | `delete_interacted_users` | `ig_account_settings` | PATCH settings | `ig_interacted_users` maintenance | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |
| Change source if crash | `change_source_if_crash` | `ig_account_settings` | PATCH settings | Recovery / source rotation | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |
| Skipped posts limit | `skipped_posts_limit` | `ig_account_settings` | PATCH settings | Source Quality Control | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Fling when skipped | `fling_when_skipped` | `ig_account_settings` | PATCH settings | UI navigation heuristic | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |

`source_accounts` is not the Phone Farm source of truth. The runtime source of
truth is `ig_targets`, later connected to CT / Target Accounts, FBR and Source
Quality Control. P1b source rotation settings are domain settings stored per
account in `account_follow_source_settings`; they are per-run controls, not
daily limits, and do not increase global Follow caps. Defaults remain
conservative (2 follows per target, 3 targets per run) until controlled
multi-target tests justify production tuning.

Checkpoint 2026-06-02:

- worker `93717ecc3b7828a01a00ca1ee8c81d077a821109` on
  `stable-follow-working-state`;
- frontend `b9f5fb4c23b745b084bedc831e69e892598e4f60` on `main`;
- migrations remote applied and local history aligned:
  `20260601224833_follow_source_rotation_settings.sql` and
  `20260601224935_follow_source_rotation_settings_revoke_public_grants.sql`;
- old local fused migration `20260601222500_follow_source_rotation_settings.sql`
  removed from the commit scope;
- API smoke on `cinema_catchup`: GET final returned `2 / 3` with
  `save_ready=true`; PATCH `3 / 3` then restore `2 / 3` succeeded; invalid
  values `0`, `51` and `11` were rejected without silent clamp;
- audit event `follow_source_rotation_settings_saved` was created with safe
  summaries only;
- visual UI smoke remains pending because the local browser redirects to
  `restaurant-login`; API/code contract is validated.

Runtime warnings:

- no real run or real follow was launched for this checkpoint;
- no 30/4 production tuning was applied;
- target metrics, `follows_sent`, followbacks, FBR, durable `last_used_at` and
  cooldown are still P1c/P2 work;
- multi-target rotation requires controlled P2 runs before being called
  production runtime-ready.

### 4.7 Filters

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Disable filters | `disable_filters` | `ig_account_filters` | PATCH `/api/instagram-dashboard/filters` | Filter domain policy | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Skip followers | `skip_followers` | `ig_account_filters` | PATCH filters | Target filter policy | legacy_unknown | client_safe_later | safe | hide_until_runtime_verified |
| Skip following | `skip_following` | `ig_account_filters` | PATCH filters | Target filter policy | legacy_unknown | client_safe_later | safe | hide_until_runtime_verified |
| Skip business profiles | `skip_business_profiles` | `ig_account_filters` | PATCH filters | Profile filter policy | legacy_unknown | client_safe_later | safe | hide_until_runtime_verified |
| Skip non-business profiles | `skip_non_business_profiles` | `ig_account_filters` | PATCH filters | Profile filter policy | legacy_unknown | client_safe_later | safe | hide_until_runtime_verified |
| Follow private profiles | `follow_private_profiles` | `ig_account_filters` | PATCH filters | `ig_account_follow_settings.dont_follow_private_accounts` inverted semantic | partial | client_safe_later | safe | needs_backend_api |
| Follow only private profiles | `follow_only_private_profiles` | `ig_account_filters` | PATCH filters | Filter domain if supported | legacy_unknown | client_safe_later | safe | hide_until_runtime_verified |
| DM private profiles | `dm_private_profiles` | `ig_account_filters` | PATCH filters | DM eligibility policy | legacy_unknown | client_safe_later | safe | hide_until_runtime_verified |
| Minimum/maximum followers | `min_followers`, `max_followers` | `ig_account_filters` | PATCH filters | Growth filters / entitlement | legacy_unknown | client_safe_later | safe | hide_until_runtime_verified |
| Minimum/maximum following | `min_following`, `max_following` | `ig_account_filters` | PATCH filters | Growth filters / entitlement | legacy_unknown | client_safe_later | safe | hide_until_runtime_verified |
| Minimum posts | `min_posts` | `ig_account_filters` | PATCH filters | Growth filters / entitlement | legacy_unknown | client_safe_later | safe | hide_until_runtime_verified |
| Blacklisted words | `blacklisted_words` | `ig_account_filters` | PATCH filters | Moderation/filter templates | db_only | admin | safe | needs_backend_api |
| Mandatory words | `mandatory_words` | `ig_account_filters` | PATCH filters | Moderation/filter templates | db_only | admin | safe | needs_backend_api |
| Whitelist words | `whitelist_words` | `ig_account_filters` | PATCH filters | Moderation/filter templates | db_only | admin | safe | needs_backend_api |
| Blacklist accounts | `blacklist_accounts` | `ig_account_filters` | PATCH filters | Target exclusion list | db_only | admin | safe | needs_backend_api |

Filters are candidates for future client Growth Settings, but only after
runtime proof and entitlement validation.

### 4.8 Safety

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Dry run enabled | `dry_run_enabled` | `ig_account_settings` | PATCH settings | Runtime dry-run gates / env gates | runtime_verified | ops_only | runtime_dangerous | move_to_ops_only |
| Total interactions limit | `total_interactions_limit` | `ig_account_settings` | PATCH settings | Package/runtime cap | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Total successful interactions limit | `total_successful_interactions_limit` | `ig_account_settings` | PATCH settings | Package/runtime cap | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Interactions count | `interactions_count` | `ig_account_settings` | PATCH settings | Runtime counters / logs projection | db_only | admin | safe | deprecate |
| Interact percentage | `interact_percentage` | `ig_account_settings` | PATCH settings | Sampling policy if retained | legacy_unknown | admin | safe | deprecate |
| End if follow limit reached | `end_if_follow_limit_reached` | `ig_account_settings` | PATCH settings | Runtime stop rule | legacy_unknown | admin | safe | hide_until_runtime_verified |
| End if DM limit reached | `end_if_dm_limit_reached` | `ig_account_settings` | PATCH settings | Runtime stop rule | legacy_unknown | admin | safe | hide_until_runtime_verified |
| End if likes limit reached | `end_if_likes_limit_reached` | `ig_account_settings` | PATCH settings | Runtime stop rule | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Max actions per hour/day | `max_actions_per_hour`, `max_actions_per_day` | `ig_account_settings` | PATCH settings | Package caps + env hard caps | planned | admin | safe | needs_backend_api |
| Random delay min/max seconds | `random_delay_min_seconds`, `random_delay_max_seconds` | `ig_account_settings` | PATCH settings | DM settings and runtime pacing | partial | admin | safe | map_to_domain_table |
| Random pause every actions | `random_pause_every_actions` | `ig_account_settings` | PATCH settings | Runtime pacing | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |
| Long break after interactions | `long_break_after_interactions` | `ig_account_settings` | PATCH settings | Runtime pacing | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |
| Long break min/max minutes | `long_break_min_minutes`, `long_break_max_minutes` | `ig_account_settings` | PATCH settings | Runtime pacing | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |
| Warmup mode | `warmup_mode` | `ig_account_settings` | PATCH settings | Warmup policy | legacy_unknown | admin | safe | hide_until_runtime_verified |
| Stop on suspicious/login/checkpoint/nav failure | `stop_on_suspicious_screen`, `stop_on_login_challenge`, `stop_on_checkpoint`, `stop_on_repeated_navigation_failure` | `ig_account_settings` | PATCH settings | Incidents / recovery policies | planned | admin | safe | needs_backend_api |
| Max repeated errors | `max_repeated_errors` | `ig_account_settings` | PATCH settings | Recovery engine policy | legacy_unknown | ops_only | runtime_dangerous | move_to_ops_only |

### 4.9 Device

The Device tab is ops-only and never client-safe.

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Disable block detection | `disable_block_detection` | `ig_account_settings` | PATCH settings | Device/runtime safety policy | ops_only | ops_only | runtime_dangerous | move_to_ops_only |
| Relog after block | `relog_after_block` | `ig_account_settings` | PATCH settings | Recovery engine | ops_only | ops_only | runtime_dangerous | move_to_ops_only |
| Relog delay seconds | `relog_delay_seconds` | `ig_account_settings` | PATCH settings | Recovery engine | ops_only | ops_only | runtime_dangerous | move_to_ops_only |
| Rotate IP | `rotate_ip` | `ig_account_settings` | PATCH settings | Future network/device layer | ops_only | ops_only | runtime_dangerous | move_to_ops_only |
| Restart UIAutomator2 | `restart_uiautomator2` | `ig_account_settings` | PATCH settings | Device Runtime Control / ATX | ops_only | ops_only | runtime_dangerous | move_to_ops_only |
| Close apps | `close_apps` | `ig_account_settings` | PATCH settings | Device Runtime Control | ops_only | ops_only | runtime_dangerous | move_to_ops_only |
| Close apps device | `close_apps_device` | `ig_account_settings` | PATCH settings | Device Runtime Control | ops_only | ops_only | runtime_dangerous | move_to_ops_only |
| Log out all before session | `log_out_all_before_session` | `ig_account_settings` | PATCH settings | Account/device ops action | ops_only | ops_only | runtime_dangerous | move_to_ops_only |
| Screen sleep | `screen_sleep` | `ig_account_settings` | PATCH settings | Device Runtime Control | ops_only | ops_only | device_internal | move_to_ops_only |
| Screen record | `screen_record` | `ig_account_settings` | PATCH settings | Debug/recording ops | ops_only | ops_only | device_internal | move_to_ops_only |
| Debug mode | `debug_mode` | `ig_account_settings` | PATCH settings | Ops debug mode | ops_only | ops_only | runtime_dangerous | move_to_ops_only |
| Total crashes limit | `total_crashes_limit` | `ig_account_settings` | PATCH settings | Recovery threshold | ops_only | ops_only | runtime_dangerous | move_to_ops_only |

Any future real device action requires admin confirmation, audit logging and a
runtime support check.

### 4.10 Advanced

| UI field | Current key | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Current run status | `current_run_status` | `ig_account_settings`, separate `ig_runs.status` exists | PATCH settings | `ig_runs`, `runtime_events`, Radar projection | partial | admin | safe | map_to_domain_table |
| Last error | `last_error` | `ig_account_settings` | PATCH settings | `account_incidents` sanitized summary | partial | admin | safe | needs_backend_api |
| Last successful action | `last_successful_action` | `ig_account_settings` | PATCH settings | `ig_action_logs` aggregate | partial | admin | safe | map_to_domain_table |
| Manual stop requested | `manual_stop_requested` | `ig_account_settings`, separate stop route exists | PATCH settings | `account_dashboard_actions` / `dashboard-actions` | partial | admin | runtime_dangerous | needs_backend_api |

### 4.11 Outside Settings

| Surface | Current fields/actions | Current table/source | Current write path | Target Phone Farm source | Runtime status | Visibility | Security class | Action |
|---|---|---|---|---|---|---|---|---|
| Account list | username, display, status, device, campaign, last run, totals, created/lifecycle dates | `ig_accounts`, `ig_account_settings`, `ig_runs`, `ig_action_logs`, `ig_targets` | none in list | RPC 1A `get_admin_account_overview` and later summaries | partial | admin | masked_only | map_to_RPC_1A |
| KPI cards | active count, DMs, stories, follows | direct table/log aggregation | none | RPC/API aggregation | partial | admin | safe | needs_backend_api |
| Lifecycle tabs | Active, Archives, Trash | `ig_accounts.status`, lifecycle columns | POST lifecycle route updates `ig_accounts` | Dashboard action + audited lifecycle API | db_only | admin | runtime_dangerous | needs_backend_api |
| Statistics drawer | runs, counts, performance metrics | `ig_runs`, `ig_action_logs` | export only | Runtime/Radar safe projection | partial | admin | masked_only | needs_backend_api |
| Logs drawer | logs, metadata, payload, exports | `ig_action_logs` | export/copy only | Sanitized logs API | partial | ops_only | runtime_dangerous | move_to_ops_only |
| Add Profile wizard | username, password, email, device, clone, template | `ig_accounts`, settings, filters, templates, devices | POST create account and settings/filter inserts | `client_instagram_accounts`, `account_credentials`, assignments, templates | planned | admin | secret_forbidden | needs_backend_api |
| Targets panel | target usernames, status, source | `ig_targets` | GET/POST/reset targets | `ig_targets`, CT sync, Source Quality Control | runtime_verified | admin | safe | keep |
| Run manually | button placeholder | none | console log only | `account_dashboard_actions` / enqueue API | planned | admin | runtime_dangerous | needs_backend_api |
| Stop run | active run status update + log insert | `ig_runs`, `ig_action_logs` | POST stop route | `dashboard-actions` + worker acknowledgement | partial | admin | runtime_dangerous | needs_backend_api |

## 5. Critical Security Points

- `password` in `ig_account_settings` is plaintext legacy state. It must be
  deprecated and never used as the future credential source.
- Credentials must flow through `account_credentials`, `instagram-credentials`
  and Vault-backed secret storage.
- `device_udid` is never client-safe.
- Durable dashboard reads must not use `select *` on logs, runs or accounts.
- Log exports and any `payload` / `metadata` output must be redacted.
- The current `Run manually` button is a placeholder. It must not claim to
  start a worker until connected to an audited enqueue/action flow.
- Monolithic `account_status` must be replaced by separate axes:
  `admin_status`, `login_status`, `provisioning_status`,
  `subscription_status` and `automation_health`.
- Service role, webhook URLs, tokens, Vault ids, `secret_ref`, XML, screenshot
  paths, raw metadata and device internals must never reach client surfaces.

## 6. Client / Admin / Ops Classification

### Admin-only

- Most Settings fields until runtime support is proven.
- Account lifecycle controls.
- Admin templates.
- Safe statistics and logs summaries.
- Add Profile wizard.
- Admin Manage and Radar surfaces.

### Client-safe later

Only after runtime proof, entitlement checks and safe API projection:

- approved DM templates;
- CT / Targets safe summaries;
- selected Growth filter settings;
- selected account health messages;
- selected package/limit displays.

### Ops-only

- Entire Device tab.
- `device_udid`.
- `send_enabled`.
- `dry_run_enabled`.
- `debug_mode`.
- `screen_record`.
- `rotate_ip`.
- `restart_uiautomator2`.
- source crash rotation.
- delete interacted users.
- raw log export and advanced runtime diagnostics.

### Forbidden client

- password;
- `secret_ref`;
- Vault id;
- raw logs;
- raw metadata;
- raw XML;
- screenshot path;
- device internals;
- service role;
- webhook URLs;
- tokens or Authorization headers.

### Hide until runtime verified

- follow / like / story percentages.
- most Followback and Safety toggles.
- legacy `source_accounts` textarea.
- any setting still only persisted in `ig_account_settings` without Python
  runtime consumption.

## 7. Migration Order for UI / Backend

### A. Manage / Radar List Reads

Replace direct `select *` reads with RPC 1A through a future
admin-dashboard API:

- `get_admin_account_overview`;
- `get_admin_radar_overview`.

This step should not mutate settings or device controls.

### B. Credentials

- Remove future dependence on `ig_account_settings.password`.
- Use `instagram-credentials` for credential writes.
- Expose only safe credential status: configured, missing, reauth required,
  needs 2FA, checkpoint, failed.

### C. DM

- Map enabled flags and limits to `ig_account_dm_settings`.
- Move messages to `ig_dm_templates`.
- Add content moderation, audit, versioning and entitlement checks.
- Keep real-send gates ops-controlled.

### D. Targets / CT

- Use `ig_targets` as the source of truth.
- Deprecate legacy `source_accounts` textarea.
- Prepare CT sync, FBR metrics and Source Quality Control later.

### E. Unfollow / Follow

- Map verified unfollow fields to `ig_account_unfollow_settings`.
- Map private-profile behavior to `ig_account_follow_settings`.
- Expose only runtime-verified settings.
- Resolve semantic conflicts with `ig_account_filters` before UI exposure.

### F. Filters

- Reconcile `ig_account_filters` with runtime behavior.
- Expose selected client Growth Settings only after runtime proof and
  entitlement checks.

### G. Safety / Device

- Keep admin/ops only.
- Add confirmation and audit requirements.
- Never expose to client.
- Never execute device actions directly from raw UI toggles.

## 8. Link with Dashboard Foundation 1A

Dashboard Foundation 1A already supplies:

- `public.get_admin_account_overview(...)`;
- `public.get_admin_radar_overview(...)`.

These RPCs should feed:

- Admin Manage;
- Admin Radar;
- Server Check;
- future BotApp / Mac app admin surfaces;
- later client-safe projections through a dedicated API.

1A is read-only, admin/backend-safe and no-leak. It does not authorize direct
client reads, settings writes, device control or runtime actions.

## 9. Link with BotApp / Propulse / Features V2

- BotApp is a reference for future API/Mac app concepts. It is not the current
  source of truth.
- Propulse is a UX reference for Manage/Radar/status dropdown behavior. Do not
  copy-paste it as backend truth.
- Instagram Bot Features V2 is a reference for ops/features and must be
  integrated progressively.
- Phone Farm Supabase, Edge, Vault and Python runtime remain the source of
  truth.

## 10. Recommended Next Step

The recommended next step is:

```text
DF-1B - admin-dashboard Edge/API over RPC 1A
```

DF-1B should:

- expose read-only admin Manage/Radar endpoints over RPC 1A;
- keep the no-leak contract;
- avoid direct UI `select *`;
- preserve service-role isolation server-side.

DF-1B implementation note:

- Edge Function: `supabase/functions/admin-dashboard/index.ts`;
- actions: `health`, `manage_overview`, `radar_overview`;
- auth: `Authorization: Bearer <ADMIN_DASHBOARD_INTERNAL_API_TOKEN>`;
- `manage_overview` calls `get_admin_account_overview`;
- `radar_overview` calls `get_admin_radar_overview`;
- no UI consumption, settings mutation, client JWT, device control or deploy is
  included without a later explicit GO.

Future UI note: the current Next.js `/instagram-dashboard` is mostly one large
admin page with drawers and row controls. DF-1B must not assume this remains the
long-term shape. The API and follow-up docs should be able to feed separate
admin views over time: Admin Manage, Admin Radar / Server Check, Account Detail,
Settings / Growth Settings, Devices / Phones, Activity Log, Target Accounts /
CT, DM Templates, and Credentials / Dashboard Actions.

DF-1B should not yet:

- mutate settings;
- touch device controls;
- activate Source Quality Control;
- expose a client dashboard;
- connect the full Settings drawer;
- enqueue or stop real workers except through a later audited action flow.
