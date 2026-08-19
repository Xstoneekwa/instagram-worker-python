-- P0C runtime identity hotfix: exact-lineage request lease renewal.

create or replace function public.renew_account_run_request_lease_v1(
  p_request_id uuid,
  p_worker_id text,
  p_run_id uuid,
  p_lease_seconds integer default 300
)
returns public.account_run_requests
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.account_run_requests;
  v_lease_seconds integer := greatest(60, least(coalesce(p_lease_seconds, 300), 900));
begin
  if nullif(btrim(coalesce(p_worker_id, '')), '') is null then
    raise exception 'account_run_request_lease_worker_missing';
  end if;

  update public.account_run_requests as arr
  set lease_expires_at = now() + make_interval(secs => v_lease_seconds),
      updated_at = now()
  where arr.id = p_request_id
    and arr.run_id = p_run_id
    and arr.claimed_by = p_worker_id
    and arr.status in ('claimed', 'starting', 'running')
  returning arr.* into v_row;

  if v_row.id is null then
    raise exception 'account_run_request_lease_lineage_mismatch';
  end if;
  return v_row;
end;
$$;

revoke all on function public.renew_account_run_request_lease_v1(uuid, text, uuid, integer) from public;
revoke all on function public.renew_account_run_request_lease_v1(uuid, text, uuid, integer) from anon;
revoke all on function public.renew_account_run_request_lease_v1(uuid, text, uuid, integer) from authenticated;
grant execute on function public.renew_account_run_request_lease_v1(uuid, text, uuid, integer) to service_role;

create table if not exists public.follow_candidate_recovery_queue (
  id uuid primary key default gen_random_uuid(),
  recovery_key text not null unique,
  account_id uuid not null references public.ig_accounts(id) on delete cascade,
  candidate_username text not null,
  source_target_id uuid,
  source_ct_username text,
  original_run_id uuid,
  original_request_id uuid,
  business_session_id uuid,
  evidence jsonb not null default '{}'::jsonb,
  status text not null default 'pending'
    check (status in ('pending','claimed','deferred','completed','terminal')),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  claimed_by text,
  lease_expires_at timestamptz,
  outcome text,
  receipt_id uuid,
  next_attempt_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);

create index if not exists follow_candidate_recovery_queue_claim_idx
  on public.follow_candidate_recovery_queue(account_id, status, next_attempt_at, created_at);

alter table public.follow_candidate_recovery_queue enable row level security;
revoke all on table public.follow_candidate_recovery_queue from public, anon, authenticated;
grant all on table public.follow_candidate_recovery_queue to service_role;

create or replace function public.enqueue_follow_candidate_recovery_v1(
  p_recovery_key text,
  p_account_id uuid,
  p_candidate_username text,
  p_source_target_id uuid default null,
  p_source_ct_username text default null,
  p_original_run_id uuid default null,
  p_original_request_id uuid default null,
  p_business_session_id uuid default null,
  p_evidence jsonb default '{}'::jsonb
)
returns public.follow_candidate_recovery_queue
language plpgsql
security definer
set search_path = public
as $$
declare v_row public.follow_candidate_recovery_queue;
begin
  if nullif(btrim(coalesce(p_recovery_key, '')), '') is null
     or nullif(btrim(coalesce(p_candidate_username, '')), '') is null then
    raise exception 'follow_candidate_recovery_identity_missing';
  end if;
  insert into public.follow_candidate_recovery_queue(
    recovery_key, account_id, candidate_username, source_target_id,
    source_ct_username, original_run_id, original_request_id,
    business_session_id, evidence
  ) values (
    p_recovery_key, p_account_id, lower(ltrim(btrim(p_candidate_username), '@')),
    p_source_target_id, lower(ltrim(btrim(coalesce(p_source_ct_username,'')), '@')),
    p_original_run_id, p_original_request_id, p_business_session_id,
    coalesce(p_evidence, '{}'::jsonb)
  )
  on conflict (recovery_key) do update
  set evidence = public.follow_candidate_recovery_queue.evidence || excluded.evidence,
      updated_at = now()
  returning * into v_row;
  return v_row;
