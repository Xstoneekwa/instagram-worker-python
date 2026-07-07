-- P3: canonical per-run resume plan, created EARLY (before any real UI action).
--
-- One row per account_session run. Written by the runner as soon as the run
-- safely knows: account, assignment, device, clone/app instance, package and
-- session window. Updated by the dispatcher on terminal failure (true reason
-- preserved) and by the orchestrator at end of session (restart verdict).
--
-- The atomic "human resume authorization" consumption state is NOT stored
-- here (see incident_resume_authorizations, backend-owned): this table is the
-- recovery contract of one run, not the lockable authorization.

create table if not exists public.account_session_resume_plans (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null unique,
  run_request_id uuid,
  account_id uuid not null,
  assignment_id uuid,
  device_id uuid,
  app_instance_id uuid,
  expected_username text,
  expected_package text,
  scheduled_window_start timestamptz,
  scheduled_window_end timestamptz,
  resume_window_key text,
  source_surface text,
  run_trigger text,
  -- Stage the session can safely resume from: preflight | phases | completed.
  resume_stage text not null default 'preflight'
    check (resume_stage in ('preflight', 'phases', 'completed')),
  -- Recovery state machine of this run's plan.
  resume_state text not null default 'run_active'
    check (resume_state in (
      'run_active',
      'awaiting_human_resume_authorization',
      'resume_requested',
      'resume_succeeded',
      'not_recoverable',
      'completed'
    )),
  restart_allowed boolean not null default false,
  restart_block_reason text not null default 'run_in_progress',
  terminal_reason_code text,
  incident_id uuid,
  attempts_in_window integer not null default 0,
  plan jsonb not null default '{}'::jsonb,
  test boolean not null default false,
  created_at timestamptz not null default now(),
  last_updated_at timestamptz not null default now()
);

create index if not exists account_session_resume_plans_account_idx
  on public.account_session_resume_plans (account_id, last_updated_at desc);

create index if not exists account_session_resume_plans_window_idx
  on public.account_session_resume_plans (resume_window_key)
  where resume_window_key is not null;

alter table public.account_session_resume_plans enable row level security;

drop policy if exists account_session_resume_plans_service_role_all
  on public.account_session_resume_plans;
create policy account_session_resume_plans_service_role_all
  on public.account_session_resume_plans
  for all
  to service_role
  using (true)
  with check (true);
