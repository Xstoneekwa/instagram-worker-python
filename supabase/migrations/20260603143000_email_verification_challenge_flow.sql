-- Email verification challenge flow: ephemeral code storage, submit/consume RPCs,
-- dashboard action status extension, and login challenge dashboard sync.

create extension if not exists pgcrypto;

-- =============================================================================
-- 1. Extend dashboard action statuses
-- =============================================================================
alter table public.account_dashboard_actions
  drop constraint if exists account_dashboard_actions_status_check;

alter table public.account_dashboard_actions
  add constraint account_dashboard_actions_status_check
  check (status in (
    'pending',
    'acknowledged',
    'pending_verification',
    'code_submitted',
    'resolved',
    'dismissed',
    'ignored'
  ));

drop index if exists public.account_dashboard_actions_active_dedupe_key;
create unique index account_dashboard_actions_active_dedupe_key
  on public.account_dashboard_actions (dedupe_key)
  where status in (
    'pending',
    'acknowledged',
    'pending_verification',
    'code_submitted'
  );

-- =============================================================================
-- 2. Ephemeral verification code submissions
-- =============================================================================
create table if not exists public.account_verification_code_submissions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  action_id uuid not null references public.account_dashboard_actions (id) on delete cascade,
  account_id uuid not null references public.ig_accounts (id) on delete cascade,

  status text not null default 'pending_submission',
  secret_ref text,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  consumed_run_id text,
  attempts_count integer not null default 0,
  max_attempts integer not null default 3,
  metadata_safe jsonb not null default '{}'::jsonb,

  constraint account_verification_code_submissions_status_check
    check (status in (
      'pending_submission',
      'code_submitted',
      'ready_for_resume',
      'consumed',
      'expired',
      'failed'
    )),
  constraint account_verification_code_submissions_attempts_nonneg
    check (attempts_count >= 0),
  constraint account_verification_code_submissions_max_attempts_positive
    check (max_attempts >= 1),
  constraint account_verification_code_submissions_metadata_object_check
    check (jsonb_typeof(metadata_safe) = 'object')
);

create index if not exists account_verification_code_submissions_action_status_idx
  on public.account_verification_code_submissions (action_id, status);

create index if not exists account_verification_code_submissions_account_status_idx
  on public.account_verification_code_submissions (account_id, status);

create index if not exists account_verification_code_submissions_expires_at_idx
  on public.account_verification_code_submissions (expires_at)
  where status in ('code_submitted', 'ready_for_resume');

drop trigger if exists account_verification_code_submissions_set_updated_at
  on public.account_verification_code_submissions;
create trigger account_verification_code_submissions_set_updated_at
  before update on public.account_verification_code_submissions
  for each row execute function public.set_updated_at();

alter table public.account_verification_code_submissions enable row level security;

do $$
begin
  if not exists (
    select 1
    from pg_policies
    where schemaname = 'public'
      and tablename = 'account_verification_code_submissions'
      and policyname = 'account_verification_code_submissions_service_role_all'
  ) then
    create policy account_verification_code_submissions_service_role_all
      on public.account_verification_code_submissions
      for all
      to service_role
      using (true)
      with check (true);
  end if;
end $$;

revoke all on table public.account_verification_code_submissions from public, anon, authenticated;
grant select, insert, update, delete on table public.account_verification_code_submissions to service_role;

-- =============================================================================
-- 3. Vault helpers for ephemeral verification codes
-- =============================================================================
create or replace function public.create_ephemeral_verification_code_vault_secret(
  p_account_id uuid,
  p_action_id uuid,
  p_verification_code text
)
returns uuid
language plpgsql
volatile
security definer
set search_path = public, vault
as $$
declare
  v_code text := btrim(coalesce(p_verification_code, ''));
  v_secret_id uuid;
  v_name text;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required';
  end if;

  if p_account_id is null or p_action_id is null then
    raise exception 'account_id_and_action_id_required';
  end if;

  if v_code = '' or char_length(v_code) > 32 then
    raise exception 'verification_code_invalid';
  end if;

  if v_code !~ '^[A-Za-z0-9-]{4,32}$' then
    raise exception 'verification_code_format_invalid';
  end if;

  v_name := format(
    'phonefarm/instagram/%s/verification-code/%s/%s',
    p_account_id::text,
    p_action_id::text,
    encode(extensions.gen_random_bytes(6), 'hex')
  );

  v_secret_id := vault.create_secret(
    jsonb_build_object(
      'kind', 'instagram_email_verification_code',
      'account_id', p_account_id::text,
      'action_id', p_action_id::text,
      'verification_code', v_code
    )::text,
    v_name,
    'Ephemeral Instagram email verification code. Single-use, short TTL.'
  );

  return v_secret_id;