end;
$$;

create or replace function public.claim_follow_candidate_recovery_v1(
  p_account_id uuid,
  p_worker_id text,
  p_limit integer default 20
)
returns setof public.follow_candidate_recovery_queue
language plpgsql
security definer
set search_path = public
as $$
begin
  if nullif(btrim(coalesce(p_worker_id, '')), '') is null then
    raise exception 'follow_candidate_recovery_worker_missing';
  end if;
  return query
  with claimable as (
    select q.id
    from public.follow_candidate_recovery_queue q
    where q.account_id = p_account_id
      and q.next_attempt_at <= now()
      and (
        q.status in ('pending','deferred')
        or (q.status = 'claimed' and q.lease_expires_at < now())
      )
    order by q.created_at, q.id
    for update skip locked
    limit greatest(1, least(coalesce(p_limit, 20), 100))
  )
  update public.follow_candidate_recovery_queue q
  set status = 'claimed', claimed_by = p_worker_id,
      lease_expires_at = now() + interval '5 minutes',
      attempt_count = q.attempt_count + 1, updated_at = now()
  from claimable c
  where q.id = c.id
  returning q.*;
end;
$$;

create or replace function public.complete_follow_candidate_recovery_v1(
  p_recovery_id uuid,
  p_worker_id text,
  p_outcome text,
  p_receipt_id uuid default null
)
returns public.follow_candidate_recovery_queue
language plpgsql
security definer
set search_path = public
as $$
declare v_row public.follow_candidate_recovery_queue;
begin
  update public.follow_candidate_recovery_queue q
  set status = case
        when p_outcome in (
          'already_following_unattributed',
          'already_following_external_or_unattributed',
          'follow_requested_after_worker_tap_uncredited'
        ) then 'terminal'
        else 'completed'
      end,
      outcome = p_outcome, receipt_id = p_receipt_id,
      completed_at = now(), updated_at = now(), lease_expires_at = null
  where q.id = p_recovery_id and q.status = 'claimed' and q.claimed_by = p_worker_id
  returning q.* into v_row;
  if v_row.id is null then raise exception 'follow_candidate_recovery_lineage_mismatch'; end if;
  return v_row;
end;
$$;

create or replace function public.defer_follow_candidate_recovery_v1(
  p_recovery_id uuid,
  p_worker_id text,
  p_reason text
)
returns public.follow_candidate_recovery_queue
language plpgsql
security definer
set search_path = public
as $$
declare v_row public.follow_candidate_recovery_queue;
begin
  update public.follow_candidate_recovery_queue q
  set status = 'deferred', outcome = p_reason,
      next_attempt_at = now() + make_interval(mins => least(60, greatest(1, q.attempt_count * 5))),
      claimed_by = null, lease_expires_at = null, updated_at = now()
  where q.id = p_recovery_id and q.status = 'claimed' and q.claimed_by = p_worker_id
  returning q.* into v_row;
  if v_row.id is null then raise exception 'follow_candidate_recovery_lineage_mismatch'; end if;
  return v_row;
end;
$$;

revoke all on function public.claim_follow_candidate_recovery_v1(uuid,text,integer) from public, anon, authenticated;
revoke all on function public.enqueue_follow_candidate_recovery_v1(text,uuid,text,uuid,text,uuid,uuid,uuid,jsonb) from public, anon, authenticated;
revoke all on function public.complete_follow_candidate_recovery_v1(uuid,text,text,uuid) from public, anon, authenticated;
revoke all on function public.defer_follow_candidate_recovery_v1(uuid,text,text) from public, anon, authenticated;
grant execute on function public.claim_follow_candidate_recovery_v1(uuid,text,integer) to service_role;
grant execute on function public.enqueue_follow_candidate_recovery_v1(text,uuid,text,uuid,text,uuid,uuid,uuid,jsonb) to service_role;
grant execute on function public.complete_follow_candidate_recovery_v1(uuid,text,text,uuid) to service_role;
grant execute on function public.defer_follow_candidate_recovery_v1(uuid,text,text) to service_role;
