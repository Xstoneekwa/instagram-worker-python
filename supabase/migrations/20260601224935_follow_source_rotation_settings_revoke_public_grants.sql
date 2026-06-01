revoke all on table public.account_follow_source_settings from anon;
revoke all on table public.account_follow_source_settings from authenticated;
grant all on table public.account_follow_source_settings to service_role;
