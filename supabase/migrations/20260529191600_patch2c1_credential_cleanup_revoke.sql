-- Credential Secure Pipeline Patch 2C-1 - credential cleanup / revoke foundation.
--
-- Backend/schema only. This patch does not modify frontend flows, does not run
-- device/provisioner/login, does not create secure links, and does not migrate
-- legacy ig_account_settings.password values.

-- =============================================================================
-- 1. Service-role-only Vault neutralization helper
-- =============================================================================
create or replace function public.revoke_instagram_credentials_vault_secret(
  p_secret_ref text,
  p_reason text default 'credential_cleanup',
  p_request_id text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, vault
as $$
declare
  v_secret_ref text := btrim(coalesce(p_secret_ref, ''));
  v_reason text := lower(coalesce(nullif(btrim(p_reason), ''), 'credential_cleanup'));
  v_request_id text := nullif(btrim(coalesce(p_request_id, '')), '');
  v_secret_id_text text;
  v_secret_id uuid;
  v_secret_exists boolean := false;
  v_neutral_payload text;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required'
      using errcode = '42501';
  end if;

  if v_reason !~ '^[a-z0-9._:-]{1,120}$' then
    raise exception 'cleanup_reason_invalid'
      using errcode = '22023';
  end if;

  if v_request_id is not null and v_request_id !~ '^[a-zA-Z0-9._:-]{1,120}$' then
    raise exception 'request_id_invalid'
      using errcode = '22023';
  end if;

  if left(v_secret_ref, length('supabase_vault://')) <> 'supabase_vault://' then
    raise exception 'vault_secret_ref_invalid'
      using errcode = '22023';
  end if;

  v_secret_id_text := btrim(substr(v_secret_ref, length('supabase_vault://') + 1));
  if v_secret_id_text !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    raise exception 'vault_secret_ref_invalid'
      using errcode = '22023';
  end if;
  v_secret_id := v_secret_id_text::uuid;

  select exists (
    select 1
    from vault.secrets s
    where s.id = v_secret_id
  )
    into v_secret_exists;

  if not v_secret_exists then
    return jsonb_build_object(
      'ok', true,
      'vault_cleanup_attempted', true,
      'vault_cleanup_status', 'not_found',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
  end if;

  v_neutral_payload := jsonb_strip_nulls(jsonb_build_object(
    'revoked', true,
    'revoked_at', now(),
    'reason', v_reason,
    'request_id', v_request_id,
    'secret_value_removed', true
  ))::text;

  perform vault.update_secret(
    v_secret_id,
    v_neutral_payload,
    'revoked/instagram/credential',
    'Revoked Instagram credential secret; value neutralized by Patch 2C-1.',
    null
  );

  return jsonb_build_object(
    'ok', true,
    'vault_cleanup_attempted', true,
    'vault_cleanup_status', 'neutralized',
    'safe_ref_label', 'supabase_vault://[REDACTED]'
  );
exception
  when others then
    if sqlstate in ('22023', '42501') then
      raise;
    end if;

    return jsonb_build_object(
      'ok', false,
      'vault_cleanup_attempted', true,
      'vault_cleanup_status', 'failed',
      'safe_ref_label', 'supabase_vault://[REDACTED]'
    );
end;
$$;

comment on function public.revoke_instagram_credentials_vault_secret(text, text, text) is
  'Patch 2C-1 service-role-only helper that neutralizes a Supabase Vault Instagram credential secret by replacing its value. Returns only safe status fields.';

-- =============================================================================
-- 2. Service-role-only credential metadata revoke helper
-- =============================================================================
create or replace function public.revoke_instagram_account_credentials(
  p_account_id uuid,
  p_provider text default 'instagram',
  p_reason text default 'credential_cleanup',
  p_request_id text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_provider text := lower(coalesce(nullif(btrim(p_provider), ''), 'instagram'));
  v_reason text := lower(coalesce(nullif(btrim(p_reason), ''), 'credential_cleanup'));
  v_request_id text := nullif(btrim(coalesce(p_request_id, '')), '');
  v_now timestamptz := now();
  v_found_count integer := 0;
  v_revoked_count integer := 0;
  v_already_revoked_count integer := 0;
  v_vault_attempted_count integer := 0;
  v_vault_neutralized_count integer := 0;
  v_vault_not_found_count integer := 0;
  v_vault_failed_count integer := 0;
  v_vault_status text := 'not_applicable';
  v_cleanup jsonb;
  v_secret_ref text;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required'
      using errcode = '42501';
  end if;

  if p_account_id is null then
    raise exception 'account_id_required'
      using errcode = '22023';
  end if;

  if v_provider <> 'instagram' then
    raise exception 'provider_not_supported'
      using errcode = '22023';
  end if;

  if v_reason !~ '^[a-z0-9._:-]{1,120}$' then
    raise exception 'cleanup_reason_invalid'
      using errcode = '22023';
  end if;

  if v_request_id is not null and v_request_id !~ '^[a-zA-Z0-9._:-]{1,120}$' then
    raise exception 'request_id_invalid'
      using errcode = '22023';
  end if;

  select count(*),
         count(*) filter (where status = 'revoked'),
         count(*) filter (where status <> 'revoked')
    into v_found_count, v_already_revoked_count, v_revoked_count
  from public.account_credentials ac
  where ac.account_id = p_account_id
    and ac.provider = v_provider;

  for v_secret_ref in
    select distinct ac.secret_ref
    from public.account_credentials ac
    where ac.account_id = p_account_id
      and ac.provider = v_provider
      and ac.secret_ref is not null
      and ac.secret_ref like 'supabase_vault://%'
  loop
    v_cleanup := public.revoke_instagram_credentials_vault_secret(
      v_secret_ref,
      v_reason,
      v_request_id
    );
    v_vault_attempted_count := v_vault_attempted_count + 1;

    if v_cleanup->>'vault_cleanup_status' = 'neutralized' then
      v_vault_neutralized_count := v_vault_neutralized_count + 1;
    elsif v_cleanup->>'vault_cleanup_status' = 'not_found' then
      v_vault_not_found_count := v_vault_not_found_count + 1;
    else
      v_vault_failed_count := v_vault_failed_count + 1;
    end if;
  end loop;

  update public.account_credentials ac
     set status = 'revoked',
         revoked_at = coalesce(ac.revoked_at, v_now),
         reauth_required = true,
         reauth_reason = v_reason,
         last_updated_at = v_now,
         updated_at = v_now,
         updated_by_actor_type = 'backend',
         source = coalesce(nullif(ac.source, ''), 'unknown'),
         metadata_safe = coalesce(ac.metadata_safe, '{}'::jsonb)
           || jsonb_strip_nulls(jsonb_build_object(
             'action', 'revoke',
             'source', coalesce(nullif(ac.source, ''), 'unknown'),
             'revoke_reason', v_reason,
             'request_id', v_request_id,
             'credential_cleanup', true
           ))
   where ac.account_id = p_account_id
     and ac.provider = v_provider
     and ac.status <> 'revoked';

  if v_vault_attempted_count = 0 then
    v_vault_status := 'not_applicable';
  elsif v_vault_failed_count > 0 then
    v_vault_status := 'partial_failed';
  elsif v_vault_neutralized_count > 0 and v_vault_not_found_count > 0 then
    v_vault_status := 'partial_neutralized';
  elsif v_vault_neutralized_count > 0 then
    v_vault_status := 'neutralized';
  else
    v_vault_status := 'not_found';
  end if;

  return jsonb_build_object(
    'ok', true,
    'account_id', p_account_id,
    'provider', v_provider,
    'credentials_found', v_found_count > 0,
    'credentials_revoked', v_revoked_count,
    'credentials_already_revoked', v_already_revoked_count,
    'vault_cleanup_attempted', v_vault_attempted_count > 0,
    'vault_cleanup_status', v_vault_status,
    'vault_cleanup_attempted_count', v_vault_attempted_count,
    'vault_cleanup_neutralized_count', v_vault_neutralized_count,
    'vault_cleanup_not_found_count', v_vault_not_found_count,
    'vault_cleanup_failed_count', v_vault_failed_count
  );
end;
$$;

comment on function public.revoke_instagram_account_credentials(uuid, text, text, text) is
  'Patch 2C-1 service-role-only helper that revokes Instagram credential metadata and neutralizes linked Vault secrets when possible. Never returns secret_ref or secret values.';

-- =============================================================================
-- 3. Strict service-role-only smoke cleanup helper
-- =============================================================================
create or replace function public.cleanup_instagram_smoke_account(
  p_username text,
  p_request_id text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_username text := lower(regexp_replace(btrim(coalesce(p_username, '')), '^@+', ''));
  v_request_id text := nullif(btrim(coalesce(p_request_id, '')), '');
  v_account_id uuid;
  v_request_match boolean := false;
  v_revoke_result jsonb := '{}'::jsonb;
  v_credential_update_requests_removed integer := 0;
  v_dashboard_actions_removed integer := 0;
  v_credentials_removed integer := 0;
  v_client_links_removed integer := 0;
  v_filters_removed integer := 0;
  v_settings_removed integer := 0;
  v_accounts_removed integer := 0;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required'
      using errcode = '42501';
  end if;

  if v_username !~ '^smoke_[a-z0-9._]{1,24}$' then
    raise exception 'smoke_username_required'
      using errcode = '22023';
  end if;

  if v_request_id is null or v_request_id !~ '^[a-zA-Z0-9._:-]{1,120}$' then
    raise exception 'request_id_required'
      using errcode = '22023';
  end if;

  select ia.id
    into v_account_id
  from public.ig_accounts ia
  where lower(ia.username) = v_username
  order by ia.created_at desc
  limit 1
  for update;

  if v_account_id is null then
    return jsonb_build_object(
      'ok', true,
      'account_removed', false,
      'credentials_removed', 0,
      'credentials_revoked', 0,
      'vault_cleanup_attempted', false,
      'vault_cleanup_status', 'not_applicable',
      'actions_removed', 0,
      'requests_removed', 0,
      'settings_removed', 0,
      'filters_removed', 0
    );
  end if;

  select exists (
    select 1
    from public.account_credentials ac
    where ac.account_id = v_account_id
      and (
        ac.metadata_safe->>'request_id' = v_request_id
        or ac.metadata_safe->>'external_request_id' = v_request_id
        or ac.metadata->>'request_id' = v_request_id
        or ac.metadata->>'external_request_id' = v_request_id
      )
    union all
    select 1
    from public.account_dashboard_actions ada
    where ada.account_id = v_account_id
      and (
        ada.metadata_safe->>'request_id' = v_request_id
        or ada.metadata_safe->>'external_request_id' = v_request_id
        or ada.metadata->>'request_id' = v_request_id
        or ada.metadata->>'external_request_id' = v_request_id
      )
    union all
    select 1
    from public.credential_update_requests cur
    where cur.account_id = v_account_id
      and (
        cur.metadata_safe->>'request_id' = v_request_id
        or cur.metadata_safe->>'external_request_id' = v_request_id
      )
  )
    into v_request_match;

  if not v_request_match then
    raise exception 'smoke_request_id_not_matched'
      using errcode = '22023';
  end if;

  v_revoke_result := public.revoke_instagram_account_credentials(
    v_account_id,
    'instagram',
    'smoke_cleanup',
    v_request_id
  );

  delete from public.credential_update_requests cur
  where cur.account_id = v_account_id;
  get diagnostics v_credential_update_requests_removed = row_count;

  delete from public.account_dashboard_actions ada
  where ada.account_id = v_account_id;
  get diagnostics v_dashboard_actions_removed = row_count;

  delete from public.account_credentials ac
  where ac.account_id = v_account_id;
  get diagnostics v_credentials_removed = row_count;

  delete from public.client_instagram_accounts cia
  where cia.account_id = v_account_id;
  get diagnostics v_client_links_removed = row_count;

  delete from public.ig_account_filters iaf
  where iaf.account_id = v_account_id;
  get diagnostics v_filters_removed = row_count;

  delete from public.ig_account_settings ias
  where ias.account_id = v_account_id;
  get diagnostics v_settings_removed = row_count;

  delete from public.ig_accounts ia
  where ia.id = v_account_id;
  get diagnostics v_accounts_removed = row_count;

  return jsonb_build_object(
    'ok', true,
    'account_removed', v_accounts_removed > 0,
    'credentials_removed', v_credentials_removed,
    'credentials_revoked', coalesce((v_revoke_result->>'credentials_revoked')::integer, 0),
    'vault_cleanup_attempted', coalesce((v_revoke_result->>'vault_cleanup_attempted')::boolean, false),
    'vault_cleanup_status', coalesce(v_revoke_result->>'vault_cleanup_status', 'not_applicable'),
    'actions_removed', v_dashboard_actions_removed,
    'requests_removed', v_credential_update_requests_removed,
    'settings_removed', v_settings_removed,
    'filters_removed', v_filters_removed,
    'client_links_removed', v_client_links_removed
  );
end;
$$;

comment on function public.cleanup_instagram_smoke_account(text, text) is
  'Patch 2C-1 service-role-only smoke cleanup helper. Requires username prefix smoke_ and a matching safe request_id in related metadata before deleting rows.';

-- =============================================================================
-- 4. Grants
-- =============================================================================
revoke execute on function public.revoke_instagram_credentials_vault_secret(text, text, text)
  from public, anon, authenticated;
revoke execute on function public.revoke_instagram_account_credentials(uuid, text, text, text)
  from public, anon, authenticated;
revoke execute on function public.cleanup_instagram_smoke_account(text, text)
  from public, anon, authenticated;

grant execute on function public.revoke_instagram_credentials_vault_secret(text, text, text)
  to service_role;
grant execute on function public.revoke_instagram_account_credentials(uuid, text, text, text)
  to service_role;
grant execute on function public.cleanup_instagram_smoke_account(text, text)
  to service_role;
