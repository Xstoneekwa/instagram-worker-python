-- Entry 2C-2 — lock grants for SECURITY DEFINER auto-assign RPC.
--
-- The auto-assign helper is service-role/admin only. Keep anon/authenticated
-- execute privileges revoked because the function is SECURITY DEFINER.

revoke execute on function public.auto_assign_account_from_subscription(
  uuid,
  timestamptz,
  timestamptz,
  uuid,
  uuid,
  jsonb
) from public;

revoke execute on function public.auto_assign_account_from_subscription(
  uuid,
  timestamptz,
  timestamptz,
  uuid,
  uuid,
  jsonb
) from anon;

revoke execute on function public.auto_assign_account_from_subscription(
  uuid,
  timestamptz,
  timestamptz,
  uuid,
  uuid,
  jsonb
) from authenticated;

grant execute on function public.auto_assign_account_from_subscription(
  uuid,
  timestamptz,
  timestamptz,
  uuid,
  uuid,
  jsonb
) to service_role;
