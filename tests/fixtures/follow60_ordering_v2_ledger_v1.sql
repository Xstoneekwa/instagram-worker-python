-- TEST-ONLY PostgreSQL contract for FOLLOW60_ORDERING_V2.
-- Never register or apply this file as a production migration.

drop schema if exists follow60_ordering_v2_test cascade;
create schema follow60_ordering_v2_test;
revoke all on schema follow60_ordering_v2_test from public;

create table follow60_ordering_v2_test.ledger (
    ledger_id bigint generated always as identity primary key,
    account_id text not null,
    run_id text not null,
    request_id text not null,
    business_session_id text not null,
    target_id text not null,
    action_id text not null,
    candidate_username text not null,
    ordering_version text not null check (ordering_version = 'FOLLOW60_ORDERING_V2'),
    profile_certified boolean not null default false,
    post_opened boolean not null default false,
    like_verified boolean not null default false,
    like_skipped boolean not null default false,
    profile_reentry_verified boolean not null default false,
    follow_pending boolean not null default false,
    follow_verified boolean not null default false,
    follow_failed boolean not null default false,
    mute_posts_verified boolean not null default false,
    mute_stories_verified boolean not null default false,
    return_ct_exact boolean not null default false,
    stop_recorded boolean not null default false,
    cycle_complete boolean not null default false,
    created_at timestamptz not null default clock_timestamp(),
    updated_at timestamptz not null default clock_timestamp(),
    unique (
        account_id, run_id, request_id, business_session_id, target_id,
        action_id, candidate_username, ordering_version
    ),
    check (not (like_verified and like_skipped)),
    check (not (follow_verified and follow_failed)),
    check (not cycle_complete or (
        profile_certified and post_opened and (like_verified or like_skipped)
        and profile_reentry_verified and follow_verified
        and mute_posts_verified and mute_stories_verified and return_ct_exact
        and not follow_failed
    ))
);

create table follow60_ordering_v2_test.receipts (
    receipt_id bigint generated always as identity primary key,
    ledger_id bigint not null references follow60_ordering_v2_test.ledger(ledger_id),
    action_type text not null,
    payload_hash text not null,
    payload jsonb not null,
    created_at timestamptz not null default clock_timestamp(),
    unique (ledger_id, action_type)
);

create table follow60_ordering_v2_test.outbox (
    outbox_id bigint generated always as identity primary key,
    receipt_id bigint not null unique references follow60_ordering_v2_test.receipts(receipt_id),
    status text not null default 'pending' check (status in ('pending', 'acked')),
    created_at timestamptz not null default clock_timestamp(),
    acked_at timestamptz
);

create or replace function follow60_ordering_v2_test.apply_receipt(
    p_account_id text,
    p_run_id text,
    p_request_id text,
    p_business_session_id text,
    p_target_id text,
    p_action_id text,
    p_candidate_username text,
    p_ordering_version text,
    p_action_type text,
    p_payload jsonb
) returns jsonb
language plpgsql
as $$
declare
    v_ledger follow60_ordering_v2_test.ledger%rowtype;
    v_receipt_id bigint;
    v_existing_hash text;
    v_payload_hash text := md5(coalesce(p_payload, '{}'::jsonb)::text);
    v_inserted boolean := false;