end;
$$;

comment on function public.create_ephemeral_verification_code_vault_secret(uuid, uuid, text) is
  'Service-role-only helper that stores a short-lived Instagram email verification code in Vault. Never log inputs or outputs.';

revoke execute on function public.create_ephemeral_verification_code_vault_secret(uuid, uuid, text)
  from public, anon, authenticated;
grant execute on function public.create_ephemeral_verification_code_vault_secret(uuid, uuid, text)
  to service_role;

create or replace function public.neutralize_ephemeral_verification_code_vault_secret(
  p_secret_ref text
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public, vault
as $$
declare
  v_secret_ref text := btrim(coalesce(p_secret_ref, ''));
  v_secret_id_text text;
  v_secret_id uuid;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required';
  end if;

  if v_secret_ref = '' or left(v_secret_ref, length('supabase_vault://')) <> 'supabase_vault://' then
    return jsonb_build_object('ok', false, 'reason', 'invalid_secret_ref');
  end if;

  v_secret_id_text := btrim(substr(v_secret_ref, length('supabase_vault://') + 1));
  if v_secret_id_text !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    return jsonb_build_object('ok', false, 'reason', 'vault_secret_id_invalid');
  end if;

  v_secret_id := v_secret_id_text::uuid;

  perform vault.update_secret(
    v_secret_id,
    jsonb_build_object('neutralized', true)::text,
    format('neutralized/instagram/verification-code/%s', v_secret_id::text),
    'Ephemeral Instagram verification code neutralized after consumption.'
  );

  return jsonb_build_object('ok', true, 'reason', 'neutralized');
exception
  when others then
    return jsonb_build_object('ok', false, 'reason', 'vault_neutralization_failed');
end;
$$;

revoke execute on function public.neutralize_ephemeral_verification_code_vault_secret(text)
  from public, anon, authenticated;
grant execute on function public.neutralize_ephemeral_verification_code_vault_secret(text)
  to service_role;

-- =============================================================================
-- 4. Submit verification code (dashboard/API)
-- =============================================================================
create or replace function public.submit_account_verification_code(
  p_action_id uuid,
  p_account_id uuid,
  p_verification_code text,
  p_actor_type text default 'client',
  p_actor_id text default null,
  p_metadata jsonb default '{}'::jsonb,
  p_ttl_minutes integer default 15
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor_type text := lower(coalesce(nullif(trim(p_actor_type), ''), 'client'));
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
  v_code text := btrim(coalesce(p_verification_code, ''));
  v_action public.account_dashboard_actions%rowtype;
  v_submission public.account_verification_code_submissions%rowtype;
  v_secret_id uuid;
  v_secret_ref text;
  v_expires_at timestamptz;
  v_now timestamptz := now();
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required';
  end if;

  if p_action_id is null or p_account_id is null then
    raise exception 'action_id_and_account_id_required';
  end if;

  if v_code = '' or char_length(v_code) > 32 or v_code !~ '^[A-Za-z0-9-]{4,32}$' then
    raise exception 'verification_code_invalid';
  end if;

  if jsonb_typeof(v_metadata) is distinct from 'object'
     or public.jsonb_has_forbidden_safe_metadata_key(v_metadata) then
    raise exception 'metadata_forbidden';
  end if;

  select ada.*
    into v_action
  from public.account_dashboard_actions as ada
  where ada.id = p_action_id
    and ada.account_id = p_account_id
  for update;

  if v_action.id is null then
    raise exception 'dashboard_action_not_found';
  end if;

  if v_action.action_type <> 'enter_email_verification_code' then
    raise exception 'dashboard_action_type_invalid';
  end if;

  if v_action.status not in ('pending', 'acknowledged', 'pending_verification', 'code_submitted') then
    raise exception 'dashboard_action_not_active';
  end if;

  v_expires_at := v_now + make_interval(mins => greatest(5, least(coalesce(p_ttl_minutes, 15), 60)));

  v_secret_id := public.create_ephemeral_verification_code_vault_secret(
    p_account_id,
    p_action_id,
    v_code
  );
  v_secret_ref := format('supabase_vault://%s', v_secret_id::text);

  select avcs.*
    into v_submission
  from public.account_verification_code_submissions as avcs
  where avcs.action_id = p_action_id
  order by avcs.created_at desc
  limit 1
  for update;

  if v_submission.id is not null and v_submission.status = 'consumed' then
    raise exception 'verification_code_already_consumed';
  end if;

  if v_submission.id is null then
    insert into public.account_verification_code_submissions (
      action_id,
      account_id,
      status,
      secret_ref,
      expires_at,
      metadata_safe
    )
    values (
      p_action_id,
      p_account_id,
      'code_submitted',
      v_secret_ref,
      v_expires_at,
      v_metadata || jsonb_build_object(
        'source', 'submit_account_verification_code',
        'actor_type', v_actor_type,
        'actor_id', nullif(trim(coalesce(p_actor_id, '')), '')
      )
    )
    returning * into v_submission;
  else
    if v_submission.secret_ref is not null then
      perform public.neutralize_ephemeral_verification_code_vault_secret(v_submission.secret_ref);
    end if;

    update public.account_verification_code_submissions as avcs
    set
      updated_at = v_now,
      status = 'code_submitted',
      secret_ref = v_secret_ref,
      expires_at = v_expires_at,
      attempts_count = avcs.attempts_count + 1,
      metadata_safe = coalesce(avcs.metadata_safe, '{}'::jsonb) || v_metadata
    where avcs.id = v_submission.id
    returning * into v_submission;
  end if;

  update public.account_dashboard_actions as ada
  set
    updated_at = v_now,
    status = 'code_submitted',
    metadata = coalesce(ada.metadata, '{}'::jsonb) || jsonb_strip_nulls(
      jsonb_build_object(
        'verification_code_submitted_at', v_now,
        'verification_code_expires_at', v_expires_at,
        'verification_submission_id', v_submission.id::text
      )
    )
  where ada.id = p_action_id;

  return jsonb_build_object(
    'ok', true,
    'action_id', p_action_id,
    'account_id', p_account_id,
    'status', 'code_submitted',
    'submission_id', v_submission.id,
    'expires_at', v_expires_at,
    'message', 'Verification code stored securely and ready for worker resume.'
  );
end;
$$;

comment on function public.submit_account_verification_code(uuid, uuid, text, text, text, jsonb, integer) is
  'Service-role-only RPC to store an ephemeral Instagram email verification code linked to a dashboard action. Never returns the code.';

revoke execute on function public.submit_account_verification_code(uuid, uuid, text, text, text, jsonb, integer)
  from public, anon, authenticated;
grant execute on function public.submit_account_verification_code(uuid, uuid, text, text, text, jsonb, integer)
  to service_role;

-- =============================================================================
-- 5. Consume verification code (worker resume)
-- =============================================================================
create or replace function public.consume_account_verification_code_for_worker(
  p_action_id uuid default null,
  p_account_id uuid default null,
  p_run_id text default null,
  p_metadata jsonb default '{}'::jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public, vault
as $$
declare
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
  v_submission public.account_verification_code_submissions%rowtype;
  v_secret_id_text text;
  v_secret_id uuid;
  v_secret_value text;
  v_payload jsonb;
  v_code text;
  v_now timestamptz := now();
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required';
  end if;

  if p_action_id is null and p_account_id is null then
    raise exception 'action_id_or_account_id_required';
  end if;

  if jsonb_typeof(v_metadata) is distinct from 'object'
     or public.jsonb_has_forbidden_safe_metadata_key(v_metadata) then
    raise exception 'metadata_forbidden';
  end if;

  select avcs.*
    into v_submission
  from public.account_verification_code_submissions as avcs
  where avcs.status in ('code_submitted', 'ready_for_resume')
    and avcs.expires_at > v_now
    and (p_action_id is null or avcs.action_id = p_action_id)
    and (p_account_id is null or avcs.account_id = p_account_id)
  order by avcs.updated_at desc
  for update skip locked
  limit 1;

  if v_submission.id is null then
    return jsonb_build_object(
      'ok', false,
      'reason', 'verification_code_not_available',
      'action_id', p_action_id,
      'account_id', p_account_id
    );
  end if;

  if v_submission.attempts_count >= v_submission.max_attempts then
    update public.account_verification_code_submissions
    set status = 'failed', updated_at = v_now
    where id = v_submission.id;
    return jsonb_build_object(
      'ok', false,
      'reason', 'verification_code_attempts_exhausted',
      'action_id', v_submission.action_id,
      'account_id', v_submission.account_id
    );
  end if;

  if coalesce(v_submission.secret_ref, '') = '' then
    return jsonb_build_object(
      'ok', false,
      'reason', 'verification_code_secret_missing',
      'action_id', v_submission.action_id,
      'account_id', v_submission.account_id
    );
  end if;

  v_secret_id_text := btrim(substr(v_submission.secret_ref, length('supabase_vault://') + 1));
  v_secret_id := v_secret_id_text::uuid;

  select ds.decrypted_secret
    into v_secret_value
  from vault.decrypted_secrets ds
  where ds.id = v_secret_id
  limit 1;

  if coalesce(v_secret_value, '') = '' then
    return jsonb_build_object(
      'ok', false,
      'reason', 'verification_code_secret_empty',
      'action_id', v_submission.action_id,
      'account_id', v_submission.account_id
    );
  end if;

  begin
    v_payload := v_secret_value::jsonb;
    v_code := btrim(coalesce(v_payload ->> 'verification_code', ''));
  exception
    when others then
      v_code := btrim(v_secret_value);
  end;

  if v_code = '' then
    return jsonb_build_object(
      'ok', false,
      'reason', 'verification_code_payload_invalid',
      'action_id', v_submission.action_id,
      'account_id', v_submission.account_id
    );
  end if;

  perform public.neutralize_ephemeral_verification_code_vault_secret(v_submission.secret_ref);

  update public.account_verification_code_submissions
  set
    updated_at = v_now,
    status = 'consumed',
    consumed_at = v_now,
    consumed_run_id = nullif(trim(coalesce(p_run_id, '')), ''),
    secret_ref = null,
    metadata_safe = coalesce(metadata_safe, '{}'::jsonb) || v_metadata
  where id = v_submission.id;

  return jsonb_build_object(
    'ok', true,
    'reason', 'consumed',
    'action_id', v_submission.action_id,
    'account_id', v_submission.account_id,
    'submission_id', v_submission.id,
    'verification_code', v_code
  );
end;
$$;

comment on function public.consume_account_verification_code_for_worker(uuid, uuid, text, jsonb) is
  'Service-role-only RPC that reads and neutralizes one ephemeral verification code for worker resume. Callers must never log the response.';

revoke execute on function public.consume_account_verification_code_for_worker(uuid, uuid, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.consume_account_verification_code_for_worker(uuid, uuid, text, jsonb)
  to service_role;

-- =============================================================================
-- 6. Login challenge dashboard action upsert helper
-- =============================================================================
create or replace function public.upsert_login_challenge_dashboard_action(
  p_account_id uuid,
  p_action_type text,
  p_title text,
  p_dedupe_key text,
  p_client_id uuid default null,
  p_status text default 'pending',
  p_severity text default 'warning',
  p_audience text default 'client',
  p_requires_client_action boolean default true,
  p_blocking_campaign boolean default true,
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
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role_required';
  end if;

  if v_action_type not in ('enter_email_verification_code', 'review_login_challenge') then
    raise exception 'unsupported_login_challenge_action_type';
  end if;

  return public.upsert_account_dashboard_action(
    p_account_id := p_account_id,
    p_client_id := p_client_id,
    p_action_type := v_action_type,
    p_status := coalesce(nullif(trim(p_status), ''), 'pending'),
    p_severity := coalesce(nullif(trim(p_severity), ''), 'warning'),
    p_audience := coalesce(nullif(trim(p_audience), ''), 'client'),
    p_requires_client_action := coalesce(p_requires_client_action, true),
    p_blocking_campaign := coalesce(p_blocking_campaign, true),
    p_title := p_title,
    p_safe_client_message := p_safe_client_message,
    p_assistant_message := p_assistant_message,
    p_admin_message := p_admin_message,
    p_action_label := p_action_label,
    p_action_deep_link := p_action_deep_link,
    p_dedupe_key := p_dedupe_key,
    p_metadata := coalesce(p_metadata, '{}'::jsonb)
  );
end;
$$;

revoke execute on function public.upsert_login_challenge_dashboard_action(
  uuid, text, text, text, uuid, text, text, text, boolean, boolean,
  text, text, text, text, text, jsonb
) from public, anon, authenticated;
grant execute on function public.upsert_login_challenge_dashboard_action(
  uuid, text, text, text, uuid, text, text, text, boolean, boolean,
  text, text, text, text, text, jsonb
) to service_role;
