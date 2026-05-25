-- ORF-4A — account incident notification delivery audit schema-only.
--
-- Delivery tracking foundation for future Slack/Discord incident notification
-- dispatchers. This migration intentionally does not add Python dispatchers,
-- webhook sends, runtime flow integration, Edge Functions, Redis, dashboard
-- views/RPC clients, credentials/provisioning, or device/session logic.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Account incident notification delivery audit
-- =============================================================================
create table if not exists public.account_incident_notifications (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  incident_id uuid not null references public.account_incidents (id) on delete cascade,
  channel text not null,
  status text not null default 'pending',
  target text,
  delivery_key text not null,

  attempt_count integer not null default 0,
  last_attempt_at timestamptz,
  delivered_at timestamptz,
  last_error text,
  response_status integer,
  response_body_preview text,

  payload jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,

  constraint account_incident_notifications_channel_check
    check (channel in ('slack', 'discord')),
  constraint account_incident_notifications_status_check
    check (status in ('pending', 'sent', 'failed', 'skipped')),
  constraint account_incident_notifications_attempt_count_check
    check (attempt_count >= 0),
  constraint account_incident_notifications_delivery_key_nonempty
    check (char_length(trim(delivery_key)) > 0),
  constraint account_incident_notifications_payload_object_check
    check (jsonb_typeof(payload) = 'object'),
  constraint account_incident_notifications_metadata_object_check
    check (jsonb_typeof(metadata) = 'object'),
  constraint account_incident_notifications_delivery_key_unique
    unique (delivery_key)
);

create index if not exists account_incident_notifications_incident_id_idx
  on public.account_incident_notifications (incident_id);

create index if not exists account_incident_notifications_channel_status_idx
  on public.account_incident_notifications (channel, status);

create index if not exists account_incident_notifications_status_created_at_idx
  on public.account_incident_notifications (status, created_at desc);

create index if not exists account_incident_notifications_last_attempt_at_idx
  on public.account_incident_notifications (last_attempt_at desc);

create index if not exists account_incident_notifications_delivered_at_idx
  on public.account_incident_notifications (delivered_at desc);

create index if not exists account_incident_notifications_delivery_key_idx
  on public.account_incident_notifications (delivery_key);

drop trigger if exists account_incident_notifications_set_updated_at
  on public.account_incident_notifications;
create trigger account_incident_notifications_set_updated_at
  before update on public.account_incident_notifications
  for each row execute function public.set_updated_at();

comment on table public.account_incident_notifications is
  'ORF-4A delivery audit table for future account incident Slack/Discord notifications. Service-role only; runtime flows must not call webhooks directly.';

comment on column public.account_incident_notifications.incident_id is
  'Source account_incidents row. Notifications consume durable incidents, not worker logs or runtime flows.';

comment on column public.account_incident_notifications.delivery_key is
  'Stable notification dedupe key. ORF-4 V1 pattern: {channel}:{incident_id}:opened.';

comment on column public.account_incident_notifications.payload is
  'Sanitized outbound payload audit. Never store webhook URLs, credentials, tokens, cookies, raw XML, raw stack traces, device_udid, or service-role details.';

comment on column public.account_incident_notifications.metadata is
  'Dispatcher-only structured metadata. Keep service-role only and do not expose directly to client dashboards.';

-- =============================================================================
-- 2. RLS and grants
-- =============================================================================
alter table public.account_incident_notifications enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'account_incident_notifications'
      and policyname = 'account_incident_notifications_service_role_all'
  ) then
    create policy account_incident_notifications_service_role_all
      on public.account_incident_notifications
      for all
      using (auth.role() = 'service_role')
      with check (auth.role() = 'service_role');
  end if;
end
$$;

revoke all on public.account_incident_notifications from public;
revoke all on public.account_incident_notifications from anon;
revoke all on public.account_incident_notifications from authenticated;

grant all on public.account_incident_notifications to service_role;
