-- Patch 2C-3: Add Profile idempotency guard.
-- Prevent double-submit/retry from creating multiple account rows for the same
-- Instagram username. Credentials remain handled by the existing single
-- account_credentials active-row invariant.

do $$
begin
  if exists (
    select 1
    from public.ig_accounts
    where btrim(username) <> ''
    group by lower(btrim(username))
    having count(*) > 1
  ) then
    raise exception 'duplicate_ig_account_usernames_block_unique_index'
      using errcode = '23505';
  end if;
end;
$$;

create unique index if not exists ig_accounts_username_lower_unique
  on public.ig_accounts (lower(btrim(username)))
  where btrim(username) <> '';

comment on index public.ig_accounts_username_lower_unique is
  'Patch 2C-3 Add Profile idempotency guard: one ig_accounts row per normalized Instagram username.';
