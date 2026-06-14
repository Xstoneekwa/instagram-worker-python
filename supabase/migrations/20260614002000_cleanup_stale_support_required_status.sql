-- Cleanup stale support_required account statuses.
--
-- Product rule: support_required is not a primary account status. Real support
-- needs are represented by admin_lifecycle_status=needs_assistance, precise
-- login/provisioning statuses, account_dashboard_actions, and incidents.
--
-- This migration only cleans rows that are already operationally ready:
-- active admin lifecycle, connected Instagram login, ready onboarding,
-- active credentials, no open blocking dashboard action, and no active incident.

with stale_ready_accounts as (
  select ia.id
  from public.ig_accounts as ia
  join public.client_instagram_accounts as cia
    on cia.account_id = ia.id
  where lower(coalesce(ia.status, '')) = 'support_required'
    and lower(coalesce(ia.admin_lifecycle_status, 'active')) = 'active'
    and lower(coalesce(cia.login_status, '')) = 'connected'
    and lower(coalesce(cia.provisioning_status, '')) = 'ready'
    and lower(coalesce(cia.onboarding_status, '')) = 'ready'
    and exists (
      select 1
      from public.account_credentials as ac
      where ac.account_id = ia.id
        and ac.provider = 'instagram'
        and lower(coalesce(ac.status, '')) = 'active'
        and nullif(trim(coalesce(ac.secret_ref, '')), '') is not null
    )
    and not exists (
      select 1
      from public.account_dashboard_actions as ada
      where ada.account_id = ia.id
        and ada.status in ('pending', 'acknowledged', 'pending_verification')
    )
    and not exists (
      select 1
      from public.account_incidents as ai
      where ai.account_id = ia.id
        and ai.status in ('open', 'acknowledged')
    )
),
updated_accounts as (
  update public.ig_accounts as ia
  set status = 'active'
  from stale_ready_accounts as stale
  where ia.id = stale.id
  returning ia.id
)
update public.ig_account_settings as ias
set account_status = 'active',
    password = ''
from updated_accounts as updated
where ias.account_id = updated.id
  and lower(coalesce(ias.account_status, '')) = 'support_required';

-- Safe onboarding cleanup for rows that have already reached connected/ready
-- but still carry the old generic onboarding label.
update public.client_instagram_accounts as cia
set onboarding_status = 'ready',
    updated_at = now()
where lower(coalesce(cia.onboarding_status, '')) = 'support_required'
  and lower(coalesce(cia.login_status, '')) = 'connected'
  and lower(coalesce(cia.provisioning_status, '')) = 'ready'
  and not exists (
    select 1
    from public.account_dashboard_actions as ada
    where ada.account_id = cia.account_id
      and ada.status in ('pending', 'acknowledged', 'pending_verification')
  )
  and not exists (
    select 1
    from public.account_incidents as ai
    where ai.account_id = cia.account_id
      and ai.status in ('open', 'acknowledged')
  );
