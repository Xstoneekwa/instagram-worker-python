-- Entry 2E-2A - status update and dashboard action sync RPCs.
--
-- Schema-only: centralizes safe updates of client_instagram_accounts statuses
-- and derives account_dashboard_actions from the resulting account/credential
-- state. No Edge Function, Python runtime, worker, webhook, Vault read, or
-- device-run integration is introduced here.

create or replace function public.sync_account_dashboard_actions_from_status(
  p_account_id uuid,
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
  v_transition_actor_type text;
  v_reason text := nullif(trim(coalesce(p_reason, '')), '');
  v_external_request_id text := nullif(trim(coalesce(p_external_request_id, '')), '');
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
  v_client_id uuid;
  v_login_status text;
  v_provisioning_status text;
  v_onboarding_status text;
  v_credential_id uuid;
  v_reauth_required boolean := false;
  v_reauth_reason text;
  v_credentials_configured boolean := false;
  v_action public.account_dashboard_actions;
  v_active_action record;
  v_actions_upserted jsonb := '[]'::jsonb;
  v_actions_resolved jsonb := '[]'::jsonb;
  v_sync_metadata jsonb;
begin
  if p_account_id is null then
    raise exception 'account_id_required'
      using errcode = '22023';
  end if;

  if v_actor_type not in ('client', 'admin', 'assistant', 'ops', 'internal', 'system', 'worker', 'provisioner') then
    raise exception 'invalid_status_actor_type'
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

  select cia.client_id, cia.login_status, cia.provisioning_status, cia.onboarding_status
    into v_client_id, v_login_status, v_provisioning_status, v_onboarding_status
  from public.client_instagram_accounts as cia
  where cia.account_id = p_account_id;

  if v_client_id is null then
    raise exception 'client_instagram_account_not_found'
      using errcode = 'P0002';
  end if;

  select ac.id, ac.reauth_required, ac.reauth_reason
    into v_credential_id, v_reauth_required, v_reauth_reason
  from public.account_credentials as ac
  where ac.account_id = p_account_id
    and ac.provider = 'instagram'
    and ac.status = 'active'
  order by ac.credentials_version desc
  limit 1;

  v_credentials_configured := v_credential_id is not null;
  v_reauth_required := coalesce(v_reauth_required, false);

  v_sync_metadata := v_metadata || jsonb_strip_nulls(
    jsonb_build_object(
      'source', 'status_sync',
      'actor_type', v_actor_type,
      'reason', v_reason,
      'external_request_id', v_external_request_id,
      'login_status', v_login_status,
      'provisioning_status', v_provisioning_status,
      'onboarding_status', v_onboarding_status
    )
  );

  if not v_credentials_configured then
    v_action := public.upsert_account_dashboard_action(
      p_account_id := p_account_id,
      p_client_id := v_client_id,
      p_action_type := 'submit_instagram_credentials',
      p_status := 'pending',
      p_severity := 'warning',
      p_audience := 'client',
      p_requires_client_action := true,
      p_blocking_campaign := true,
      p_title := 'Connexion Instagram requise',
      p_safe_client_message := 'Merci de connecter votre compte Instagram pour continuer la campagne.',
      p_action_label := 'Connecter Instagram',
      p_action_deep_link := '/accounts/' || p_account_id::text || '/connect-instagram',
      p_dedupe_key := 'account:' || p_account_id::text || ':dashboard_action:submit_instagram_credentials',
      p_metadata := v_sync_metadata
    );
    v_actions_upserted := v_actions_upserted || jsonb_build_array(
      jsonb_build_object('id', v_action.id, 'action_type', v_action.action_type, 'status', v_action.status)
    );
  elsif v_reauth_required then
    v_action := public.upsert_account_dashboard_action(
      p_account_id := p_account_id,
      p_client_id := v_client_id,
      p_action_type := 'update_instagram_password',
      p_status := 'pending',
      p_severity := 'warning',
      p_audience := 'client',
      p_requires_client_action := true,
      p_blocking_campaign := true,
      p_title := 'Mot de passe Instagram requis',
      p_safe_client_message := 'Merci de mettre à jour votre mot de passe Instagram pour continuer.',
      p_action_label := 'Mettre à jour le mot de passe',
      p_action_deep_link := '/accounts/' || p_account_id::text || '/credentials#password',
      p_dedupe_key := 'account:' || p_account_id::text || ':dashboard_action:update_instagram_password',
      p_metadata := v_sync_metadata
    );
    v_actions_upserted := v_actions_upserted || jsonb_build_array(
      jsonb_build_object('id', v_action.id, 'action_type', v_action.action_type, 'status', v_action.status)
    );
  end if;

  if v_credentials_configured then
    if v_login_status = 'needs_2fa' then
      v_action := public.upsert_account_dashboard_action(
        p_account_id := p_account_id,
        p_client_id := v_client_id,
        p_action_type := 'complete_two_factor',
        p_status := 'pending',
        p_severity := 'warning',
        p_audience := 'client',
        p_requires_client_action := true,
        p_blocking_campaign := true,
        p_title := 'Authentification à deux facteurs requise',
        p_safe_client_message := 'Instagram demande une validation à deux facteurs pour continuer.',
        p_action_label := 'Compléter la 2FA',
        p_action_deep_link := '/accounts/' || p_account_id::text || '/connect-instagram#2fa',
        p_dedupe_key := 'account:' || p_account_id::text || ':dashboard_action:complete_two_factor',
        p_metadata := v_sync_metadata
      );
      v_actions_upserted := v_actions_upserted || jsonb_build_array(
        jsonb_build_object('id', v_action.id, 'action_type', v_action.action_type, 'status', v_action.status)
      );
    elsif v_login_status = 'checkpoint' then
      v_action := public.upsert_account_dashboard_action(
        p_account_id := p_account_id,
        p_client_id := v_client_id,
        p_action_type := 'resolve_checkpoint',
        p_status := 'pending',
        p_severity := 'warning',
        p_audience := 'client',
        p_requires_client_action := true,
        p_blocking_campaign := true,
        p_title := 'Vérification Instagram requise',
        p_safe_client_message := 'Instagram demande une vérification de sécurité pour continuer.',
        p_action_label := 'Résoudre la vérification',
        p_action_deep_link := '/accounts/' || p_account_id::text || '/connect-instagram#checkpoint',
        p_dedupe_key := 'account:' || p_account_id::text || ':dashboard_action:resolve_checkpoint',
        p_metadata := v_sync_metadata
      );
      v_actions_upserted := v_actions_upserted || jsonb_build_array(
        jsonb_build_object('id', v_action.id, 'action_type', v_action.action_type, 'status', v_action.status)
      );
    elsif v_login_status = 'failed' then
      v_action := public.upsert_account_dashboard_action(
        p_account_id := p_account_id,
        p_client_id := v_client_id,
        p_action_type := 'review_login_failure',
        p_status := 'pending',
        p_severity := 'error',
        p_audience := 'client',
        p_requires_client_action := true,
        p_blocking_campaign := true,
        p_title := 'Connexion Instagram échouée',
        p_safe_client_message := 'La connexion Instagram a échoué. Merci de vérifier les informations de connexion.',
        p_action_label := 'Vérifier la connexion',
        p_action_deep_link := '/accounts/' || p_account_id::text || '/connect-instagram',
        p_dedupe_key := 'account:' || p_account_id::text || ':dashboard_action:review_login_failure',
        p_metadata := v_sync_metadata
      );
      v_actions_upserted := v_actions_upserted || jsonb_build_array(
        jsonb_build_object('id', v_action.id, 'action_type', v_action.action_type, 'status', v_action.status)
      );
    elsif v_login_status = 'mismatch' then
      v_action := public.upsert_account_dashboard_action(
        p_account_id := p_account_id,
        p_client_id := v_client_id,
        p_action_type := 'review_account_mismatch',
        p_status := 'pending',
        p_severity := 'critical',
        p_audience := 'admin',
        p_requires_client_action := false,
        p_blocking_campaign := true,
        p_title := 'Compte Instagram incohérent',
        p_safe_client_message := null,
        p_admin_message := 'Le compte connecté ne correspond pas au compte attendu. Vérification admin requise.',
        p_action_label := 'Examiner le compte',
        p_action_deep_link := '/admin/accounts/' || p_account_id::text || '/identity',
        p_dedupe_key := 'account:' || p_account_id::text || ':dashboard_action:review_account_mismatch',
        p_metadata := v_sync_metadata
      );
      v_actions_upserted := v_actions_upserted || jsonb_build_array(
        jsonb_build_object('id', v_action.id, 'action_type', v_action.action_type, 'status', v_action.status)
      );
    elsif v_login_status = 'logged_out' then
      v_action := public.upsert_account_dashboard_action(
        p_account_id := p_account_id,
        p_client_id := v_client_id,
        p_action_type := 'reconnect_instagram',
        p_status := 'pending',
        p_severity := 'warning',
        p_audience := 'client',
        p_requires_client_action := true,
        p_blocking_campaign := true,
        p_title := 'Reconnexion Instagram requise',
        p_safe_client_message := 'Votre compte Instagram semble déconnecté. Merci de reconnecter votre compte.',
        p_action_label := 'Reconnecter Instagram',
        p_action_deep_link := '/accounts/' || p_account_id::text || '/connect-instagram',
        p_dedupe_key := 'account:' || p_account_id::text || ':dashboard_action:reconnect_instagram',
        p_metadata := v_sync_metadata
      );
      v_actions_upserted := v_actions_upserted || jsonb_build_array(
        jsonb_build_object('id', v_action.id, 'action_type', v_action.action_type, 'status', v_action.status)
      );
    elsif v_login_status = 'connected' and not v_reauth_required then
      for v_active_action in
        select ada.id, ada.action_type
        from public.account_dashboard_actions as ada
        where ada.account_id = p_account_id
          and ada.status in ('pending', 'acknowledged', 'pending_verification')
          and ada.action_type in (
            'submit_instagram_credentials',
            'update_instagram_password',
            'reconnect_instagram',
            'complete_two_factor',
            'resolve_checkpoint',
            'review_login_failure'
          )
        order by ada.created_at asc
      loop
        v_action := public.transition_account_dashboard_action(
          v_active_action.id,
          'resolved',
          v_transition_actor_type,
          null,
          coalesce(v_reason, 'login_connected'),
          v_sync_metadata
        );
        v_actions_resolved := v_actions_resolved || jsonb_build_array(
          jsonb_build_object('id', v_action.id, 'action_type', v_action.action_type, 'status', v_action.status)
        );
      end loop;
    elsif v_login_status in ('verification_pending')
          or v_provisioning_status = 'login_verification_pending' then
      -- Generic verification only: keep existing credential actions; do not
      -- create new 2FA/checkpoint/login-failure actions from status sync alone.
      null;
    end if;
  end if;

  return jsonb_build_object(
    'ok', true,
    'account_id', p_account_id,
    'login_status', v_login_status,
    'provisioning_status', v_provisioning_status,
    'onboarding_status', v_onboarding_status,
    'credentials_configured', v_credentials_configured,
    'reauth_required', v_reauth_required,
    'reauth_reason', v_reauth_reason,
    'actions_upserted', v_actions_upserted,
    'actions_resolved', v_actions_resolved
  );
end;
$$;

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
    'onboarding_status', v_onboarding_status
  );
