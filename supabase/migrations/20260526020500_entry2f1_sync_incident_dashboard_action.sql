-- Entry 2F-1 - sync account incidents to dashboard actions.
--
-- Schema-only RPC for projecting durable ORF account_incidents into
-- account_dashboard_actions. No Python runtime wiring, Edge Function changes,
-- dashboard UI changes, webhooks, or device runs are introduced here.

create or replace function public.sync_account_incident_dashboard_action(
  p_incident_id uuid,
  p_actor_type text default 'system',
  p_reason text default null,
  p_metadata jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor_type text := lower(coalesce(nullif(trim(p_actor_type), ''), 'system'));
  v_transition_actor_type text;
  v_reason text := nullif(trim(coalesce(p_reason, '')), '');
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
  v_incident public.account_incidents%rowtype;
  v_incident_metadata jsonb := '{}'::jsonb;
  v_action_type text;
  v_action_title text;
  v_action_label text;
  v_action_deep_link text;
  v_action_dedupe_key text;
  v_action public.account_dashboard_actions;
  v_existing_action public.account_dashboard_actions%rowtype;
  v_dashboard_action_id uuid;
  v_login_status text;
  v_sync_metadata jsonb;
  v_transition_status text;
begin
  if p_incident_id is null then
    return jsonb_build_object(
      'ok', false,
      'action', 'skipped',
      'reason', 'incident_id_required'
    );
  end if;

  if v_actor_type not in ('client', 'admin', 'assistant', 'ops', 'internal', 'system', 'worker', 'provisioner') then
    raise exception 'invalid_incident_dashboard_action_actor_type'
      using errcode = '22023';
  end if;

  v_transition_actor_type := case
    when v_actor_type in ('worker', 'provisioner') then 'system'
    else v_actor_type
  end;

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
      'adb_serial',
      'device_udid',
      'raw_xml',
      'xml',
      'screenshot',
      'session_cookie'
    )
  ) then
    raise exception 'metadata contains a forbidden key'
      using errcode = '22023';
  end if;

  select ai.*
    into v_incident
  from public.account_incidents as ai
  where ai.id = p_incident_id
  for update;

  if v_incident.id is null then
    return jsonb_build_object(
      'ok', false,
      'incident_id', p_incident_id,
      'action', 'skipped',
      'reason', 'incident_not_found'
    );
  end if;

  if v_incident.account_id is null then
    return jsonb_build_object(
      'ok', true,
      'incident_id', v_incident.id,
      'incident_type', v_incident.incident_type,
      'incident_status', v_incident.status,
      'action', 'skipped',
      'reason', 'missing_account_id'
    );
  end if;

  if v_incident.incident_type = 'active_instagram_account_mismatch' then
    v_action_type := 'review_account_mismatch';
    v_action_title := 'Compte Instagram incohérent';
    v_action_label := 'Examiner le compte';
    v_action_deep_link := '/admin/accounts/' || v_incident.account_id::text || '/identity';
    v_action_dedupe_key := 'account:' || v_incident.account_id::text || ':dashboard_action:review_account_mismatch';
  else
    return jsonb_build_object(
      'ok', true,
      'incident_id', v_incident.id,
      'incident_type', v_incident.incident_type,
      'incident_status', v_incident.status,
      'action', 'skipped',
      'reason', 'unsupported_incident_type'
    );
  end if;

  v_incident_metadata := coalesce(v_incident.metadata, '{}'::jsonb);
  v_sync_metadata := v_metadata || jsonb_strip_nulls(
    jsonb_build_object(
      'source', 'account_incident',
      'incident_id', v_incident.id::text,
      'incident_type', v_incident.incident_type,
      'incident_status', v_incident.status,
      'incident_severity', v_incident.severity,
      'occurrence_count', v_incident.occurrence_count,
      'run_id', coalesce(v_incident.run_id::text, v_incident_metadata ->> 'run_id'),
      'stage', v_incident_metadata ->> 'stage',
      'run_type', v_incident_metadata ->> 'run_type',
      'expected_account_username', v_incident_metadata ->> 'expected_account_username',
      'actual_username', coalesce(
        v_incident_metadata ->> 'actual_username',
        v_incident_metadata ->> 'actual_logged_in_username'
      ),
      'verification_method', v_incident_metadata ->> 'verification_method',
      'identity_evidence', v_incident_metadata ->> 'identity_evidence',
      'guard_reason', coalesce(v_incident_metadata ->> 'guard_reason', v_incident.failure_reason)
    )
  );

  if v_incident.status in ('open', 'acknowledged') then
    v_action := public.upsert_account_dashboard_action(
      p_account_id := v_incident.account_id,
      p_client_id := v_incident.client_id,
      p_incident_id := v_incident.id,
      p_action_type := v_action_type,
      p_status := 'pending',
      p_severity := 'critical',
      p_audience := 'admin',
      p_requires_client_action := false,
      p_blocking_campaign := true,
      p_title := v_action_title,
      p_safe_client_message := null,
      p_assistant_message := v_incident.assistant_message,
      p_admin_message := coalesce(
        nullif(trim(v_incident.admin_message), ''),
        'Le compte connecté ne correspond pas au compte attendu. Vérification admin requise.'
      ),
      p_action_label := v_action_label,
      p_action_deep_link := v_action_deep_link,
      p_dedupe_key := v_action_dedupe_key,
      p_metadata := v_sync_metadata
    );

    return jsonb_build_object(
      'ok', true,
      'incident_id', v_incident.id,
      'incident_type', v_incident.incident_type,
      'incident_status', v_incident.status,
      'action', 'upserted',
      'dashboard_action_id', v_action.id,
      'action_type', v_action.action_type,
      'reason', 'incident_open'
    );
  end if;

  if v_incident.status in ('resolved', 'ignored') then
    select cia.login_status
      into v_login_status
    from public.client_instagram_accounts as cia
    where cia.account_id = v_incident.account_id;

    if v_incident.incident_type = 'active_instagram_account_mismatch'
       and v_login_status = 'mismatch' then
      return jsonb_build_object(
        'ok', true,
        'incident_id', v_incident.id,
        'incident_type', v_incident.incident_type,
        'incident_status', v_incident.status,
        'action', 'skipped',
        'action_type', v_action_type,
        'reason', 'status_still_mismatch'
      );
    end if;

    select ada.*
      into v_existing_action
    from public.account_dashboard_actions as ada
    where ada.account_id = v_incident.account_id
      and ada.action_type = v_action_type
      and ada.status in ('pending', 'acknowledged', 'pending_verification')
      and (
        ada.incident_id = v_incident.id
        or ada.metadata ->> 'incident_id' = v_incident.id::text
      )
    order by ada.created_at desc
    limit 1
    for update;

    if v_existing_action.id is null then
      return jsonb_build_object(
        'ok', true,
        'incident_id', v_incident.id,
        'incident_type', v_incident.incident_type,
        'incident_status', v_incident.status,
        'action', 'skipped',
        'action_type', v_action_type,
        'reason', 'dashboard_action_not_found'
      );
    end if;

    v_transition_status := case
      when v_incident.status = 'ignored' then 'ignored'
      else 'resolved'
    end;

    v_action := public.transition_account_dashboard_action(
      v_existing_action.id,
      v_transition_status,
      v_transition_actor_type,
      null,
      coalesce(v_reason, 'incident_' || v_incident.status),
      v_sync_metadata
    );

    return jsonb_build_object(
      'ok', true,
      'incident_id', v_incident.id,
      'incident_type', v_incident.incident_type,
      'incident_status', v_incident.status,
      'action', v_transition_status,
      'dashboard_action_id', v_action.id,
      'action_type', v_action.action_type,
      'reason', 'incident_' || v_incident.status
    );
  end if;

  return jsonb_build_object(
    'ok', true,
    'incident_id', v_incident.id,
    'incident_type', v_incident.incident_type,
    'incident_status', v_incident.status,
    'action', 'skipped',
    'reason', 'unsupported_incident_status'
  );
end;
$$;

comment on function public.sync_account_incident_dashboard_action(
  uuid,
  text,
  text,
  jsonb
) is
  'Entry 2F-1 service-role RPC that projects supported account_incidents into account_dashboard_actions using action-level dedupe. V1 supports active_instagram_account_mismatch -> review_account_mismatch only.';

revoke execute on function public.sync_account_incident_dashboard_action(
  uuid,
  text,
  text,
  jsonb
) from public;

revoke execute on function public.sync_account_incident_dashboard_action(
  uuid,
  text,
  text,
  jsonb
) from anon;

revoke execute on function public.sync_account_incident_dashboard_action(
  uuid,
  text,
  text,
  jsonb
) from authenticated;

grant execute on function public.sync_account_incident_dashboard_action(
  uuid,
  text,
  text,
  jsonb
) to service_role;
