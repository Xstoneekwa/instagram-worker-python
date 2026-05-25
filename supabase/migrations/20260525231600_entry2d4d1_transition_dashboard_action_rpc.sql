-- Entry 2D-4D-1 - account dashboard action atomic transition RPC.
--
-- Schema-only: adds the service-role RPC used later by the dashboard-actions
-- Edge mutations and by future login/provisioning workers. No Edge Function,
-- runtime, worker, dashboard UI, webhook, or device run integration is
-- introduced here.

create or replace function public.transition_account_dashboard_action(
  p_action_id uuid,
  p_new_status text,
  p_actor_type text,
  p_actor_id uuid default null,
  p_reason text default null,
  p_metadata jsonb default '{}'::jsonb
)
returns public.account_dashboard_actions
language plpgsql
security definer
set search_path = public
as $$
declare
  v_new_status text := lower(coalesce(nullif(trim(p_new_status), ''), ''));
  v_actor_type text := lower(coalesce(nullif(trim(p_actor_type), ''), ''));
  v_reason text := nullif(trim(coalesce(p_reason, '')), '');
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
  v_transition_at timestamptz := now();
  v_transition_metadata jsonb := '{}'::jsonb;
  v_existing_status text;
  v_row public.account_dashboard_actions;
begin
  if p_action_id is null then
    raise exception 'dashboard_action_not_found'
      using errcode = '22023';
  end if;

  if v_new_status not in ('acknowledged', 'dismissed', 'resolved', 'ignored') then
    raise exception 'invalid_dashboard_action_status'
      using errcode = '22023';
  end if;

  if v_actor_type not in ('client', 'admin', 'assistant', 'ops', 'internal', 'system') then
    raise exception 'invalid_dashboard_action_actor_type'
      using errcode = '22023';
  end if;

  if v_reason is not null and char_length(v_reason) > 500 then
    raise exception 'reason too long'
      using errcode = '22023';
  end if;

  if jsonb_typeof(v_metadata) is distinct from 'object' then
    raise exception 'metadata must be a json object'
      using errcode = '22023';
  end if;

  if exists (
    select 1
    from jsonb_object_keys(v_metadata) as k(key)
    where lower(k.key) in (
      'password',
      'secret',
      'secret_ref',
      'raw_secret',
      'token',
      'cookie',
      'webhook',
      'webhook_url',
      'vault',
      'service_role',
      'authorization',
      'bearer',
      'raw_xml',
      'xml',
      'screenshot'
    )
  ) then
    raise exception 'metadata contains a forbidden key'
      using errcode = '22023';
  end if;

  select ada.status
    into v_existing_status
  from public.account_dashboard_actions as ada
  where ada.id = p_action_id
  for update;

  if v_existing_status is null then
    raise exception 'dashboard_action_not_found'
      using errcode = 'P0002';
  end if;

  if v_existing_status in ('resolved', 'dismissed', 'ignored') then
    raise exception 'invalid_dashboard_action_transition'
      using errcode = '22023';
  end if;

  if v_existing_status = 'pending' and v_new_status not in ('acknowledged', 'dismissed', 'resolved', 'ignored') then
    raise exception 'invalid_dashboard_action_transition'
      using errcode = '22023';
  end if;

  if v_existing_status = 'acknowledged' and v_new_status not in ('acknowledged', 'dismissed', 'resolved', 'ignored') then
    raise exception 'invalid_dashboard_action_transition'
      using errcode = '22023';
  end if;

  if v_existing_status = 'pending_verification' and v_new_status not in ('acknowledged', 'dismissed', 'resolved', 'ignored') then
    raise exception 'invalid_dashboard_action_transition'
      using errcode = '22023';
  end if;

  v_transition_metadata := jsonb_build_object(
    'last_transition',
    v_new_status,
    'actor_type',
    v_actor_type,
    'transition_at',
    to_jsonb(v_transition_at)
  );

  if p_actor_id is not null then
    v_transition_metadata := v_transition_metadata || jsonb_build_object('actor_id', p_actor_id::text);
  end if;

  if v_reason is not null then
    v_transition_metadata := v_transition_metadata || jsonb_build_object('reason', v_reason);
  end if;

  update public.account_dashboard_actions as ada
  set
    status = v_new_status,
    updated_at = v_transition_at,
    acknowledged_at = case
      when v_new_status = 'acknowledged' then coalesce(ada.acknowledged_at, v_transition_at)
      else ada.acknowledged_at
    end,
    dismissed_at = case
      when v_new_status = 'dismissed' then v_transition_at
      else ada.dismissed_at
    end,
    resolved_at = case
      when v_new_status = 'resolved' then v_transition_at
      else ada.resolved_at
    end,
    metadata = coalesce(ada.metadata, '{}'::jsonb) || v_metadata || v_transition_metadata
  where ada.id = p_action_id
  returning ada.* into v_row;

  return v_row;
end;
$$;

comment on function public.transition_account_dashboard_action(
  uuid,
  text,
  text,
  uuid,
  text,
  jsonb
) is
  'Entry 2D-4D-1 service-role RPC for atomic account_dashboard_actions status transitions. Ownership is enforced by Edge/API callers, not by this RPC.';

revoke execute on function public.transition_account_dashboard_action(
  uuid,
  text,
  text,
  uuid,
  text,
  jsonb
) from public;

revoke execute on function public.transition_account_dashboard_action(
  uuid,
  text,
  text,
  uuid,
  text,
  jsonb
) from anon;

revoke execute on function public.transition_account_dashboard_action(
  uuid,
  text,
  text,
  uuid,
  text,
  jsonb
) from authenticated;

grant execute on function public.transition_account_dashboard_action(
  uuid,
  text,
  text,
  uuid,
  text,
  jsonb
) to service_role;