end;
$$;

comment on function public.sync_account_dashboard_actions_from_status(
  uuid,
  text,
  text,
  text,
  jsonb
) is
  'Entry 2E-2A service-role RPC that derives safe account_dashboard_actions from client_instagram_accounts and active account_credentials status. Never reads Vault or returns secret_ref.';

comment on function public.update_client_instagram_account_status(
  uuid,
  text,
  text,
  text,
  boolean,
  text,
  text,
  text,
  text,
  jsonb
) is
  'Entry 2E-2A service-role RPC that updates safe Instagram account statuses, optionally clears/sets active credential reauth state, then syncs dashboard actions.';

revoke execute on function public.sync_account_dashboard_actions_from_status(
  uuid,
  text,
  text,
  text,
  jsonb
) from public;
revoke execute on function public.sync_account_dashboard_actions_from_status(
  uuid,
  text,
  text,
  text,
  jsonb
) from anon;
revoke execute on function public.sync_account_dashboard_actions_from_status(
  uuid,
  text,
  text,
  text,
  jsonb
) from authenticated;

revoke execute on function public.update_client_instagram_account_status(
  uuid,
  text,
  text,
  text,
  boolean,
  text,
  text,
  text,
  text,
  jsonb
) from public;
revoke execute on function public.update_client_instagram_account_status(
  uuid,
  text,
  text,
  text,
  boolean,
  text,
  text,
  text,
  text,
  jsonb
) from anon;
revoke execute on function public.update_client_instagram_account_status(
  uuid,
  text,
  text,
  text,
  boolean,
  text,
  text,
  text,
  text,
  jsonb
) from authenticated;

grant execute on function public.sync_account_dashboard_actions_from_status(
  uuid,
  text,
  text,
  text,
  jsonb
) to service_role;

grant execute on function public.update_client_instagram_account_status(
  uuid,
  text,
  text,
  text,
  boolean,
  text,
  text,
  text,
  text,
  jsonb
) to service_role;
