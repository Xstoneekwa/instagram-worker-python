"""Real unchanged Backend -> PG17/PostgREST -> Worker -> terminal contracts."""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import urllib.request
from urllib.parse import urlparse

from test_auto_restart_terminal_lineage_v1 import A,SOURCE,PRIOR,DEVICE,APP,ASSIGNMENT,WORKER
from auto_restart_runtime import validate_claimed_auto_restart_request,runner_env_for_resume_policy,load_resume_policy_from_env

ROOT=Path(__file__).resolve().parents[1]
MIGRATION=ROOT/'supabase/migrations/20260914122213_operator_stop_natural_tick_lineage_v1.sql'
NODE='/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'

def literal(value):
    return "'"+json.dumps(value).replace("'","''")+"'::jsonb"

@unittest.skipUnless(os.environ.get('OPERATOR_STOP_TEST_SOCKET'),'requires disposable PG17/PostgREST')
class OperatorStopDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.socket=Path(os.environ['OPERATOR_STOP_TEST_SOCKET'])
        if cls.socket.parent!=Path('/private/tmp') or not cls.socket.name.startswith('restriction-resume-pg17-'):
            raise RuntimeError('isolated_socket_required')
        cls.port=os.environ['OPERATOR_STOP_TEST_PORT']
        cls.output=Path(os.environ['OPERATOR_STOP_TEST_OUTPUT'])
        cls.backend=Path(os.environ['OPERATOR_STOP_BACKEND_ROOT'])
        import supabase_client
        cls.db=supabase_client
        native=urllib.request.urlopen
        origin=urlparse(os.environ['SUPABASE_URL'])
        def local_only(req,*args,**kwargs):
            url=urlparse(req.full_url if hasattr(req,'full_url') else str(req))
            if (url.scheme,url.netloc)!=(origin.scheme,origin.netloc):
                raise RuntimeError('non_local_network_forbidden')
            return native(req,*args,**kwargs)
        guard=patch('urllib.request.urlopen',side_effect=local_only);guard.start()
        cls.addClassCleanup(guard.stop)
        cls.proofs=[]
        cls.before_functions=cls.function_hashes()

    @classmethod
    def function_hashes(cls):
        return json.loads(cls.sql("select jsonb_object_agg(p.oid::regprocedure::text,encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex')) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.prokind='f'"))

    @classmethod
    def tearDownClass(cls):
        (cls.output/'real-path-receipt.json').write_text(json.dumps(cls.proofs,indent=2)+'\n')
        (cls.output/'catalog-after.json').write_text(json.dumps(cls.function_hashes(),indent=2)+'\n')

    @classmethod
    def sql(cls,query,ok=True):
        p=subprocess.run(['/opt/homebrew/opt/postgresql@17/bin/psql','-X','-qAt','-v','ON_ERROR_STOP=1',
          '-h',str(cls.socket),'-p',cls.port,'-U','postgres','-d','postgres'],input=query,text=True,capture_output=True)
        if ok and p.returncode: raise AssertionError(p.stderr)
        return p.stdout.strip() if ok else p

    def setUp(self):
        self.sql('select operator_stop_test.reset_ready()')

    def rpc(self,name,body):
        return self.db.call_rpc_once(name,body,timeout_seconds=10)

    def metadata(self,source_attempt=1,follow=False):
        quota={k:dict(enabled=k=='unfollow',doneToday=0,capDay=72,remaining=72 if k=='unfollow' else 0)
               for k in ('welcome','follow','unfollow','outreach')}
        candidate=dict(accountId=A,assignmentId=ASSIGNMENT,deviceId=DEVICE,appInstanceId=APP,
          sourceRunId=PRIOR,sourceRequestId=SOURCE,canonicalAttemptId=source_attempt,sourceLineageValid=True,
          sourceBusinessSessionId='operator-stop:'+PRIOR,nextRetryIndex=0,operatorStopContinuation=True,
          operatorStopReason='botapp_manual_stop',freshBoundaryOnly=True,exactViewportResumeAvailable=False,
          safeRestartStrategy='rebuilt_safe_target_plan',safeRestartReason='operator_stop_live_phase_plan_rebuilt',
          restartNeeded=True,accountEligible=True,enqueueAllowed=True,packageCode='growth',packageLabel='Growth',
          plannedPhasesToRun=dict(welcome=False,follow=follow,unfollow=True),
          plannedQuotaRemaining=dict(welcome=0,follow=50 if follow else 0,unfollow=72,outreach=0),quotas=quota,
          eligibleFollowTargetCount=1 if follow else 0,eligibleUnfollowCandidateCount=72,
          reliability=dict(restartAllowed=False,restartBlockReason='operator_canceled',
            sessionTerminationClass='operator_canceled',cleanupCompleted=True,lockReleased=True,
            unfollowCheckpoint={'schema':'UNFOLLOW_RESUME_CHECKPOINT_V1','depth':4}))
        authority=dict(sourcePlanLineageValid=True,attemptLineageValid=True,lastRunStatus='stopped',
          sourceRequestStatus='canceled',cancelRequestedAt=datetime.now(timezone.utc).isoformat(),
          cancelReason='botapp_manual_stop',restartAllowed=False,restartBlockReason='operator_canceled',unsafeMarkers=[])
        result=subprocess.run([NODE,'--experimental-strip-types',str(ROOT/'tests/operator_stop_backend_payload_v1.ts'),str(self.backend)],
          input=json.dumps(dict(candidate=candidate,authority=authority,now=datetime.now(timezone.utc).isoformat())),
          capture_output=True,text=True,check=True)
        return json.loads(result.stdout)

    def enqueue(self,meta=None,key='operator-stop-once'):
        return self.rpc('create_account_run_request',dict(p_account_id=A,p_actor_type='system',
          p_source_surface='auto_restart_tick',p_requested_run_type='account_session',
          p_idempotency_key=key,p_metadata_safe=meta or self.metadata()))

    def assert_rejected_without_effect(self,m,reason='operator_stop|auto_restart'):
        before=self.sql("select jsonb_build_array((select count(*) from account_run_requests),(select count(*) from auto_restart_device_locks))")
        with self.assertRaisesRegex(Exception,reason): self.enqueue(m)
        self.assertEqual(before,self.sql("select jsonb_build_array((select count(*) from account_run_requests),(select count(*) from auto_restart_device_locks))"))

    def test_00_reproduce_original_error_then_apply_forward_migration(self):
        m=self.metadata()
        self.assertEqual(m['attempt_id'],1);self.assertEqual(m['retry_index'],0)
        self.assert_rejected_without_effect(m,'auto_restart_request_lineage_mismatch')
        self.sql(MIGRATION.read_text())
        q=self.enqueue(m)
        self.assertEqual((q['root_business_session_id'],q['execution_attempt_no'],q['retry_index']),(q['id'],1,0))
        self.proofs.append(dict(contract='same_real_backend_payload_before_after',before='auto_restart_request_lineage_mismatch',after='enqueued',request=q['id']))

    def test_10_first_and_third_stop_real_claim_admission_terminal(self):
        import account_run_control as control
        import account_session_resume_plan_store as store
        from account_run_request_consumer import _admit_account_session_v1
        from account_session_orchestrator import _canonical_terminal_lineage
        for attempt in (1,3):
            with self.subTest(source_attempt=attempt):
                self.sql(f'select operator_stop_test.reset_ready({attempt})')
                original=self.sql(f"select jsonb_build_array((select to_jsonb(q) from account_run_requests q where id='{SOURCE}'),(select to_jsonb(r) from ig_runs r where id='{PRIOR}'))")
                m=self.metadata(attempt,follow=attempt==1)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results=list(pool.map(lambda _:self.enqueue(m),range(4)))
                self.assertEqual(len({q['id'] for q in results}),1)
                q=results[0]
                self.assertEqual(q,self.enqueue(m))
                row=control.claim_next_account_run_request(WORKER,allowed_run_types=['account_session'])
                self.assertEqual(row['id'],q['id'])
                ok,reason,policy=validate_claimed_auto_restart_request(account_id=A,metadata=row['metadata_safe'],claimed_request=row)
                self.assertTrue(ok,reason)
                self.assertEqual(policy['phases_to_run'],m['resume_plan']['phases_to_run'])
                for phase in ('follow','unfollow','welcome'):
                    self.assertEqual(policy['quota_remaining'][phase],m['resume_plan']['quota_remaining'][phase])
                self.assertEqual(policy['unfollow_checkpoint'],m['resume_plan']['unfollow_checkpoint'])
                binding=row['metadata_safe']['device_lock_binding']
                context=dict(assignment_id=ASSIGNMENT,device_id=DEVICE,app_instance_id=APP,package_name='com.instagram.android')
                run=_admit_account_session_v1(request_id=row['id'],worker_id=WORKER,dispatch_ctx=context,device_lock_binding=binding)
                self.assertTrue(run)
                with patch.dict(os.environ,runner_env_for_resume_policy(policy)):
                    transported=load_resume_policy_from_env()
                lineage=_canonical_terminal_lineage(resume_policy=transported,run_request_id=row['id'],supplied_business_session_id=None,run_id=run,default_attempt_id=1)
                capsule=store.load_resume_plan(run_id=run)
                self.assertEqual((lineage['root_business_session_id'],lineage['execution_attempt_no'],lineage['retry_index']),(row['id'],1,0))
                for k in ('root_business_session_id','execution_attempt_no','retry_index'):
                    self.assertEqual(capsule['plan'][k],lineage[k])
                terminal={**lineage,'session_termination_class':'completed','restart_allowed':False,
                  'restart_eligibility':'not_needed','restart_block_reason':'session_completed',
                  'phases_to_run':dict(welcome=False,follow=False,unfollow=False),
                  'quota_remaining':dict(follow=0,unfollow=0,welcome=0,total=0),
                  'unfollow_actions_verified':72,'unfollow_results_persisted_count':72}
                for k,v in [('root_business_session_id',SOURCE),('parent_request_id',SOURCE),('attempt_id',2)]:
                    self.assertFalse(store.record_end_of_session(run_id=run,session_plan={**terminal,k:v},session_status='success')['persisted'])
                for _ in range(2):
                    self.assertTrue(store.record_end_of_session(run_id=run,session_plan=terminal,session_status='success')['persisted'])
                final=store.load_resume_plan(run_id=run)
                self.assertEqual(final['resume_state'],'completed')
                self.assertEqual(final['plan']['unfollow_results_persisted_count'],72)
                self.assertEqual(original,self.sql(f"select jsonb_build_array((select to_jsonb(q) from account_run_requests q where id='{SOURCE}'),(select to_jsonb(r) from ig_runs r where id='{PRIOR}'))"))
                self.proofs.append(dict(contract='backend_pg_worker_terminal',source_attempt=attempt,request=row['id'],run=run,
                  root=row['id'],attempt=1,retry=0,concurrent_requests_created=1,checkpoint_preserved=True,source_unchanged=True,
                  terminal_state=final['resume_state'],terminal_verified=72))

    def test_20_forged_contract_fields_do_not_reset_retry_budget(self):
        for level in ('top','plan'):
            for key,value in [('operator_stop_continuation',False),('fresh_boundary_only',False),
              ('exact_viewport_resume_available',True),('safe_restart_strategy','exact_checkpoint_resume'),
              ('operator_stop_source_reason','another_reason'),('source_lineage_valid',False),
              ('business_session_id','operator-stop:'+SOURCE),('source_request_id',PRIOR),
              ('source_canonical_attempt_id',3),('attempt_id',2),('retry_index',1),('account_id',PRIOR)]:
                with self.subTest(level=level,key=key):
                    m=self.metadata();(m if level=='top' else m['resume_plan'])[key]=value
                    self.assert_rejected_without_effect(m)

    def test_21_missing_and_cross_account_source_rejected(self):
        for key,value in [('source_request_id',None),('prior_run_id',None),('source_request_id',PRIOR),('prior_run_id',SOURCE)]:
            m=self.metadata();m[key]=value;self.assert_rejected_without_effect(m)
        self.sql(f"insert into ig_accounts(id,username,status) values ('{APP}','another_synthetic_account','active'); update account_run_requests set account_id='{APP}' where id='{SOURCE}';")
        self.assert_rejected_without_effect(self.metadata())

    def test_22_durable_stop_evidence_required(self):
        for change in ["status='failed'","status='completed'","status='running'","cancel_reason='incident_pause'",
                       "cancel_requested_at=null","cancel_requested_at=now()+interval '1 day'"]:
            self.sql('select operator_stop_test.reset_ready()');self.sql('update account_run_requests set '+change)
            self.assert_rejected_without_effect(self.metadata(),'operator_stop|auto_restart|account_run_already_requested')
        for change in ["status='completed'","status='failed'","status='running'","finished_at=null"]:
            self.sql('select operator_stop_test.reset_ready()');self.sql('update ig_runs set '+change)
            self.assert_rejected_without_effect(self.metadata(),'operator_stop|auto_restart|account_already_running')

    def test_23_different_key_cannot_repeat_same_stop(self):
        q=self.enqueue()
        self.sql(f"update account_run_requests set status='canceled',completed_at=now() where id='{q['id']}';delete from auto_restart_device_locks;")
        with self.assertRaisesRegex(Exception,'superseded|unique'):self.enqueue(key='different-key')
        self.assertEqual(self.sql("select count(*) from account_run_requests where metadata_safe->'operator_stop_lineage_contract_v1'='true'::jsonb"),'1')

    def test_24_newer_run_supersedes_old_stop(self):
        self.sql(f"insert into ig_runs(id,account_id,status,started_at,finished_at) values ('{APP}','{A}','completed',now()-interval '1 minute',now())")
        self.assert_rejected_without_effect(self.metadata(),'superseded')

    def test_30_admission_fence_still_blocks_enqueue(self):
        self.rpc('acquire_phonefarm_worker_promotion_fence_v1',dict(p_transaction_id='local-opstop-test',p_candidate_sha='a'*40))
        try:self.assert_rejected_without_effect(self.metadata(),'fence|promotion')
        finally:self.rpc('release_phonefarm_worker_promotion_fence_v1',dict(p_transaction_id='local-opstop-test',p_terminal_state='ROLLED_BACK'))

    def test_31_trigger_privileges_and_immutable_tuple(self):
        for role in ('anon','authenticated'):
            self.assertEqual(self.sql(f"select has_function_privilege('{role}','public.apply_ordinary_auto_restart_lineage_v1()','execute')"),'f')
        q=self.enqueue()
        bad=self.sql(f"update account_run_requests set execution_attempt_no=2,retry_index=1 where id='{q['id']}'",ok=False)
        self.assertIn('account_run_request_lineage_immutable',bad.stderr)
        self.assertEqual(self.sql("select count(*) from pg_index where indexrelid='account_run_request_operator_stop_source_once_v1'::regclass and indisunique and indisvalid"),'1')
        after=self.function_hashes()
        changed=[k for k in after.keys()|self.before_functions.keys() if after.get(k)!=self.before_functions.get(k)]
        self.assertEqual(changed,['apply_ordinary_auto_restart_lineage_v1()'])
        self.proofs.append(dict(contract='catalog_scope',changed_functions=changed,
          unchanged_functions=len(after)-1,no_new_public_function=True))

    def test_32_claim_preserves_physical_safety_guards(self):
        import account_run_control as control
        for marker in ('instagram_challenge_checkpoint','instagram_action_restriction','active_instagram_account_mismatch'):
            with self.subTest(marker=marker):
                self.sql('select operator_stop_test.reset_ready()')
                self.sql(f"update ig_runs set performance_summary=jsonb_set(performance_summary,'{{auto_restart_resume_plan,root_failure_code}}',to_jsonb('{marker}'::text))")
                self.enqueue()
                row=control.claim_next_account_run_request(WORKER,allowed_run_types=['account_session'])
                ok,reason,policy=validate_claimed_auto_restart_request(account_id=A,metadata=row['metadata_safe'],claimed_request=row)
                self.assertFalse(ok);self.assertIsNone(policy)
                self.assertIn(reason,('challenge_blocked','restriction_blocked','account_mismatch_blocked'))

    def test_33_index_rejects_concurrent_distinct_keys(self):
        m=self.metadata()
        def create(i):
            try:return self.enqueue(m,'parallel-distinct-'+str(i))
            except Exception:return None
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(create,range(4)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.sql("select count(*) from account_run_requests where metadata_safe->'operator_stop_lineage_contract_v1'='true'::jsonb"),'1')
        self.assertEqual(self.sql('select count(*) from auto_restart_device_locks'),'1')

    def test_40_exact_predecessor_rollback_and_forward(self):
        predecessor=(self.output/'predecessor.sql').read_text()
        current=self.sql("select pg_get_functiondef('public.apply_ordinary_auto_restart_lineage_v1()'::regprocedure)")
        self.sql(predecessor)
        self.assert_rejected_without_effect(self.metadata(),'auto_restart_request_lineage_mismatch')
        self.sql('drop index account_run_request_operator_stop_source_once_v1;'+MIGRATION.read_text())
        self.assertEqual(current,self.sql("select pg_get_functiondef('public.apply_ordinary_auto_restart_lineage_v1()'::regprocedure)"))
        q=self.enqueue();self.assertEqual(q['execution_attempt_no'],1)

if __name__=='__main__': unittest.main()
