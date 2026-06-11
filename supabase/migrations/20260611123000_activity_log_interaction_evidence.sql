-- Activity Log interaction evidence foundation.
--
-- Safe additive migration only:
-- - no destructive DDL;
-- - no NOT NULL on existing rows;
-- - no worker-breaking column/type changes;
-- - no inferred CT proof when source evidence is missing.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Add durable evidence columns to state and append-only interaction tables
-- =============================================================================
alter table public.ig_interacted_users
  add column if not exists ct_id uuid references public.ig_targets(id) on delete set null,
  add column if not exists source_target_id uuid references public.ig_targets(id) on delete set null,
  add column if not exists source_target_username text,
  add column if not exists request_id uuid references public.account_run_requests(id) on delete set null,
  add column if not exists device_id uuid references public.phone_devices(id) on delete set null,
  add column if not exists safe_device_label text,
  add column if not exists interaction_status text,
  add column if not exists evidence_source text,
  add column if not exists evidence_confidence text,
  add column if not exists evidence_summary text,
  add column if not exists metadata_safe jsonb;

alter table public.ig_interaction_events
  add column if not exists ct_id uuid references public.ig_targets(id) on delete set null,
  add column if not exists source_target_id uuid references public.ig_targets(id) on delete set null,
  add column if not exists source_target_username text,
  add column if not exists request_id uuid references public.account_run_requests(id) on delete set null,
  add column if not exists device_id uuid references public.phone_devices(id) on delete set null,
  add column if not exists safe_device_label text,
  add column if not exists interaction_type text,
  add column if not exists interaction_status text,
  add column if not exists evidence_source text,
  add column if not exists evidence_confidence text,
  add column if not exists evidence_summary text,
  add column if not exists metadata_safe jsonb;

-- =============================================================================
-- 2. Safe constraints (NOT VALID where existing rows might need cleanup first)
-- =============================================================================
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'ig_interacted_users_evidence_confidence_check'
      and conrelid = 'public.ig_interacted_users'::regclass
  ) then
    alter table public.ig_interacted_users
      add constraint ig_interacted_users_evidence_confidence_check
      check (
        evidence_confidence is null
        or evidence_confidence in ('high', 'medium', 'best_effort', 'unknown')
      ) not valid;
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'ig_interaction_events_evidence_confidence_check'
      and conrelid = 'public.ig_interaction_events'::regclass
  ) then
    alter table public.ig_interaction_events
      add constraint ig_interaction_events_evidence_confidence_check
      check (
        evidence_confidence is null
        or evidence_confidence in ('high', 'medium', 'best_effort', 'unknown')
      ) not valid;
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'ig_interacted_users_metadata_safe_object_check'
      and conrelid = 'public.ig_interacted_users'::regclass
  ) then
    alter table public.ig_interacted_users
      add constraint ig_interacted_users_metadata_safe_object_check
      check (metadata_safe is null or jsonb_typeof(metadata_safe) = 'object') not valid;
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'ig_interaction_events_metadata_safe_object_check'
      and conrelid = 'public.ig_interaction_events'::regclass
  ) then
    alter table public.ig_interaction_events
      add constraint ig_interaction_events_metadata_safe_object_check
      check (metadata_safe is null or jsonb_typeof(metadata_safe) = 'object') not valid;
  end if;
end
$$;

-- =============================================================================
-- 3. Indexes for investigation searches
-- =============================================================================
create index if not exists ig_interacted_users_account_source_target_last_idx
  on public.ig_interacted_users (account_id, source_target_id, last_interaction_at desc)
  where source_target_id is not null;

create index if not exists ig_interacted_users_account_source_username_last_idx
  on public.ig_interacted_users (account_id, source_target_username, last_interaction_at desc)
  where source_target_username is not null;

create index if not exists ig_interacted_users_account_status_last_idx
  on public.ig_interacted_users (account_id, interaction_status, last_interaction_at desc)
  where interaction_status is not null;

create index if not exists ig_interacted_users_request_id_idx
  on public.ig_interacted_users (request_id)
  where request_id is not null;

create index if not exists ig_interaction_events_account_source_target_at_idx
  on public.ig_interaction_events (account_id, source_target_id, event_at desc)
  where source_target_id is not null;

create index if not exists ig_interaction_events_account_source_username_at_idx
  on public.ig_interaction_events (account_id, source_target_username, event_at desc)
  where source_target_username is not null;

create index if not exists ig_interaction_events_account_interaction_type_at_idx
  on public.ig_interaction_events (account_id, interaction_type, event_at desc)
  where interaction_type is not null;

create index if not exists ig_interaction_events_request_id_idx
  on public.ig_interaction_events (request_id)
  where request_id is not null;

