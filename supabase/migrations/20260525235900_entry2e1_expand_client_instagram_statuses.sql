-- Entry 2E-1 - schema-only status model expansion for client Instagram accounts.
--
-- This migration only widens CHECK constraints for login/provisioning/onboarding
-- status fields. It does not change data, add runtime logic, create triggers,
-- modify Edge Functions, read secrets, or touch worker flows.

alter table public.client_instagram_accounts
  drop constraint if exists client_instagram_accounts_login_status_check,
  drop constraint if exists client_instagram_accounts_provisioning_status_check,
  drop constraint if exists client_instagram_accounts_onboarding_status_check;

alter table public.client_instagram_accounts
  add constraint client_instagram_accounts_login_status_check
    check (login_status in (
      'unknown',
      'pending',
      'connected',
      'needs_2fa',
      'checkpoint',
      'failed',
      'mismatch',
      'logged_out',
      'verification_pending'
    )),
  add constraint client_instagram_accounts_provisioning_status_check
    check (provisioning_status in (
      'not_started',
      'pending',
      'provisioning',
      'assigned',
      'login_pending',
      'login_verification_pending',
      'ready',
      'failed',
      'blocked',
      'paused'
    )),
  add constraint client_instagram_accounts_onboarding_status_check
    check (onboarding_status in (
      'pending',
      'incomplete',
      'credentials_required',
      'configured',
      'credentials_submitted',
      'verification_pending',
      'ready',
      'blocked',
      'support_required'
    ));

comment on column public.client_instagram_accounts.login_status is
  'Entry 2E-1 safe Instagram login status for dashboards and future provisioning workers. Never store credentials, secret refs, Vault payloads, tokens, cookies, raw XML, or screenshots here.';

comment on column public.client_instagram_accounts.provisioning_status is
  'Entry 2E-1 safe provisioning/login pipeline status for account setup. Ops/device details remain service-side and must not be exposed directly to clients.';

comment on column public.client_instagram_accounts.onboarding_status is
  'Entry 2E-1 client-visible onboarding state. This field is status only and must never contain password, secret_ref, Vault payload, or device identifiers.';
