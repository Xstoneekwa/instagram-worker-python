-- Entry 2D-4A — schema-only dashboard action center.
--
-- This table is the future UI action/task model for client/admin/assistant
-- dashboards. It is not a Slack/Discord delivery audit table and must never
-- store secrets, passwords, secret refs, webhook URLs, or Vault payloads.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Dashboard action center
-- =============================================================================
create table if not exists public.account_dashboard_actions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  account_id uuid not null references public.ig_accounts (id) on delete cascade,
  client_id uuid references public.clients (id) on delete set null,
  incident_id uuid references public.account_incidents (id) on delete set null,

  action_type text not null,
  status text not null default 'pending',
  severity text not null default 'warning',
  audience text not null default 'client',

  requires_client_action boolean not null default true,
  blocking_campaign boolean not null default false,

  title text not null,
  safe_client_message text,
  assistant_message text,
  admin_message text,
  action_label text,
  action_deep_link text,
  dedupe_key text not null,

  resolved_at timestamptz,
  dismissed_at timestamptz,
  acknowledged_at timestamptz,
  metadata jsonb not null default '{}'::jsonb,

  constraint account_dashboard_actions_status_check
    check (status in (
      'pending',
      'acknowledged',
      'pending_verification',
      'resolved',
      'dismissed',
      'ignored'
    )),
  constraint account_dashboard_actions_severity_check
    check (severity in ('info', 'warning', 'error', 'critical')),
  constraint account_dashboard_actions_audience_check
    check (audience in ('client', 'admin', 'assistant', 'ops')),
  constraint account_dashboard_actions_metadata_object_check
    check (jsonb_typeof(metadata) = 'object'),
  constraint account_dashboard_actions_action_type_nonempty
    check (char_length(trim(action_type)) > 0),
  constraint account_dashboard_actions_dedupe_key_nonempty
    check (char_length(trim(dedupe_key)) > 0),
  constraint account_dashboard_actions_title_nonempty
    check (char_length(trim(title)) > 0)
);

-- =============================================================================
-- 2. Indexes
-- =============================================================================
create index if not exists account_dashboard_actions_client_status_audience_idx
  on public.account_dashboard_actions (client_id, status, audience)
  where client_id is not null;

create index if not exists account_dashboard_actions_account_status_idx
  on public.account_dashboard_actions (account_id, status);

create index if not exists account_dashboard_actions_incident_id_idx
  on public.account_dashboard_actions (incident_id)
  where incident_id is not null;

create index if not exists account_dashboard_actions_action_type_status_idx
  on public.account_dashboard_actions (action_type, status);

create index if not exists account_dashboard_actions_blocking_campaign_idx
  on public.account_dashboard_actions (blocking_campaign)
  where blocking_campaign = true;

create index if not exists account_dashboard_actions_requires_client_action_idx
  on public.account_dashboard_actions (requires_client_action)
  where requires_client_action = true;

create index if not exists account_dashboard_actions_created_at_idx
  on public.account_dashboard_actions (created_at desc);

create unique index if not exists account_dashboard_actions_active_dedupe_key
  on public.account_dashboard_actions (dedupe_key)
  where status in ('pending', 'acknowledged', 'pending_verification');

-- =============================================================================
-- 3. updated_at trigger
-- =============================================================================
drop trigger if exists account_dashboard_actions_set_updated_at
  on public.account_dashboard_actions;
create trigger account_dashboard_actions_set_updated_at
  before update on public.account_dashboard_actions
  for each row execute function public.set_updated_at();

-- =============================================================================
-- 4. RLS and grants
-- =============================================================================
alter table public.account_dashboard_actions enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'account_dashboard_actions'
      and policyname = 'account_dashboard_actions_service_role_all'
  ) then
    create policy account_dashboard_actions_service_role_all
      on public.account_dashboard_actions
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

revoke all on public.account_dashboard_actions from public;
revoke all on public.account_dashboard_actions from anon;
revoke all on public.account_dashboard_actions from authenticated;

grant all on public.account_dashboard_actions to service_role;

-- =============================================================================
-- 5. Documentation comments
-- =============================================================================
comment on table public.account_dashboard_actions is
  'Entry 2D-4A dashboard action center. UI task/action state for client/admin/assistant dashboards; not Slack/Discord delivery audit.';

comment on column public.account_dashboard_actions.incident_id is
  'Optional link to account_incidents. Incidents remain the operational source; dashboard actions are the UI task projection.';

comment on column public.account_dashboard_actions.action_type is
  'Stable action type such as update_instagram_password, complete_two_factor, add_targets, or review_dm_template.';

comment on column public.account_dashboard_actions.status is
  'Dashboard action lifecycle: pending, acknowledged, pending_verification, resolved, dismissed, or ignored.';

comment on column public.account_dashboard_actions.audience is
  'Primary audience for the action: client, admin, assistant, or ops. Future safe APIs must filter fields by audience.';

comment on column public.account_dashboard_actions.requires_client_action is
  'True when the client must perform an action, e.g. update password, submit credentials, complete 2FA, or add targets.';

comment on column public.account_dashboard_actions.blocking_campaign is
  'True when the action should block or pause campaign activity and may require a priority dashboard modal.';

comment on column public.account_dashboard_actions.action_deep_link is
  'Safe dashboard app route to the correct form or panel. Never store external secret URLs, webhook URLs, or Vault references here.';

comment on column public.account_dashboard_actions.metadata is
  'Safe metadata only. Never store passwords, raw secrets, secret_ref, Vault payloads, webhook URLs, service-role data, raw XML, screenshots, tokens, or cookies.';