-- =============================================================================
-- 4. Best-effort, non-destructive backfill
-- =============================================================================
update public.ig_interaction_events as event
set
  source_target_id = coalesce(event.source_target_id, event.target_id),
  ct_id = coalesce(event.ct_id, event.target_id),
  source_target_username = coalesce(
    nullif(event.source_target_username, ''),
    nullif(event.source_profile, ''),
    target.normalized_username,
    target.target_username
  ),
  interaction_type = coalesce(
    nullif(event.interaction_type, ''),
    case
      when lower(coalesce(event.event_type, '')) like '%unfollow%' then 'unfollow'
      when lower(coalesce(event.event_type, '')) like '%followback%' then 'followback'
      when lower(coalesce(event.event_type, '')) like '%follow%' then 'follow'
      when lower(coalesce(event.event_type, '')) like '%post_like%' then 'like'
      when lower(coalesce(event.event_type, '')) like '%like%' then 'like'
      when lower(coalesce(event.event_type, '')) like '%dm%' then 'dm'
      when lower(coalesce(event.event_type, '')) like '%story%' then 'story_view'
      when lower(coalesce(event.event_type, '')) like '%profile_visit%' then 'profile_visit'
      else null
    end
  ),
  interaction_status = coalesce(nullif(event.interaction_status, ''), nullif(event.event_status, '')),
  evidence_source = coalesce(nullif(event.evidence_source, ''), 'ig_interaction_events'),
  evidence_confidence = coalesce(
    nullif(event.evidence_confidence, ''),
    case
      when event.target_id is not null and nullif(event.source_profile, '') is not null then 'high'
      when nullif(event.source_profile, '') is not null then 'medium'
      else 'unknown'
    end
  ),
  evidence_summary = coalesce(
    nullif(event.evidence_summary, ''),
    concat_ws(
      ' ',
      'Interaction event',
      nullif(event.event_type, ''),
      'for',
      '@' || nullif(event.username, ''),
      case
        when coalesce(nullif(event.source_profile, ''), target.normalized_username, target.target_username) is not null
          then 'via CT @' || coalesce(nullif(event.source_profile, ''), target.normalized_username, target.target_username)
        else null
      end
    )
  ),
  metadata_safe = coalesce(event.metadata_safe, '{}'::jsonb)
from public.ig_targets as target
where event.target_id = target.id
  and (
    event.source_target_id is null
    or event.ct_id is null
    or nullif(event.source_target_username, '') is null
    or nullif(event.interaction_type, '') is null
    or nullif(event.interaction_status, '') is null
    or nullif(event.evidence_source, '') is null
    or nullif(event.evidence_confidence, '') is null
    or nullif(event.evidence_summary, '') is null
    or event.metadata_safe is null
  );

update public.ig_interaction_events as event
set
  source_target_username = coalesce(nullif(event.source_target_username, ''), nullif(event.source_profile, '')),
  interaction_status = coalesce(nullif(event.interaction_status, ''), nullif(event.event_status, '')),
  evidence_source = coalesce(nullif(event.evidence_source, ''), 'ig_interaction_events'),
  evidence_confidence = coalesce(nullif(event.evidence_confidence, ''), 'unknown'),
  metadata_safe = coalesce(event.metadata_safe, '{}'::jsonb)
where event.target_id is null
  and (
    nullif(event.source_target_username, '') is null
    or nullif(event.interaction_status, '') is null
    or nullif(event.evidence_source, '') is null
    or nullif(event.evidence_confidence, '') is null
    or event.metadata_safe is null
  );

update public.ig_interacted_users as interacted
set
  source_target_id = coalesce(
    interacted.source_target_id,
    case
      when interacted.payload ? 'target_id'
        and (interacted.payload ->> 'target_id') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        then (interacted.payload ->> 'target_id')::uuid
      else null
    end
  ),
  ct_id = coalesce(
    interacted.ct_id,
    case
      when interacted.payload ? 'target_id'
        and (interacted.payload ->> 'target_id') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        then (interacted.payload ->> 'target_id')::uuid
      else null
    end
  ),
  source_target_username = coalesce(
    nullif(interacted.source_target_username, ''),
    nullif(interacted.last_source_profile, ''),
    nullif(interacted.source_profile, '')
  ),
  interaction_status = coalesce(
    nullif(interacted.interaction_status, ''),
    nullif(interacted.follow_status, ''),
    case
      when interacted.was_successful is true then 'success'
      when interacted.was_successful is false then 'failed'
      else null
    end
  ),
  evidence_source = coalesce(nullif(interacted.evidence_source, ''), 'ig_interacted_users'),
  evidence_confidence = coalesce(
    nullif(interacted.evidence_confidence, ''),
    case
      when interacted.payload ? 'target_id' and nullif(interacted.last_source_profile, '') is not null then 'high'
      when nullif(interacted.last_source_profile, '') is not null or nullif(interacted.source_profile, '') is not null then 'medium'
      else 'unknown'
    end
  ),
  evidence_summary = coalesce(
    nullif(interacted.evidence_summary, ''),
    concat_ws(
      ' ',
      'Interaction memory',
      nullif(interacted.interaction_type, ''),
      'for',
      '@' || nullif(interacted.username, ''),
      case
        when coalesce(nullif(interacted.last_source_profile, ''), nullif(interacted.source_profile, '')) is not null
          then 'via CT @' || coalesce(nullif(interacted.last_source_profile, ''), nullif(interacted.source_profile, ''))
        else null
      end
    )
  ),
  metadata_safe = coalesce(interacted.metadata_safe, '{}'::jsonb)
