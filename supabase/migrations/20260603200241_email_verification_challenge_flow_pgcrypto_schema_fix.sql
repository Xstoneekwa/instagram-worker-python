-- Email verification challenge flow - pgcrypto schema qualification fix.
--
-- The remote project exposes pgcrypto from the `extensions` schema while the
-- Vault helper runs with a restricted search_path. Keep randomness
-- schema-qualified so the helper works in production.

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
  'Service-role-only helper that stores a short-lived Instagram email verification code in Vault. Uses schema-qualified pgcrypto randomness.';
