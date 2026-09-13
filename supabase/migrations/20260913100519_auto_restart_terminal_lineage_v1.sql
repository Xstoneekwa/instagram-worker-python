-- Ordinary Auto Restart must inherit canonical SQL lineage at INSERT, before
-- the immutable-lineage default and before atomic admission builds a capsule.
-- Existing enqueue, human authorization, Fleet lease and fence RPCs are untouched.
begin;

do $$
begin
  if to_regprocedure('public.account_run_request_lineage_v1_guard()') is null
     or to_regprocedure('public.apply_authorized_resume_request_lineage_v1()') is null
     or to_regprocedure('public.create_account_run_request(uuid,uuid,text,text,text,text,integer,jsonb)') is null then
    raise exception 'auto_restart_lineage_dependencies_missing';
  end if;
end
$$;

create function public.apply_ordinary_auto_restart_lineage_v1()
returns trigger
language plpgsql security invoker set search_path = public, pg_temp
as $$
declare
  v_source public.account_run_requests%rowtype;
  v_run public.ig_runs%rowtype;
  v_meta jsonb := coalesce(new.metadata_safe, '{}'::jsonb);
  v_plan jsonb;
  v_record jsonb;
  v_next integer;
  v_key text;
  v_value text;
  v_lineage jsonb;
begin
  if new.source_surface <> 'auto_restart_tick'
     or new.requested_run_type <> 'account_session' then
    return new;
  end if;
  -- The preceding 00 trigger already verifies the consumed human
  -- authorization. Do not reinterpret or weaken that separate contract.
  if v_meta -> 'resume_lineage_contract_v1' = 'true'::jsonb then
    return new;
  end if;
  v_plan := v_meta -> 'resume_plan';
  -- Historical verification-only and explicitly armed *first* attempts are
  -- distinct contracts. Their existing authorization/Worker guards remain.
  if v_plan -> 'restriction_preflight_only' = 'true'::jsonb
     or (jsonb_typeof(v_plan -> 'follow_60s_canary_contract') = 'object'
         and v_meta ->> 'attempt_id' = '1') then
    return new;
  end if;
  if v_meta -> 'auto_restart' is distinct from 'true'::jsonb
     or v_meta ->> 'source' is distinct from 'auto_restart_tick'
     or jsonb_typeof(v_plan) is distinct from 'object'
     or v_plan ->> 'schema' is distinct from 'AUTO_RESTART_RESUME_PLAN_V2'
     or nullif(v_meta ->> 'source_request_id', '') is null
     or nullif(v_meta ->> 'prior_run_id', '') is null then
    raise exception 'auto_restart_source_lineage_missing' using errcode = '22023';
  end if;
  select * into v_source from public.account_run_requests
    where id = (v_meta ->> 'source_request_id')::uuid
      and account_id = new.account_id for share;
  select * into v_run from public.ig_runs
    where id = (v_meta ->> 'prior_run_id')::uuid
      and account_id = new.account_id for share;
  if v_source.id is null or v_run.id is null
     or v_source.run_id is distinct from v_run.id
     or v_source.requested_run_type <> 'account_session'
     or v_source.status not in ('completed','failed','blocked','canceled')
     or v_run.status not in ('completed','failed','stopped')
     or v_source.root_business_session_id is null
     or v_source.execution_attempt_no is null
     or v_source.retry_index is distinct from v_source.execution_attempt_no - 1 then
    raise exception 'auto_restart_source_lineage_mismatch' using errcode = '22023';
  end if;
  v_next := v_source.execution_attempt_no + 1;
  if v_next not between 2 and 3 then
    raise exception 'auto_restart_attempt_limit_reached' using errcode = '22023';
  end if;
  if (new.root_business_session_id is not null and new.root_business_session_id <> v_source.root_business_session_id)
     or (new.execution_attempt_no is not null and new.execution_attempt_no <> v_next)
     or (new.retry_index is not null and new.retry_index <> v_next - 1) then
    raise exception 'auto_restart_request_lineage_mismatch' using errcode = '22023';
  end if;
  foreach v_record in array array[v_meta, v_plan] loop
    foreach v_key in array array['attempt_id','current_attempt_id','execution_attempt_no','retry_index','source_canonical_attempt_id'] loop
      v_value := nullif(v_record ->> v_key, '');
      if v_value is not null and (
          jsonb_typeof(v_record -> v_key) not in ('number','string')
          or v_value !~ '^[0-9]+$') then
        raise exception 'auto_restart_request_lineage_mismatch' using errcode = '22023';
      end if;
      if v_value is not null and v_value::numeric <> (case
          when v_key in ('retry_index','source_canonical_attempt_id') then v_next - 1
          else v_next end) then
        raise exception 'auto_restart_request_lineage_mismatch' using errcode = '22023';
      end if;
    end loop;
    foreach v_key in array array['source_request_id','parent_request_id'] loop
      if nullif(v_record ->> v_key, '') is not null
         and (v_record ->> v_key)::uuid is distinct from v_source.id then
        raise exception 'auto_restart_source_lineage_mismatch' using errcode = '22023';
      end if;
    end loop;
    foreach v_key in array array['prior_run_id','source_run_id','previous_run_id'] loop
      if nullif(v_record ->> v_key, '') is not null
         and (v_record ->> v_key)::uuid is distinct from v_run.id then
        raise exception 'auto_restart_source_lineage_mismatch' using errcode = '22023';
      end if;
    end loop;
    if (nullif(v_record ->> 'root_business_session_id', '') is not null
        and (v_record ->> 'root_business_session_id')::uuid is distinct from v_source.root_business_session_id)
       or (nullif(v_record ->> 'account_id', '') is not null
        and (v_record ->> 'account_id')::uuid is distinct from new.account_id) then
      raise exception 'auto_restart_source_lineage_mismatch' using errcode = '22023';
    end if;
    v_value := nullif(v_record ->> 'business_session_id', '');
    -- A historical scheduled: label is a projection, never root authority.
    -- Replace it with the source row's UUID; reject a conflicting UUID root.
    if v_value is not null and v_value not like 'scheduled:%'
       and v_value::uuid is distinct from v_source.root_business_session_id then
      raise exception 'auto_restart_source_lineage_mismatch' using errcode = '22023';
    end if;
  end loop;
  new.root_business_session_id := v_source.root_business_session_id;
  new.execution_attempt_no := v_next;
  new.retry_index := v_next - 1;
  v_lineage := jsonb_build_object(
    'root_business_session_id', v_source.root_business_session_id::text,
    'business_session_id', v_source.root_business_session_id::text,
    'source_request_id', v_source.id::text, 'parent_request_id', v_source.id::text,
    'execution_attempt_no', v_next, 'attempt_id', v_next,
    'current_attempt_id', v_next, 'retry_index', v_next - 1);
  new.metadata_safe := v_meta || v_lineage || jsonb_build_object(
    'resume_plan', v_plan || v_lineage);
  return new;
exception when invalid_text_representation or numeric_value_out_of_range then
  raise exception 'auto_restart_request_lineage_invalid' using errcode = '22023';
end
$$;

revoke all on function public.apply_ordinary_auto_restart_lineage_v1() from public, anon, authenticated;
create trigger account_run_request_01_auto_restart_lineage_v1
before insert on public.account_run_requests
for each row execute function public.apply_ordinary_auto_restart_lineage_v1();

comment on function public.apply_ordinary_auto_restart_lineage_v1() is
  'Ordinary Auto Restart INSERT lineage derived from the exact canonical source; no historical updates, authorization bypass or terminal guard relaxation.';
commit;
