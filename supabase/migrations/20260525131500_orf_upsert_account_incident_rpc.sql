-- ORF-3B-1 - account incident atomic upsert RPC.
--
-- Schema-only: adds the service-role RPC used later by runtime_incidents.py.
-- No Python runtime integration, no Edge Functions, no webhooks, no Redis, and
-- no dashboard/client access are introduced here.

create or replace function public.upsert_account_incident(
  p_incident_type text,
  p_dedupe_key text,
  p_severity text default 'warning',
  p_status text default 'open',
  p_client_id uuid default null,
  p_account_id uuid default null,
  p_account_username text default null,
  p_run_id uuid default null,
  p_assignment_id uuid default null,
  p_device_id uuid default null,
  p_clone_id uuid default null,
  p_source_event_id uuid default null,
  p_source text default null,
  p_reason text default null,
  p_failure_reason text default null,
  p_action_required text default null,
  p_safe_client_message text default null,
  p_assistant_message text default null,
  p_admin_message text default null,
  p_metadata jsonb default '{}'::jsonb
)
returns public.account_incidents
language plpgsql
security definer
set search_path = public
as $$
declare
  v_incident_type text := nullif(trim(p_incident_type), '');
  v_dedupe_key text := nullif(trim(p_dedupe_key), '');
  v_severity text := lower(coalesce(nullif(trim(p_severity), ''), 'warning'));
  v_status text := lower(coalesce(nullif(trim(p_status), ''), 'open'));
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
  v_now timestamptz;
  v_existing_id uuid;
  v_row public.account_incidents;
begin
  if v_incident_type is null then
    raise exception 'incident_type must be non-empty'
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

  if v_status not in ('open', 'acknowledged', 'resolved', 'ignored') then
    raise exception 'invalid account incident status: %', v_status
      using errcode = '22023';
  end if;

  if v_severity not in ('info', 'warning', 'error', 'critical') then
    raise exception 'invalid account incident severity: %', v_severity
      using errcode = '22023';
  end if;

  loop
    v_now := now();
    v_existing_id := null;

    select ai.id
      into v_existing_id
    from public.account_incidents as ai
    where ai.dedupe_key = v_dedupe_key
      and ai.status in ('open', 'acknowledged')
    order by ai.created_at desc
    limit 1
    for update;

    if v_existing_id is not null then
      update public.account_incidents as ai
      set
        severity = case
          when (
            case ai.severity
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
          then ai.severity
          else v_severity
        end,
        occurrence_count = ai.occurrence_count + 1,
        last_seen_at = v_now,
        updated_at = v_now,
        client_id = coalesce(p_client_id, ai.client_id),
        account_id = coalesce(p_account_id, ai.account_id),
        account_username = coalesce(nullif(trim(p_account_username), ''), ai.account_username),
        run_id = coalesce(p_run_id, ai.run_id),
        assignment_id = coalesce(p_assignment_id, ai.assignment_id),
        device_id = coalesce(p_device_id, ai.device_id),
        clone_id = coalesce(p_clone_id, ai.clone_id),
        source_event_id = coalesce(p_source_event_id, ai.source_event_id),
        source = coalesce(nullif(trim(p_source), ''), ai.source),
        reason = coalesce(nullif(trim(p_reason), ''), ai.reason),
        failure_reason = coalesce(nullif(trim(p_failure_reason), ''), ai.failure_reason),
        action_required = coalesce(nullif(trim(p_action_required), ''), ai.action_required),
        safe_client_message = coalesce(nullif(trim(p_safe_client_message), ''), ai.safe_client_message),
        assistant_message = coalesce(nullif(trim(p_assistant_message), ''), ai.assistant_message),
        admin_message = coalesce(nullif(trim(p_admin_message), ''), ai.admin_message),
        metadata = coalesce(ai.metadata, '{}'::jsonb) || v_metadata
      where ai.id = v_existing_id
      returning ai.* into v_row;

      return v_row;
    end if;

    begin
      insert into public.account_incidents (
        created_at,
        updated_at,
        first_seen_at,
        last_seen_at,
        status,
        severity,
        incident_type,
        dedupe_key,
        occurrence_count,
        client_id,
        account_id,
        account_username,
        run_id,
        assignment_id,
        device_id,
        clone_id,
        source_event_id,
        source,
        reason,
        failure_reason,
        action_required,
        safe_client_message,
        assistant_message,
        admin_message,
        metadata
      )
      values (
        v_now,
        v_now,
        v_now,
        v_now,
        v_status,
        v_severity,
        v_incident_type,
        v_dedupe_key,
        1,
        p_client_id,
        p_account_id,
        nullif(trim(p_account_username), ''),
        p_run_id,
        p_assignment_id,
        p_device_id,
        p_clone_id,
        p_source_event_id,
        nullif(trim(p_source), ''),
        nullif(trim(p_reason), ''),
        nullif(trim(p_failure_reason), ''),
        nullif(trim(p_action_required), ''),
        nullif(trim(p_safe_client_message), ''),
        nullif(trim(p_assistant_message), ''),
        nullif(trim(p_admin_message), ''),
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

comment on function public.upsert_account_incident(
  text,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  uuid,
  uuid,
  uuid,
  uuid,
  uuid,
  text,
  text,
  text,
  text,
  text,
  text,
  text,
  jsonb
) is
  'ORF-3B-1 service-role RPC for atomic account_incidents active dedupe. It avoids PostgREST upsert because active dedupe uses a partial unique index.';

revoke execute on function public.upsert_account_incident(
  text,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  uuid,
  uuid,
  uuid,
  uuid,
  uuid,
  text,
  text,
  text,
  text,
  text,
  text,
  text,
  jsonb
) from public;

revoke execute on function public.upsert_account_incident(
  text,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  uuid,
  uuid,
  uuid,
  uuid,
  uuid,
  text,
  text,
  text,
  text,
  text,
  text,
  text,
  jsonb
) from anon;

revoke execute on function public.upsert_account_incident(
  text,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  uuid,
  uuid,
  uuid,
  uuid,
  uuid,
  text,
  text,
  text,
  text,
  text,
  text,
  text,
  jsonb
) from authenticated;

grant execute on function public.upsert_account_incident(
  text,
  text,
  text,
  text,
  uuid,
  uuid,
  text,
  uuid,
  uuid,
  uuid,
  uuid,
  uuid,
  text,
  text,
  text,
  text,
  text,
  text,
  text,
  jsonb
) to service_role;
