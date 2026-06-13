create or replace function public.sync_instagram_account_runtime_settings_after_provisioning(
  p_account_id uuid,
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
  v_reason text := nullif(trim(coalesce(p_reason, '')), '');
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
  v_package_name text;
  v_app_instance_id uuid;
  v_device_id uuid;
  v_settings_updated boolean := false;
  v_dm_settings_updated boolean := false;
  v_unfollow_settings_updated boolean := false;
begin
  if p_account_id is null then
    raise exception 'account_id_required'
      using errcode = '22023';
  end if;

  if v_actor_type not in ('client', 'admin', 'assistant', 'ops', 'internal', 'system', 'worker', 'provisioner') then
    raise exception 'invalid_runtime_settings_actor_type'
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

  select pai.package_name, pai.id, pai.device_id
    into v_package_name, v_app_instance_id, v_device_id
  from public.account_assignments as aa
  join public.phone_app_instances as pai
    on pai.id = aa.app_instance_id
  where aa.account_id = p_account_id
    and aa.released_at is null
    and aa.status in ('reserved', 'active')
    and nullif(trim(coalesce(pai.package_name, '')), '') is not null
  order by aa.assigned_at desc nulls last, aa.created_at desc
  limit 1;

  if nullif(trim(coalesce(v_package_name, '')), '') is null then
    return jsonb_build_object(
      'ok', false,
      'applied', false,
      'reason', 'active_assignment_package_missing',
      'account_id', p_account_id
    );
  end if;

  update public.ig_account_settings as s
  set
    app_package = v_package_name,
    follow_enabled = true,
    like_enabled = true,
    mute_posts_after_follow = true,
    mute_stories_after_follow = true,
    welcome_dm_enabled = false,
    cold_dm_enabled = false,
    unfollow_enabled = false,
    updated_at = now()
  where s.account_id = p_account_id;
  get diagnostics v_settings_updated = row_count;

  update public.ig_account_dm_settings as d
  set
    welcome_enabled = false,
    outreach_enabled = false,
    updated_at = now()
  where d.account_id = p_account_id;
  get diagnostics v_dm_settings_updated = row_count;

  update public.ig_account_unfollow_settings as u
  set
    unfollow_enabled = false,
    updated_at = now()
  where u.account_id = p_account_id;
  get diagnostics v_unfollow_settings_updated = row_count;

  return jsonb_build_object(
    'ok', true,
    'applied', true,
    'reason', 'runtime_settings_synced_after_provisioning',
    'account_id', p_account_id,
    'package_name', v_package_name,
    'app_instance_id', v_app_instance_id,
    'device_id', v_device_id,
    'settings_updated', v_settings_updated,
    'dm_settings_updated', v_dm_settings_updated,
    'unfollow_settings_updated', v_unfollow_settings_updated,
    'follow_enabled', true,
    'like_enabled', true,
    'mute_posts_after_follow', true,
    'mute_stories_after_follow', true,
    'welcome_enabled', false,
    'outreach_enabled', false,
    'unfollow_enabled', false
  );
end;
$$;

comment on function public.sync_instagram_account_runtime_settings_after_provisioning(
  uuid,
  text,
  text,
  jsonb
) is
  'After successful login provisioning, sync runtime-safe account settings from the active assignment: assigned app package, Follow ON, Mute posts/stories ON, Like ON, Welcome/Outreach/Unfollow OFF. Does not read or expose credentials.';

revoke execute on function public.sync_instagram_account_runtime_settings_after_provisioning(
  uuid,
  text,
  text,
  jsonb
) from public;

revoke execute on function public.sync_instagram_account_runtime_settings_after_provisioning(
  uuid,
  text,
  text,
  jsonb
) from anon;

revoke execute on function public.sync_instagram_account_runtime_settings_after_provisioning(
  uuid,
  text,
  text,
  jsonb
) from authenticated;

grant execute on function public.sync_instagram_account_runtime_settings_after_provisioning(
  uuid,
  text,
  text,
  jsonb
) to service_role;

create or replace function public.update_client_instagram_account_status(
  p_account_id uuid,
  p_login_status text default null,
  p_provisioning_status text default null,
  p_onboarding_status text default null,
  p_reauth_required boolean default null,
  p_reauth_reason text default null,
  p_actor_type text default 'system',
  p_reason text default null,
  p_external_request_id text default null,
  p_metadata jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor_type text := lower(coalesce(nullif(trim(p_actor_type), ''), 'system'));
  v_reason text := nullif(trim(coalesce(p_reason, '')), '');
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
  v_effective_reauth_required boolean := p_reauth_required;
  v_client_id uuid;
  v_login_status text;
  v_provisioning_status text;
  v_onboarding_status text;
  v_sync_result jsonb;
  v_runtime_settings_sync jsonb := jsonb_build_object(
    'ok', true,
    'applied', false,
    'reason', 'not_connected_ready'
  );
begin
  if p_account_id is null then
    raise exception 'account_id_required'
      using errcode = '22023';
  end if;

  if v_actor_type not in ('client', 'admin', 'assistant', 'ops', 'internal', 'system', 'worker', 'provisioner') then
    raise exception 'invalid_status_actor_type'
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
      'screenshot',
      'device_udid',
      'adb_serial'
    )
  ) then
    raise exception 'metadata contains a forbidden key'
      using errcode = '22023';
  end if;

  select cia.client_id
    into v_client_id
  from public.client_instagram_accounts as cia
  where cia.account_id = p_account_id
  for update;

  if v_client_id is null then
    raise exception 'client_instagram_account_not_found'
      using errcode = 'P0002';
  end if;

  if lower(coalesce(nullif(trim(p_login_status), ''), '')) = 'connected'
     and p_reauth_required is null then
    v_effective_reauth_required := false;
  end if;

  update public.client_instagram_accounts as cia
  set
    login_status = coalesce(nullif(trim(p_login_status), ''), cia.login_status),
    provisioning_status = coalesce(nullif(trim(p_provisioning_status), ''), cia.provisioning_status),
    onboarding_status = coalesce(nullif(trim(p_onboarding_status), ''), cia.onboarding_status),
    updated_at = now()
  where cia.account_id = p_account_id
  returning cia.login_status, cia.provisioning_status, cia.onboarding_status
    into v_login_status, v_provisioning_status, v_onboarding_status;

  if v_effective_reauth_required is not null then
    update public.account_credentials as ac
    set
      reauth_required = v_effective_reauth_required,
      reauth_reason = case
        when v_effective_reauth_required then nullif(trim(coalesce(p_reauth_reason, '')), '')
        else null
      end,
      updated_at = now()
    where ac.account_id = p_account_id
      and ac.provider = 'instagram'
      and ac.status = 'active';
  end if;

  -- When login/provisioning are ready and runtime prerequisites are satisfied,
  -- promote onboarding to ready unless this call explicitly set onboarding_status.
  if nullif(trim(coalesce(p_onboarding_status, '')), '') is null
     and lower(coalesce(v_login_status, '')) = 'connected'
     and lower(coalesce(v_provisioning_status, '')) = 'ready'
     and lower(coalesce(v_onboarding_status, '')) not in ('ready', 'blocked', 'support_required')
     and exists (
       select 1
       from public.account_credentials as ac
       where ac.account_id = p_account_id
         and ac.provider = 'instagram'
         and ac.status = 'active'
         and coalesce(ac.reauth_required, false) = false
     )
     and exists (
       select 1
       from public.account_assignments as aa
       join public.phone_app_instances as pai
         on pai.id = aa.app_instance_id
       where aa.account_id = p_account_id
         and aa.released_at is null
         and aa.status in ('reserved', 'active')
         and nullif(trim(coalesce(pai.package_name, '')), '') is not null
     )
  then
    update public.client_instagram_accounts as cia
    set
      onboarding_status = 'ready',
      updated_at = now()
    where cia.account_id = p_account_id
    returning cia.onboarding_status into v_onboarding_status;
  end if;

  if lower(coalesce(v_login_status, '')) = 'connected'
     and lower(coalesce(v_provisioning_status, '')) = 'ready'
     and lower(coalesce(v_onboarding_status, '')) = 'ready' then
    v_runtime_settings_sync := public.sync_instagram_account_runtime_settings_after_provisioning(
      p_account_id := p_account_id,
      p_actor_type := v_actor_type,
      p_reason := coalesce(v_reason, 'login_provisioning_connected'),
      p_metadata := v_metadata
    );
  end if;

  v_sync_result := public.sync_account_dashboard_actions_from_status(
    p_account_id := p_account_id,
    p_actor_type := v_actor_type,
    p_reason := v_reason,
    p_external_request_id := p_external_request_id,
    p_metadata := v_metadata
  );

  return v_sync_result || jsonb_build_object(
    'ok', true,
    'account_id', p_account_id,
    'login_status', v_login_status,
    'provisioning_status', v_provisioning_status,
    'onboarding_status', v_onboarding_status,
    'runtime_settings_sync', v_runtime_settings_sync
  );
end;
$$;