where
  interacted.source_target_id is null
  or interacted.ct_id is null
  or nullif(interacted.source_target_username, '') is null
  or nullif(interacted.interaction_status, '') is null
  or nullif(interacted.evidence_source, '') is null
  or nullif(interacted.evidence_confidence, '') is null
  or nullif(interacted.evidence_summary, '') is null
  or interacted.metadata_safe is null;

update public.ig_interaction_events as event
set request_id = request.id
from public.account_run_requests as request
where event.request_id is null
  and event.run_id = request.run_id
  and request.run_id is not null;

update public.ig_interacted_users as interacted
set request_id = request.id
from public.account_run_requests as request
where interacted.request_id is null
  and coalesce(interacted.last_run_id, interacted.run_id) = request.run_id
  and request.run_id is not null;

-- =============================================================================
-- 5. Admin read-only projection
-- =============================================================================
create or replace view public.activity_log_interaction_evidence_admin_v1
with (security_invoker = true)
as
with latest_assignment as (
  select distinct on (aa.account_id)
    aa.account_id,
    aa.device_id,
    coalesce(nullif(pd.name, ''), nullif(pd.device_name, ''), 'assigned phone') as assignment_device_label
  from public.account_assignments aa
  left join public.phone_devices pd
    on pd.id = aa.device_id
  where aa.status in ('reserved', 'active')
  order by aa.account_id, aa.created_at desc
),
event_records as (
  select
    event.id as source_record_id,
    'ig_interaction_events'::text as evidence_source_table,
    event.account_id,
    cia.client_id,
    coalesce(nullif(ia.username, ''), nullif(cia.label, '')) as client_account_username,
    coalesce(event.ct_id, event.source_target_id, event.target_id) as ct_id,
    coalesce(
      nullif(event.source_target_username, ''),
      target.normalized_username,
      target.target_username,
      nullif(event.source_profile, '')
    ) as ct_username,
    event.username as interacted_username,
    coalesce(nullif(event.interaction_type, ''), nullif(event.event_type, ''), 'unknown') as action_type,
    coalesce(nullif(event.interaction_status, ''), nullif(event.event_status, ''), 'unknown') as action_status,
    event.event_at as occurred_at,
    event.run_id,
    event.request_id,
    coalesce(nullif(event.safe_device_label, ''), la.assignment_device_label) as safe_device_label,
    coalesce(nullif(event.evidence_source, ''), 'ig_interaction_events') as evidence_source,
    coalesce(nullif(event.evidence_confidence, ''), 'unknown') as evidence_confidence,
    coalesce(nullif(event.evidence_summary, ''), event.event_reason, 'Interaction event persisted by worker.') as evidence_summary,
    coalesce(event.metadata_safe, '{}'::jsonb) as metadata_safe
  from public.ig_interaction_events event
  left join public.client_instagram_accounts cia
    on cia.account_id = event.account_id
  left join public.ig_accounts ia
    on ia.id = event.account_id
  left join public.ig_targets target
    on target.id = coalesce(event.source_target_id, event.target_id, event.ct_id)
  left join latest_assignment la
    on la.account_id = event.account_id
),
state_records as (
  select
    interacted.id as source_record_id,
    'ig_interacted_users'::text as evidence_source_table,
    interacted.account_id,
    cia.client_id,
    coalesce(nullif(ia.username, ''), nullif(cia.label, '')) as client_account_username,
    coalesce(interacted.ct_id, interacted.source_target_id) as ct_id,
    coalesce(
      nullif(interacted.source_target_username, ''),
      target.normalized_username,
      target.target_username,
      nullif(interacted.last_source_profile, ''),
      nullif(interacted.source_profile, '')
    ) as ct_username,
    interacted.username as interacted_username,
    coalesce(nullif(interacted.interaction_type, ''), 'unknown') as action_type,
    coalesce(nullif(interacted.interaction_status, ''), nullif(interacted.follow_status, ''), 'unknown') as action_status,
    coalesce(interacted.last_interaction_at, interacted.updated_at, interacted.created_at) as occurred_at,
    coalesce(interacted.last_run_id, interacted.run_id) as run_id,
    interacted.request_id,
    coalesce(nullif(interacted.safe_device_label, ''), la.assignment_device_label) as safe_device_label,
    coalesce(nullif(interacted.evidence_source, ''), 'ig_interacted_users') as evidence_source,
    coalesce(nullif(interacted.evidence_confidence, ''), 'unknown') as evidence_confidence,
    coalesce(nullif(interacted.evidence_summary, ''), 'Interaction state persisted by worker.') as evidence_summary,
    coalesce(interacted.metadata_safe, '{}'::jsonb) as metadata_safe
  from public.ig_interacted_users interacted
  left join public.client_instagram_accounts cia
    on cia.account_id = interacted.account_id
  left join public.ig_accounts ia
    on ia.id = interacted.account_id
  left join public.ig_targets target
    on target.id = coalesce(interacted.source_target_id, interacted.ct_id)
  left join latest_assignment la
    on la.account_id = interacted.account_id
)
select * from event_records
union all
select * from state_records;

