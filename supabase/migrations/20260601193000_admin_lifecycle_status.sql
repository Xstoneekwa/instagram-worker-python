-- Admin lifecycle status for account operations controls.
--
-- This status is intentionally separate from login/provisioning/onboarding,
-- assignment status, incidents, and runtime run status.
-- Visible Accounts UI values for this step:
-- active, pending, onboarding, paused, cancelled, needs_assistance.
-- Archived remains legacy/internal for existing archive flows and is not exposed
-- in the Client Accounts status action menu in this step.

alter table public.ig_accounts
  add column if not exists admin_lifecycle_status text;

update public.ig_accounts
set admin_lifecycle_status = case
  when lower(coalesce(status, '')) in ('paused') then 'paused'
  when lower(coalesce(status, '')) in ('cancelled', 'canceled') then 'cancelled'
  when lower(coalesce(status, '')) in ('needs_assistance', 'support_required', 'review') then 'needs_assistance'
  else 'active'
end
where admin_lifecycle_status is null;

alter table public.ig_accounts
  alter column admin_lifecycle_status set default 'active';

alter table public.ig_accounts
  alter column admin_lifecycle_status set not null;

alter table public.ig_accounts
  drop constraint if exists ig_accounts_admin_lifecycle_status_check;

alter table public.ig_accounts
  add constraint ig_accounts_admin_lifecycle_status_check
  check (admin_lifecycle_status in (
    'active',
    'paused',
    'cancelled',
    'needs_assistance',
    'pending_cancellation'
  ));

create index if not exists ig_accounts_admin_lifecycle_status_idx
  on public.ig_accounts (admin_lifecycle_status);

comment on column public.ig_accounts.admin_lifecycle_status is
  'Manual admin lifecycle status. Paused and needs_assistance block runtime but keep assignment/app instance/slot. Cancelled may release capacity when safe. Do not mix with login/provisioning/onboarding or runtime stopped.';

create or replace function public.release_schedule_capacity_on_account_admin_lifecycle()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_reason text;
begin
  if old.admin_lifecycle_status is not distinct from new.admin_lifecycle_status then
    return new;
  end if;

  if new.admin_lifecycle_status in ('paused', 'needs_assistance') then
    perform public.audit_schedule_capacity_event(
      'schedule_capacity_release_skipped',
      new.id,
      null,
      null,
      case
        when new.admin_lifecycle_status = 'paused' then 'account_paused_keep_assignment'
        else 'account_needs_assistance_keep_assignment'
      end,
      jsonb_build_object(
        'source', 'ig_accounts_admin_lifecycle_trigger',
        'old_admin_lifecycle_status', old.admin_lifecycle_status,
        'new_admin_lifecycle_status', new.admin_lifecycle_status
      )
    );
    return new;
  end if;

  if new.admin_lifecycle_status not in ('cancelled') then
    return new;
  end if;

  v_reason := 'account_cancelled_release';

  perform public.release_account_schedule_capacity(
    new.id,
    v_reason,
    'ig_accounts_admin_lifecycle_trigger',
    null
  );

  return new;
end;
$$;

drop trigger if exists ig_accounts_release_schedule_capacity_on_status on public.ig_accounts;
drop trigger if exists ig_accounts_release_schedule_capacity_on_admin_lifecycle on public.ig_accounts;
create trigger ig_accounts_release_schedule_capacity_on_admin_lifecycle
  after update of admin_lifecycle_status on public.ig_accounts
  for each row execute function public.release_schedule_capacity_on_account_admin_lifecycle();

revoke all on function public.release_schedule_capacity_on_account_admin_lifecycle() from public;
revoke all on function public.release_schedule_capacity_on_account_admin_lifecycle() from anon;
revoke all on function public.release_schedule_capacity_on_account_admin_lifecycle() from authenticated;
grant execute on function public.release_schedule_capacity_on_account_admin_lifecycle() to service_role;
