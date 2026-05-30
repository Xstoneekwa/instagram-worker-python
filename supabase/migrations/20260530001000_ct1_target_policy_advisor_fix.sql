-- CT-1 - Advisor cleanup for target RLS policies and updated_at trigger.
--
-- Keeps service-role-only access while avoiding per-row auth initplan warnings.

create or replace function public.set_ig_targets_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop policy if exists ig_targets_service_role_all on public.ig_targets;
create policy ig_targets_service_role_all
  on public.ig_targets
  for all
  using ((select auth.role()) = 'service_role')
  with check ((select auth.role()) = 'service_role');

drop policy if exists ct_target_audit_events_service_role_all on public.ct_target_audit_events;
create policy ct_target_audit_events_service_role_all
  on public.ct_target_audit_events
  for all
  using ((select auth.role()) = 'service_role')
  with check ((select auth.role()) = 'service_role');
