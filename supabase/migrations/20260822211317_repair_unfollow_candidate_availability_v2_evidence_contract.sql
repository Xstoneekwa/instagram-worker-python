-- A healthy exact absence is evidence, not an immediate terminal outcome.
-- This forward migration preserves the detailed healthy-proof reason while
-- requiring a second independent run after the 24-hour hold to terminalize.

alter table public.ig_unfollow_candidate_availability
  drop constraint if exists ig_unfollow_candidate_availability_reason_check;

alter table public.ig_unfollow_candidate_availability
  add constraint ig_unfollow_candidate_availability_reason_check check (
    reason = any (array[
      'unfollow_candidate_not_found',
      'unfollow_candidate_account_unavailable',
      'unfollow_candidate_possible_username_change',
      'username_not_found_confirmed',
      'username_not_found_confirmed_suggestion_only_stable',
      'username_not_found_confirmed_non_match_only_stable',
      'search_surface_unhealthy',
      'search_results_loading_timeout',
      'search_query_field_missing',
      'search_query_field_mismatch',
      'search_hierarchy_unparseable',
      'multiple_exact_account_rows',
      'open_search_failed_after_bounded_retry',
      'type_search_failed',
      'exact_account_row_tap_failed',
      'profile_identity_unconfirmed',
      'already_not_following_confirmed'
    ]::text[])
  );

create or replace function public.record_unfollow_candidate_availability_v2(
  p_account_id uuid,
  p_normalized_username text,
  p_source_run_id uuid,
  p_classification text,
  p_reason text,
  p_technical_cooldown_minutes integer default 30
) returns jsonb
language plpgsql
security definer
set search_path = ''
as $function$
declare
  v_now timestamptz := clock_timestamp();
  v_business_date date := (v_now at time zone 'Africa/Johannesburg')::date;
  v_username text := lower(btrim(coalesce(p_normalized_username, '')));
  v_classification text := btrim(coalesce(p_classification, ''));
  v_reason text := btrim(coalesce(p_reason, ''));
  v_cooldown_minutes integer := greatest(5, least(coalesce(p_technical_cooldown_minutes, 30), 1440));
  v_existing public.ig_unfollow_candidate_availability%rowtype;
  v_interaction_id uuid;
  v_allowed_confirmed_reasons text[] := array[
    'username_not_found_confirmed',
    'username_not_found_confirmed_suggestion_only_stable',
    'username_not_found_confirmed_non_match_only_stable'
  ];
  v_allowed_technical_reasons text[] := array[
    'search_surface_unhealthy',
    'search_results_loading_timeout',
    'search_query_field_missing',
    'search_query_field_mismatch',
    'search_hierarchy_unparseable',
    'multiple_exact_account_rows',
    'open_search_failed_after_bounded_retry',
    'type_search_failed',
    'exact_account_row_tap_failed',
    'profile_identity_unconfirmed'
  ];
