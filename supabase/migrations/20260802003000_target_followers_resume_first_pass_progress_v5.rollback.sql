begin;

drop function if exists public.commit_target_followers_resume_first_pass_progress_v5(
  uuid,uuid,text,uuid,text,bigint,jsonb,text,text,jsonb,text,integer
);

commit;