begin
    if nullif(btrim(p_account_id), '') is null
       or nullif(btrim(p_run_id), '') is null
       or nullif(btrim(p_request_id), '') is null
       or nullif(btrim(p_business_session_id), '') is null
       or nullif(btrim(p_target_id), '') is null
       or nullif(btrim(p_action_id), '') is null
       or nullif(btrim(p_candidate_username), '') is null then
        raise exception 'ledger_scope_missing';
    end if;
    if p_ordering_version <> 'FOLLOW60_ORDERING_V2' then
        raise exception 'ledger_ordering_version_invalid';
    end if;
    if p_action_type not in (
        'profile_certified', 'post_opened', 'like_verified', 'like_skipped',
        'profile_reentry_verified', 'follow_pending', 'follow_verified',
        'follow_failed', 'mute_posts_verified', 'mute_stories_verified',
        'return_ct_exact', 'cycle_complete', 'stop_recorded'
    ) then
        raise exception 'ledger_action_type_invalid';
    end if;

    insert into follow60_ordering_v2_test.ledger (
        account_id, run_id, request_id, business_session_id, target_id,
        action_id, candidate_username, ordering_version
    ) values (
        p_account_id, p_run_id, p_request_id, p_business_session_id, p_target_id,
        p_action_id, lower(ltrim(p_candidate_username, '@')), p_ordering_version
    ) on conflict do nothing;

    select * into v_ledger
    from follow60_ordering_v2_test.ledger
    where account_id = p_account_id
      and run_id = p_run_id
      and request_id = p_request_id
      and business_session_id = p_business_session_id
      and target_id = p_target_id
      and action_id = p_action_id
      and candidate_username = lower(ltrim(p_candidate_username, '@'))
      and ordering_version = p_ordering_version
    for update;

    if p_action_type = 'post_opened' and not v_ledger.profile_certified then
        raise exception 'ledger_transition_invalid:post_opened';
    elsif p_action_type in ('like_verified', 'like_skipped') and not v_ledger.post_opened then
        raise exception 'ledger_transition_invalid:like_terminal';
    elsif p_action_type = 'like_verified' and v_ledger.like_skipped then
        raise exception 'ledger_transition_conflict:like_verified';
    elsif p_action_type = 'like_skipped' and v_ledger.like_verified then
        raise exception 'ledger_transition_conflict:like_skipped';
    elsif p_action_type = 'profile_reentry_verified'
          and not (v_ledger.like_verified or v_ledger.like_skipped) then
        raise exception 'ledger_transition_invalid:profile_reentry_verified';
    elsif p_action_type = 'follow_pending' and not v_ledger.profile_reentry_verified then
        raise exception 'ledger_transition_invalid:follow_pending';
    elsif p_action_type in ('follow_verified', 'follow_failed') and not v_ledger.follow_pending then
        raise exception 'ledger_transition_invalid:follow_terminal';
    elsif p_action_type = 'follow_verified' and v_ledger.follow_failed then
        raise exception 'ledger_transition_conflict:follow_verified';
    elsif p_action_type = 'follow_failed' and v_ledger.follow_verified then
        raise exception 'ledger_transition_conflict:follow_failed';
    elsif p_action_type in ('mute_posts_verified', 'mute_stories_verified')
          and not v_ledger.follow_verified then
        raise exception 'ledger_transition_invalid:mute';
    elsif p_action_type = 'return_ct_exact'
          and not (v_ledger.follow_verified and v_ledger.mute_posts_verified and v_ledger.mute_stories_verified) then
        raise exception 'ledger_transition_invalid:return_ct_exact';
    elsif p_action_type = 'cycle_complete'
          and not (
              (v_ledger.like_verified or v_ledger.like_skipped)
              and v_ledger.follow_verified
              and v_ledger.mute_posts_verified
              and v_ledger.mute_stories_verified
              and v_ledger.return_ct_exact
          ) then
        raise exception 'ledger_transition_invalid:cycle_complete';
    end if;

    insert into follow60_ordering_v2_test.receipts (ledger_id, action_type, payload_hash, payload)
    values (v_ledger.ledger_id, p_action_type, v_payload_hash, coalesce(p_payload, '{}'::jsonb))
    on conflict (ledger_id, action_type) do nothing
    returning receipt_id into v_receipt_id;

    if v_receipt_id is null then
        select payload_hash into v_existing_hash
        from follow60_ordering_v2_test.receipts
        where ledger_id = v_ledger.ledger_id and action_type = p_action_type;
        if v_existing_hash is distinct from v_payload_hash then
            raise exception 'ledger_duplicate_conflict:%', p_action_type;
        end if;
    else
        v_inserted := true;
        update follow60_ordering_v2_test.ledger
        set profile_certified = profile_certified or p_action_type = 'profile_certified',
            post_opened = post_opened or p_action_type = 'post_opened',
            like_verified = like_verified or p_action_type = 'like_verified',
            like_skipped = like_skipped or p_action_type = 'like_skipped',
            profile_reentry_verified = profile_reentry_verified or p_action_type = 'profile_reentry_verified',
            follow_pending = follow_pending or p_action_type = 'follow_pending',
            follow_verified = follow_verified or p_action_type = 'follow_verified',
            follow_failed = follow_failed or p_action_type = 'follow_failed',
            mute_posts_verified = mute_posts_verified or p_action_type = 'mute_posts_verified',
            mute_stories_verified = mute_stories_verified or p_action_type = 'mute_stories_verified',
            return_ct_exact = return_ct_exact or p_action_type = 'return_ct_exact',
            stop_recorded = stop_recorded or p_action_type = 'stop_recorded',
            cycle_complete = cycle_complete or p_action_type = 'cycle_complete',
            updated_at = clock_timestamp()
        where ledger_id = v_ledger.ledger_id;

        insert into follow60_ordering_v2_test.outbox (receipt_id)
        values (v_receipt_id)
        on conflict (receipt_id) do nothing;
    end if;

    update follow60_ordering_v2_test.ledger
    set updated_at = clock_timestamp()
    where ledger_id = v_ledger.ledger_id
    returning * into v_ledger;

    return jsonb_build_object(
        'ok', true,
        'ledger_id', v_ledger.ledger_id,
        'inserted', v_inserted,
        'duplicate', not v_inserted,
        'action_type', p_action_type,
        'cycle_complete', v_ledger.cycle_complete,
        'like_verified', v_ledger.like_verified,
        'like_skipped', v_ledger.like_skipped,
        'follow_verified', v_ledger.follow_verified,
        'follow_failed', v_ledger.follow_failed,
        'stop_recorded', v_ledger.stop_recorded
    );
end;
$$;

create or replace function follow60_ordering_v2_test.ack_outbox(p_receipt_id bigint)
returns jsonb
language plpgsql
as $$
declare
    v_was_pending boolean;
begin
    select status = 'pending' into v_was_pending
    from follow60_ordering_v2_test.outbox
    where receipt_id = p_receipt_id
    for update;
    if v_was_pending is null then
        raise exception 'outbox_receipt_missing';
    end if;
    update follow60_ordering_v2_test.outbox
    set status = 'acked', acked_at = coalesce(acked_at, clock_timestamp())
    where receipt_id = p_receipt_id;
    return jsonb_build_object('ok', true, 'duplicate', not v_was_pending);
end;
$$;

create or replace function follow60_ordering_v2_test.next_stage(p_ledger_id bigint)
returns text
language sql
stable
as $$
    select case
        when follow_failed then 'follow_failed_terminal'
        when not profile_certified then 'profile_certified'
        when not post_opened then 'post_opened'
        when not (like_verified or like_skipped) then 'like'
        when not profile_reentry_verified then 'profile_reentry_verified'
        when not follow_pending then 'follow_pending'
        when not follow_verified then 'follow_verified'
        when not mute_posts_verified then 'mute_posts_verified'
        when not mute_stories_verified then 'mute_stories_verified'
        when not return_ct_exact then 'return_ct_exact'
        else 'cycle_complete'
    end
    from follow60_ordering_v2_test.ledger
    where ledger_id = p_ledger_id;
$$;

revoke all on all tables in schema follow60_ordering_v2_test from public;
revoke all on all functions in schema follow60_ordering_v2_test from public;