begin
  if coalesce(auth.jwt() ->> 'role', '') <> 'service_role' then
    raise exception 'service_role_required' using errcode = '42501';
  end if;
  if p_account_id is null or p_source_run_id is null then
    raise exception 'unfollow_candidate_availability_identity_required' using errcode = '22023';
  end if;
  if v_username !~ '^[a-z0-9._]{1,30}$'
     or v_username ~ '^\.' or v_username ~ '\.$' or v_username ~ '\.\.' then
    raise exception 'unfollow_candidate_username_invalid' using errcode = '22023';
  end if;
  if v_classification not in ('username_not_found_confirmed', 'search_surface_unhealthy') then
    raise exception 'unfollow_candidate_classification_invalid' using errcode = '22023';
  end if;
  if v_classification = 'username_not_found_confirmed' then
    if not (v_reason = any(v_allowed_confirmed_reasons)) then
      raise exception 'unfollow_candidate_confirmed_reason_invalid' using errcode = '22023';
    end if;
  elsif not (v_reason = any(v_allowed_technical_reasons)) then
    v_reason := 'search_surface_unhealthy';
  end if;
  if not exists (
    select 1 from public.ig_runs r
    where r.id = p_source_run_id and r.account_id = p_account_id
  ) then
    raise exception 'unfollow_candidate_source_run_invalid' using errcode = '22023';
  end if;

  select u.id into v_interaction_id
  from public.ig_interacted_users u
  where u.account_id = p_account_id
    and lower(btrim(u.username)) = v_username
  order by u.followed_at desc nulls last, u.created_at desc, u.id desc
  limit 1;

  select * into v_existing
  from public.ig_unfollow_candidate_availability a
  where a.account_id = p_account_id
    and a.normalized_username = v_username
  for update;

  if v_existing.account_id is not null
     and v_existing.status in ('exhausted', 'username_not_found_confirmed', 'already_not_following_confirmed') then
    return jsonb_build_object(
      'ok', true, 'terminal_preserved', true,
      'account_id', v_existing.account_id,
      'normalized_username', v_existing.normalized_username,
      'status', v_existing.status, 'reason', v_existing.reason,
      'not_found_attempt_count', v_existing.not_found_attempt_count,
      'technical_attempt_count', v_existing.technical_attempt_count,
      'first_not_found_at', v_existing.first_not_found_at,
      'source_run_id', v_existing.source_run_id,
      'next_retry_at', v_existing.next_retry_at,
      'terminal_at', v_existing.terminal_at,
      'business_date_sast', v_existing.business_date_sast
    );
  end if;

  if v_classification = 'username_not_found_confirmed' then
    if v_existing.account_id is null then
      insert into public.ig_unfollow_candidate_availability (
        account_id, normalized_username, interaction_id, status, reason,
        first_not_found_at, last_checked_at, not_found_attempt_count,
        first_failure_at, last_failure_at, technical_attempt_count,
        source_run_id, next_retry_at, terminal_at, business_date_sast,
        created_at, updated_at
      ) values (
        p_account_id, v_username, v_interaction_id, 'temporary_unavailable', v_reason,
        v_now, v_now, 1,
        null, null, 0,
        p_source_run_id, v_now + interval '24 hours', null, v_business_date,
        v_now, v_now
      );
    elsif v_existing.not_found_attempt_count >= 1 then
      if v_existing.source_run_id = p_source_run_id then
        return jsonb_build_object(
          'ok', true, 'idempotent_replay', true,
          'account_id', v_existing.account_id,
          'normalized_username', v_existing.normalized_username,
          'status', v_existing.status, 'reason', v_existing.reason,
          'not_found_attempt_count', v_existing.not_found_attempt_count,
          'technical_attempt_count', v_existing.technical_attempt_count,
          'first_not_found_at', v_existing.first_not_found_at,
          'source_run_id', v_existing.source_run_id,
          'next_retry_at', v_existing.next_retry_at,
          'terminal_at', v_existing.terminal_at,
          'business_date_sast', v_existing.business_date_sast
        );
      end if;
      if v_existing.next_retry_at is null or v_now < v_existing.next_retry_at then
        return jsonb_build_object(
          'ok', true, 'hold_not_elapsed', true,
          'account_id', v_existing.account_id,
          'normalized_username', v_existing.normalized_username,
          'status', v_existing.status, 'reason', v_existing.reason,
          'not_found_attempt_count', v_existing.not_found_attempt_count,
          'technical_attempt_count', v_existing.technical_attempt_count,
          'first_not_found_at', v_existing.first_not_found_at,
          'source_run_id', v_existing.source_run_id,
          'next_retry_at', v_existing.next_retry_at,
          'terminal_at', v_existing.terminal_at,
          'business_date_sast', v_existing.business_date_sast
        );
      end if;
      update public.ig_unfollow_candidate_availability set
        interaction_id = coalesce(v_interaction_id, interaction_id),
        status = 'username_not_found_confirmed',
        reason = v_reason,
        last_checked_at = v_now,
        not_found_attempt_count = least(not_found_attempt_count + 1, 10),
        source_run_id = p_source_run_id,
        next_retry_at = null,
        terminal_at = v_now,
        business_date_sast = v_business_date,
        updated_at = v_now
      where account_id = p_account_id and normalized_username = v_username;
    else
      update public.ig_unfollow_candidate_availability set
        interaction_id = coalesce(v_interaction_id, interaction_id),
        status = 'temporary_unavailable',
        reason = v_reason,
        first_not_found_at = v_now,
        last_checked_at = v_now,
        not_found_attempt_count = 1,
        source_run_id = p_source_run_id,
        next_retry_at = v_now + interval '24 hours',
        terminal_at = null,
        business_date_sast = v_business_date,
        updated_at = v_now
      where account_id = p_account_id and normalized_username = v_username;
    end if;
  else
    if v_existing.account_id is not null and v_existing.not_found_attempt_count >= 1 then
      return jsonb_build_object(
        'ok', true, 'healthy_absence_evidence_preserved', true,
        'account_id', v_existing.account_id,
        'normalized_username', v_existing.normalized_username,
        'status', v_existing.status, 'reason', v_existing.reason,
        'not_found_attempt_count', v_existing.not_found_attempt_count,
        'technical_attempt_count', v_existing.technical_attempt_count,
        'first_not_found_at', v_existing.first_not_found_at,
        'source_run_id', v_existing.source_run_id,
        'next_retry_at', v_existing.next_retry_at,
        'terminal_at', v_existing.terminal_at,
        'business_date_sast', v_existing.business_date_sast
      );
    end if;
    if v_existing.account_id is not null
       and v_existing.source_run_id = p_source_run_id
       and v_existing.status = 'search_surface_unhealthy' then
      return jsonb_build_object(
        'ok', true, 'idempotent_replay', true,
        'account_id', v_existing.account_id,
        'normalized_username', v_existing.normalized_username,
        'status', v_existing.status, 'reason', v_existing.reason,
        'not_found_attempt_count', v_existing.not_found_attempt_count,
        'technical_attempt_count', v_existing.technical_attempt_count,
        'first_not_found_at', v_existing.first_not_found_at,
        'source_run_id', v_existing.source_run_id,
        'next_retry_at', v_existing.next_retry_at,
        'terminal_at', v_existing.terminal_at,
        'business_date_sast', v_existing.business_date_sast
      );
    end if;
    insert into public.ig_unfollow_candidate_availability (
      account_id, normalized_username, interaction_id, status, reason,
      first_not_found_at, last_checked_at, not_found_attempt_count,
      first_failure_at, last_failure_at, technical_attempt_count,
      source_run_id, next_retry_at, terminal_at, business_date_sast,
      created_at, updated_at
    ) values (
      p_account_id, v_username, v_interaction_id, 'search_surface_unhealthy', v_reason,
      null, v_now, 0,
      v_now, v_now, 1,
      p_source_run_id, v_now + make_interval(mins => v_cooldown_minutes), null, v_business_date,
      v_now, v_now
    )
    on conflict (account_id, normalized_username) do update set
      interaction_id = coalesce(excluded.interaction_id, public.ig_unfollow_candidate_availability.interaction_id),
      status = excluded.status,
      reason = excluded.reason,
      last_checked_at = excluded.last_checked_at,
      first_failure_at = coalesce(public.ig_unfollow_candidate_availability.first_failure_at, excluded.first_failure_at),
      last_failure_at = excluded.last_failure_at,
      technical_attempt_count = least(public.ig_unfollow_candidate_availability.technical_attempt_count + 1, 100),
      source_run_id = excluded.source_run_id,
      next_retry_at = excluded.next_retry_at,
      terminal_at = null,
      business_date_sast = excluded.business_date_sast,
      updated_at = excluded.updated_at;
  end if;

  select * into v_existing
  from public.ig_unfollow_candidate_availability a
  where a.account_id = p_account_id and a.normalized_username = v_username;

  return jsonb_build_object(
    'ok', true, 'idempotent_replay', false,
    'account_id', v_existing.account_id,
    'normalized_username', v_existing.normalized_username,
    'status', v_existing.status, 'reason', v_existing.reason,
    'not_found_attempt_count', v_existing.not_found_attempt_count,
    'technical_attempt_count', v_existing.technical_attempt_count,
    'first_not_found_at', v_existing.first_not_found_at,
    'source_run_id', v_existing.source_run_id,
    'next_retry_at', v_existing.next_retry_at,
    'terminal_at', v_existing.terminal_at,
    'business_date_sast', v_existing.business_date_sast
  );
end
$function$;

revoke all on function public.record_unfollow_candidate_availability_v2(uuid, text, uuid, text, text, integer)
  from public, anon, authenticated;
grant execute on function public.record_unfollow_candidate_availability_v2(uuid, text, uuid, text, text, integer)
  to service_role;
