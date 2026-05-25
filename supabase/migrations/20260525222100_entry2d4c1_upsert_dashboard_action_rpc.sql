-- Entry 2D-4C-1 - account dashboard action atomic upsert RPC.
--
-- Schema-only: adds the service-role RPC used later by Edge/internal helpers,
-- login/provisioning workers, and incident publishers. No runtime integration,
-- Edge Function changes, dashboard mutations, webhooks, or device runs are
-- introduced here.

create or replace function public.upsert_account_dashboard_action(
  p_account_id uuid,
  p_action_type text,
  p_title text,
  p_dedupe_key text,
  p_client_id uuid default null,
  p_incident_id uuid default null,
  p_status text default 'pending',
  p_severity text default 'warning',
  p_audience text default 'client',
  p_requires_client_action boolean default true,
  p_blocking_campaign boolean default false,
  p_safe_client_message text default null,
  p_assistant_message text default null,
  p_admin_message text default null,
  p_action_label text default null,
  p_action_deep_link text default null,
  p_metadata jsonb default '{}'::jsonb
)
returns public.account_dashboard_actions
language plpgsql
security definer
set search_path = public
as $$
declare
  v_action_type text := nullif(trim(p_action_type), '');
  v_title text := nullif(trim(p_title), '');
  v_dedupe_key text := nullif(trim(p_dedupe_key), '');
  v_status text := lower(coalesce(nullif(trim(p_status), ''), 'pending'));
  v_severity text := lower(coalesce(nullif(trim(p_severity), ''), 'warning'));
  v_audience text := lower(coalesce(nullif(trim(p_audience), ''), 'client'));
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
  v_now timestamptz;
  v_existing_id uuid;
  v_existing_account_id uuid;
  v_row public.account_dashboard_actions;
begin
  if p_account_id is null then
    raise exception 'account_id is required'
      using errcode = '22023';
  end if;

  if v_action_type is null then
    raise exception 'action_type must be non-empty'
      using errcode = '22023';
  end if;

  if v_title is null then
    raise exception 'title must be non-empty'
      using errcode = '22023';
  end if;

  if v_dedupe_key is null then
    raise exception 'dedupe_key must be non-empty'
      using errcode = '22023';
  end if;

  if jsonb_typeof(v_metadata) is distinct from 'object' then
    raise exception 'metadata must be a JSON object'
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

  if v_status not in ('pending', 'acknowledged', 'pending_verification', 'resolved', 'dismissed', 'ignored') then
    raise exception 'invalid dashboard action status: %', v_status
      using errcode = '22023';
  end if;

  if v_severity not in ('info', 'warning', 'error', 'critical') then
    raise exception 'invalid dashboard action severity: %', v_severity
      using errcode = '22023';
  end if;

  if v_audience not in ('client', 'admin', 'assistant', 'ops') then
    raise exception 'invalid dashboard action audience: %', v_audience
      using errcode = '22023';
  end if;

  loop
    v_now := now();
    v_existing_id := null;
    v_existing_account_id := null;

    select ada.id, ada.account_id
      into v_existing_id, v_existing_account_id
    from public.account_dashboard_actions as ada
    where ada.dedupe_key = v_dedupe_key
      and ada.status in ('pending', 'acknowledged', 'pending_verification')
    order by ada.created_at desc
    limit 1
    for update;

    if v_existing_id is not null then
      if v_existing_account_id <> p_account_id then
        raise exception 'dedupe_key belongs to another account'
          using errcode = '22023';
      end if;

      update public.account_dashboard_actions as ada
      set
        updated_at = v_now,
        client_id = coalesce(p_client_id, ada.client_id),
        incident_id = coalesce(p_incident_id, ada.incident_id),
        action_type = v_action_type,
        status = case
          when v_status in ('pending', 'acknowledged', 'pending_verification') then v_status
          else ada.status
        end,
        severity = case
          when (
            case ada.severity
              when 'info' then 1
              when 'warning' then 2
              when 'error' then 3
              when 'critical' then 4
            end
          ) >= (
            case v_severity
              when 'info' then 1
              when 'warning' then 2
              when 'error' then 3
              when 'critical' then 4
            end
          )
          then ada.severity
          else v_severity
        end,
        audience = v_audience,
        requires_client_action = coalesce(p_requires_client_action, ada.requires_client_action),
        blocking_campaign = coalesce(p_blocking_campaign, ada.blocking_campaign),
        title = v_title,
        safe_client_message = coalesce(nullif(trim(p_safe_client_message), ''), ada.safe_client_message),
        assistant_message = coalesce(nullif(trim(p_assistant_message), ''), ada.assistant_message),
        admin_message = coalesce(nullif(trim(p_admin_message), ''), ada.admin_message),
        action_label = coalesce(nullif(trim(p_action_label), ''), ada.action_label),
        action_deep_link = coalesce(nullif(trim(p_action_deep_link), ''), ada.action_deep_link),
        metadata = coalesce(ada.metadata, '{}'::jsonb) || v_metadata
      where ada.id = v_existing_id
      returning ada.* into v_row;

      return v_row;
    end if;

    begin
      insert into public.account_dashboard_actions (
        created_at,
        updated_at,
        account_id,
        client_id,
        incident_id,
        action_type,
        status,
        severity,
        audience,
        requires_client_action,
        blocking_campaign,
        title,
        safe_client_message,
        assistant_message,
        admin_message,
        action_label,
        action_deep_link,
        dedupe_key,
        metadata
      )
      values (
        v_now,
        v_now,
        p_account_id,
        p_client_id,
        p_incident_id,
        v_action_type,
        v_status,
        v_severity,
        v_audience,
        coalesce(p_requires_client_action, true),
        coalesce(p_blocking_campaign, false),
        v_title,
        nullif(trim(p_safe_client_message), ''),
        nullif(trim(p_assistant_message), ''),
        nullif(trim(p_admin_message), ''),
        nullif(trim(p_action_label), ''),
        nullif(trim(p_action_deep_link), ''),
        v_dedupe_key,
        v_metadata
      )
      returning * into v_row;

      return v_row;
    exception
      when unique_violation then
        -- A concurrent writer inserted the active dedupe row after our lookup.
        -- Retry so the next loop locks and updates that row atomically.
    end;
  end loop;
end;
$$;

comment on function public.upsert_account_dashboard_action(
  uuid,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  text,
  text,
  boolean,
  boolean,
  text,
  text,
  text,
  text,
  text,
  jsonb
) is
  'Entry 2D-4C-1 service-role RPC for atomic account_dashboard_actions active dedupe. It avoids PostgREST upsert because active dedupe uses a partial unique index.';

revoke execute on function public.upsert_account_dashboard_action(
  uuid,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  text,
  text,
  boolean,
  boolean,
  text,
  text,
  text,
  text,
  text,
  jsonb
) from public;

revoke execute on function public.upsert_account_dashboard_action(
  uuid,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  text,
  text,
  boolean,
  boolean,
  text,
  text,
  text,
  text,
  text,
  jsonb
) from anon;

revoke execute on function public.upsert_account_dashboard_action(
  uuid,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  text,
  text,
  boolean,
  boolean,
  text,
  text,
  text,
  text,
  text,
  jsonb
) from authenticated;

grant execute on function public.upsert_account_dashboard_action(
  uuid,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  text,
  text,
  boolean,
  boolean,
  text,
  text,
  text,
  text,
  text,
  jsonb
) to service_role;