comment on view public.activity_log_interaction_evidence_admin_v1 is
  'Admin-safe interaction evidence projection for Activity Log investigation. Does not expose internal device identifiers, worker payloads, sensitive runtime artifacts, or cross-client joins outside caller filtering.';

create or replace function public.get_activity_log_interaction_evidence_admin(
  p_account_id uuid default null,
  p_search text default null,
  p_mode text default 'all',
  p_period text default '7d',
  p_limit integer default 100
)
returns table (
  source_record_id uuid,
  evidence_source_table text,
  account_id uuid,
  client_id uuid,
  client_account_username text,
  ct_id uuid,
  ct_username text,
  interacted_username text,
  action_type text,
  action_status text,
  occurred_at timestamptz,
  run_id uuid,
  request_id uuid,
  safe_device_label text,
  evidence_source text,
  evidence_confidence text,
  evidence_summary text,
  metadata_safe jsonb
)
language sql
stable
security invoker
set search_path = public
as $$
  select
    source_record_id,
    evidence_source_table,
    account_id,
    client_id,
    client_account_username,
    ct_id,
    ct_username,
    interacted_username,
    action_type,
    action_status,
    occurred_at,
    run_id,
    request_id,
    safe_device_label,
    evidence_source,
    evidence_confidence,
    evidence_summary,
    metadata_safe
  from public.activity_log_interaction_evidence_admin_v1 evidence
  where (p_account_id is null or evidence.account_id = p_account_id)
    and evidence.occurred_at >= now() - case lower(coalesce(nullif(trim(p_period), ''), '7d'))
      when '24h' then interval '24 hours'
      when '30d' then interval '30 days'
      else interval '7 days'
    end
    and (
      nullif(trim(coalesce(p_search, '')), '') is null
      or case lower(coalesce(nullif(trim(p_mode), ''), 'all'))
        when 'search_by_ct' then coalesce(evidence.ct_username, '') ilike '%' || trim(both '@' from p_search) || '%'
        when 'search_by_account' then coalesce(evidence.interacted_username, '') ilike '%' || trim(both '@' from p_search) || '%'
        else (
          coalesce(evidence.ct_username, '') ilike '%' || trim(both '@' from p_search) || '%'
          or coalesce(evidence.interacted_username, '') ilike '%' || trim(both '@' from p_search) || '%'
          or coalesce(evidence.client_account_username, '') ilike '%' || trim(both '@' from p_search) || '%'
        )
      end
    )
  order by evidence.occurred_at desc nulls last
  limit least(greatest(coalesce(p_limit, 100), 1), 500);
$$;

revoke all on public.activity_log_interaction_evidence_admin_v1 from public;
revoke all on public.activity_log_interaction_evidence_admin_v1 from anon;
revoke all on public.activity_log_interaction_evidence_admin_v1 from authenticated;
grant select on public.activity_log_interaction_evidence_admin_v1 to service_role;

revoke all on function public.get_activity_log_interaction_evidence_admin(uuid, text, text, text, integer) from public;
revoke all on function public.get_activity_log_interaction_evidence_admin(uuid, text, text, text, integer) from anon;
revoke all on function public.get_activity_log_interaction_evidence_admin(uuid, text, text, text, integer) from authenticated;
grant execute on function public.get_activity_log_interaction_evidence_admin(uuid, text, text, text, integer) to service_role;
