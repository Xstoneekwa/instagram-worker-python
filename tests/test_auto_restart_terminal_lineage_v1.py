"""Pure claim checks plus the optional, real PG17/PostgREST end-to-end suite."""
from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import urllib.request

from auto_restart_runtime import (
    _claimed_request_lineage, _bind_claimed_lineage,
    validate_claimed_auto_restart_request, runner_env_for_resume_policy,
    load_resume_policy_from_env,
)

ROOT = Path(__file__).resolve().parents[1]
A = '00000000-0000-0000-0000-000000009002'
SOURCE = '00000000-0000-0000-0000-000000009010'
PRIOR = '00000000-0000-0000-0000-000000009009'
DEVICE = '00000000-0000-0000-0000-000000009006'
APP = '00000000-0000-0000-0000-000000009007'
ASSIGNMENT = '00000000-0000-0000-0000-000000009008'
WORKER = 'run-dispatcher:local-cert'


def claimed(attempt=2):
    return dict(id='00000000-0000-0000-0000-000000009020', account_id=A,
                root_business_session_id=SOURCE, execution_attempt_no=attempt, retry_index=attempt-1)


def metadata(source=SOURCE, prior=PRIOR, attempt=2):
    lineage = dict(attempt_id=attempt, retry_index=attempt-1, source_request_id=source,
                   source_run_id=prior, prior_run_id=prior, business_session_id=SOURCE)
    return dict(auto_restart=True, source='auto_restart_tick', **lineage,
                worker_id=WORKER, execution_worker_id=WORKER,
                resume_plan_version=2, resume_plan_schema='AUTO_RESTART_RESUME_PLAN_V2',
                resume_plan=dict(schema='AUTO_RESTART_RESUME_PLAN_V2',plan_version=2,
                                 account_id=A, **lineage,
                                 restart_allowed=True,restart_block_reason='',
                                 session_termination_class='partial_resumable',
                                 phases_to_run=dict(welcome=False,follow=False,unfollow=True),
                                 phase_order=['welcome','follow','unfollow'],
                                 quota_remaining=dict(welcome=0,follow=0,unfollow=72,outreach=0,total=72),
                                 cleanup_completed=True,lock_released=True,package_contract_ready=True))


class ClaimTests(unittest.TestCase):
    def test_outreach_retains_its_separate_policy_contract(self):
        existing=(True,'',{'outreach_policy':'unchanged'})
        with patch('auto_restart_runtime.validate_auto_restart_request_at_claim',return_value=existing) as validate:
            result=validate_claimed_auto_restart_request(account_id=A,metadata=metadata(),
                claimed_request={'requested_run_type':'outreach_session'})
        self.assertEqual(result,existing)
        validate.assert_called_once_with(account_id=A,metadata=metadata())

    def test_missing_canonical_row_never_loads_prior_or_starts_work(self):
        with patch('auto_restart_runtime.load_prior_run_summary') as load:
            ok, reason, policy = validate_claimed_auto_restart_request(account_id=A, metadata=metadata(), claimed_request=None)
        self.assertFalse(ok); self.assertIsNone(policy); load.assert_not_called()
        self.assertEqual(reason, 'resume_claim_lineage_missing_or_invalid')

    def test_reproduced_canonical_one_metadata_two_is_rejected(self):
        with patch('auto_restart_runtime.load_prior_run_summary') as load:
            ok, reason, policy = validate_claimed_auto_restart_request(account_id=A, metadata=metadata(), claimed_request=claimed(1))
        self.assertFalse(ok); self.assertIsNone(policy); load.assert_not_called()
        self.assertEqual(reason, 'resume_claim_lineage_mismatch')

    def test_conflicts_at_every_request_projection_are_rejected(self):
        for level in ('top', 'embedded'):
            for key, value in [('attempt_id',1),('current_attempt_id',3),('execution_attempt_no',1),
                               ('retry_index',0),('attempt_id',2.7),('attempt_id',True),
                               ('root_business_session_id',PRIOR),('business_session_id',PRIOR),
                               ('parent_request_id',PRIOR),('account_id',PRIOR)]:
                with self.subTest(level=level,key=key,value=value):
                    meta=metadata(); target=meta if level=='top' else meta['resume_plan'];target[key]=value
                    with self.assertRaises(ValueError): _claimed_request_lineage(A,claimed(),meta)

    def test_source_parent_and_terminal_parent_are_distinct(self):
        row=claimed();meta=metadata();meta['parent_request_id']=SOURCE
        lineage=_claimed_request_lineage(A,row,meta)
        frozen=copy.deepcopy(meta['resume_plan'])
        policy=_bind_claimed_lineage({'frozen_phase_plan':frozen},lineage)
        self.assertEqual(policy['parent_request_id'],row['id'])
        self.assertEqual(policy['root_business_session_id'],SOURCE)
        self.assertEqual(policy['frozen_phase_plan'],frozen)
        with patch.dict(os.environ,runner_env_for_resume_policy(policy)):
            self.assertEqual(load_resume_policy_from_env(),policy)

    def test_first_third_and_exhausted_attempt_contract(self):
        for attempt in (1,3):
            self.assertEqual(_claimed_request_lineage(A,claimed(attempt),metadata(attempt=attempt))['execution_attempt_no'],attempt)
        with self.assertRaises(ValueError): _claimed_request_lineage(A,claimed(4),metadata(attempt=4))
        bad=claimed();bad['account_id']=PRIOR
        with self.assertRaises(ValueError): _claimed_request_lineage(A,bad,metadata())

    def test_consumer_passes_full_claimed_row_before_runner_spawn(self):
        tree=ast.parse((ROOT/'account_run_request_consumer.py').read_text())
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        checks=[n for n in calls if n.func.id=='validate_claimed_auto_restart_request']
        self.assertEqual(len(checks),1)
        request=next(k.value for k in checks[0].keywords if k.arg=='claimed_request')
        self.assertIsInstance(request,ast.Name);self.assertEqual(request.id,'request')
        self.assertLess(checks[0].lineno,min(n.lineno for n in calls if n.func.id=='_admit_account_session_v1'))


@unittest.skipUnless(os.environ.get('LINEAGE_TEST_SOCKET'),'requires disposable PG17/PostgREST harness')
class PostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.socket=Path(os.environ['LINEAGE_TEST_SOCKET'])
        if cls.socket.parent!=Path('/private/tmp') or not cls.socket.name.startswith('auto-restart-lineage-pg17-'):
            raise RuntimeError('isolated_socket_required')
        import supabase_client
        cls.db=supabase_client
        cls.native_urlopen=urllib.request.urlopen
        from urllib.parse import urlparse
        origin=urlparse(os.environ['SUPABASE_URL'])
        def local_only(req,*args,**kwargs):
            url=urlparse(req.full_url if hasattr(req,'full_url') else str(req))
            if (url.scheme,url.netloc)!=(origin.scheme,origin.netloc):
                raise RuntimeError('non_local_network_forbidden')
            return cls.native_urlopen(req,*args,**kwargs)
        cls.guard=patch('urllib.request.urlopen',side_effect=local_only);cls.guard.start()
        cls.addClassCleanup(cls.guard.stop)

    @classmethod
    def sql(cls, query, *, ok=True):
        r=subprocess.run(['/opt/homebrew/opt/postgresql@17/bin/psql','-X','-qAt','-v','ON_ERROR_STOP=1',
                          '-h',str(cls.socket),'-p','55483','-U','postgres','-d','postgres'],
                         input=query,text=True,capture_output=True)
        if ok and r.returncode: raise AssertionError(r.stderr)
        return r

    def rpc(self,name,body):
        return self.db.call_rpc_once(name,body,timeout_seconds=5)

    def enqueue(self,meta=None,key='lineage-retry-2'):
        return self.rpc('create_account_run_request',dict(p_account_id=A,p_actor_type='system',
            p_source_surface='auto_restart_tick',p_requested_run_type='account_session',
            p_idempotency_key=key,p_metadata_safe=meta or metadata()))

    def test_01_sql_guards_reject_corrupt_and_missing_source_without_rows_or_leases(self):
        for key,value in [('source_request_id',PRIOR),('prior_run_id',SOURCE),('root_business_session_id',PRIOR),
                          ('attempt_id',3),('retry_index',0),('parent_request_id',PRIOR)]:
            m=metadata();m[key]=value
            with self.subTest(key=key), self.assertRaisesRegex(Exception,'auto_restart_(source|request)_lineage_mismatch'): self.enqueue(m,'bad-'+key)
        m=metadata();del m['source_request_id']
        with self.assertRaisesRegex(Exception,'auto_restart_source_lineage_missing'):self.enqueue(m,'missing-source')
        other='00000000-0000-0000-0000-000000009102'
        other_run='00000000-0000-0000-0000-000000009109'
        other_request='00000000-0000-0000-0000-000000009110'
        self.sql(f"""insert into ig_accounts(id,username,status) values ('{other}','synthetic_other','active');
          insert into ig_runs(id,account_id,status) values ('{other_run}','{other}','completed');
          insert into account_run_requests(id,account_id,run_id,status,requested_run_type,source_surface,actor_type,idempotency_key)
          values ('{other_request}','{other}','{other_run}','completed','account_session','dashboard','system','synthetic-other');""")
        for meta in (metadata(other_request,other_run),metadata(SOURCE,other_run)):
            with self.assertRaisesRegex(Exception,'auto_restart_source_lineage_mismatch'): self.enqueue(meta,'bad-other-account')
        self.assertEqual(self.sql("select count(*) from account_run_requests where idempotency_key like 'bad-%' or idempotency_key='missing-source'").stdout.strip(),'0')
        self.assertEqual(self.sql('select count(*) from auto_restart_device_locks').stdout.strip(),'0')
        first=json.loads(self.sql(f"select row_to_json(q) from account_run_requests q where id='{SOURCE}'").stdout)
        self.assertEqual((first['root_business_session_id'],first['execution_attempt_no'],first['retry_index']),(SOURCE,1,0))
        for col,value in [('root_business_session_id',f"'{PRIOR}'"),('execution_attempt_no','2'),('retry_index','1')]:
            rejected=self.sql(f"update account_run_requests set {col}={value} where id='{SOURCE}'",ok=False)
            self.assertNotEqual(rejected.returncode,0)
            self.assertIn('account_run_request_lineage_immutable',rejected.stderr)

    def test_02_atomic_enqueue_claim_admit_policy_terminal_and_replay(self):
        import account_run_control as control
        import account_session_resume_plan_store as store
        from account_session_orchestrator import _canonical_terminal_lineage
        from account_run_request_consumer import _admit_account_session_v1
        source,prior=SOURCE,PRIOR
        proof=[]
        for attempt in (2,3):
            m=metadata(source,prior,attempt)
            # Reproduce the legacy scheduler label too: source SQL UUID wins.
            m['business_session_id']=m['resume_plan']['business_session_id']='scheduled:synthetic-window'
            with ThreadPoolExecutor(max_workers=4) as pool:
                duplicate=list(pool.map(lambda _:self.enqueue(m,'lineage-retry-'+str(attempt)),range(4)))
            self.assertEqual(len({r['id'] for r in duplicate}),1)
            request=duplicate[0]
            self.assertEqual((request['root_business_session_id'],request['execution_attempt_no'],request['retry_index']),(SOURCE,attempt,attempt-1))
            self.assertEqual(self.sql(f"select count(*) from account_run_requests where root_business_session_id='{SOURCE}' and execution_attempt_no={attempt}").stdout.strip(),'1')
            self.assertEqual(self.sql('select count(*) from auto_restart_device_locks').stdout.strip(),'1')
            replay=self.enqueue(m,'lineage-retry-'+str(attempt));self.assertEqual(request,replay)
            row=control.claim_next_account_run_request(WORKER,allowed_run_types=['account_session'])
            self.assertEqual(row['id'],request['id'])
            ok,reason,policy=validate_claimed_auto_restart_request(account_id=A,metadata=row['metadata_safe'],claimed_request=row)
            self.assertTrue(ok,reason)
            binding=row['metadata_safe']['device_lock_binding']
            context=dict(assignment_id=ASSIGNMENT,device_id=DEVICE,app_instance_id=APP,package_name='com.instagram.android')
            run=_admit_account_session_v1(request_id=row['id'],worker_id=WORKER,dispatch_ctx=context,device_lock_binding=binding)
            with patch.dict(os.environ,runner_env_for_resume_policy(policy)):
                transported=load_resume_policy_from_env()
            lineage=_canonical_terminal_lineage(resume_policy=transported,run_request_id=row['id'],supplied_business_session_id=None,run_id=run,default_attempt_id=1)
            capsule=store.load_resume_plan(run_id=run)
            self.assertEqual(capsule['plan']['execution_attempt_no'],attempt)
            for key in ('root_business_session_id','execution_attempt_no','retry_index'):
                self.assertEqual(capsule['plan'][key],lineage[key])
            terminal={**lineage,'session_termination_class':'completed','restart_allowed':False,
                      'restart_eligibility':'not_needed','restart_block_reason':'session_completed',
                      'phases_to_run':dict(welcome=False,follow=False,unfollow=False),
                      'quota_remaining':dict(follow=0,unfollow=0,welcome=0,total=0),
                      'unfollow_actions_verified':72,'unfollow_results_persisted_count':72}
            for key,value,reason in [('root_business_session_id',PRIOR,'root'),
                                     ('parent_request_id',source,'parent'),('attempt_id',1,'attempt')]:
                rejected=store.record_end_of_session(run_id=run,session_plan={**terminal,key:value},session_status='success')
                self.assertEqual(rejected,dict(persisted=False,reason='resume_plan_lineage_'+reason+'_mismatch'))
                self.assertEqual(store.load_resume_plan(run_id=run),capsule)
            for _ in range(2):
                result=store.record_end_of_session(run_id=run,session_plan=terminal,session_status='success')
                self.assertTrue(result['persisted'],result)
            final=store.load_resume_plan(run_id=run)
            self.assertEqual(final['resume_state'],'completed');self.assertFalse(final['restart_allowed'])
            self.assertEqual(final['plan']['unfollow_results_persisted_count'],72)
            proof.append(dict(attempt=attempt,request=row['id'],root=SOURCE,run=run,terminal=final['resume_state'],verified=72,
                              concurrent_identical_calls=4,requests_created=1,terminal_guard_negatives='PASS'))
            # Complete the synthetic run/lease through the existing contracts;
            # the next fixture continuation models another partial boundary.
            self.sql(f"update ig_runs set status='completed',finished_at=now(),performance_summary=(select performance_summary from ig_runs where id='{PRIOR}') where id='{run}';")
            control.complete_account_run_request(row['id'],WORKER,'completed')
            from auto_restart_device_lock import release_device_lock
            release=release_device_lock(device_id=DEVICE,worker_id=WORKER,request_id=row['id'],expected_lease_id=binding['lease_id'],account_id=A)
            self.assertTrue(release.get('released'),release)
            source,prior=row['id'],run
        m=metadata(source,prior,4)
        with self.assertRaisesRegex(Exception,'auto_restart_attempt_limit_reached'):self.enqueue(m,'attempt-4')
        with self.assertRaisesRegex(Exception,'duplicate key|unique constraint'):
            self.enqueue(metadata(),'different-key-same-root-attempt')
        self.assertEqual(self.sql("select count(*) from account_run_requests where idempotency_key='attempt-4'").stdout.strip(),'0')
        Path(os.environ['LINEAGE_TEST_OUTPUT'],'real-path-receipt.json').write_text(json.dumps(proof,indent=2)+'\n')

    def test_03_human_v4_authorization_and_claim_are_preserved(self):
        # Reset only this disposable account's generated rows; restore the
        # synthetic incident, then use the real resolution trigger and V4 RPC.
        self.sql(f"""delete from account_session_resume_plans where account_id='{A}' and run_id<>'{PRIOR}';
          delete from account_run_requests where account_id='{A}' and id<>'{SOURCE}';
          delete from ig_runs where account_id='{A}' and id<>'{PRIOR}';
          insert into account_incidents select * from lineage_test.incident;
          update account_session_resume_plans set incident_id='00000000-0000-0000-0000-000000009011',
            resume_state='awaiting_human_resume_authorization',restart_allowed=true,restart_block_reason='',attempts_in_window=0
            where run_id='{PRIOR}';
          update account_incidents set status='resolved',resolved_at=now(),resolution_note='synthetic local resolution'
            where account_id='{A}';""")
        auth=json.loads(self.sql(f"select row_to_json(a) from incident_resume_authorizations a where account_id='{A}'").stdout)
        self.assertEqual(auth['status'],'armed')
        m=metadata()
        m.update(recovery_mode='human_confirmed_resume',authorization_id=auth['id'],incident_id=auth['incident_id'],
                 original_run_id=PRIOR,resume_plan_id=auth['resume_plan_id'])
        args=dict(p_authorization_id=auth['id'],p_worker_id=WORKER,p_device_id=DEVICE,p_metadata_safe=m)
        corrupt=copy.deepcopy(args);corrupt['p_metadata_safe']['resume_plan']['execution_attempt_no']=3
        with self.assertRaisesRegex(Exception,'resume_plan_lineage_mismatch'):
            self.rpc('consume_resume_authorization_and_create_request_v4',corrupt)
        self.assertEqual(self.sql(f"select status from incident_resume_authorizations where id='{auth['id']}'").stdout.strip(),'armed')
        result=self.rpc('consume_resume_authorization_and_create_request_v4',args)
        replay=self.rpc('consume_resume_authorization_and_create_request_v4',args)
        self.assertEqual(result['request_id'],replay['request_id']);self.assertTrue(replay['idempotent'])
        row=json.loads(self.sql(f"select row_to_json(q) from account_run_requests q where id='{result['request_id']}'").stdout)
        self.assertEqual((row['root_business_session_id'],row['execution_attempt_no'],row['retry_index']),(SOURCE,2,1))
        self.assertTrue(row['metadata_safe']['resume_lineage_contract_v1'])
        ok,reason,policy=validate_claimed_auto_restart_request(account_id=A,metadata=row['metadata_safe'],claimed_request=row)
        self.assertTrue(ok,reason)
        frozen=json.loads(self.sql(f"select frozen_phase_plan from incident_resume_authorizations where id='{auth['id']}'").stdout)
        self.assertEqual(policy['frozen_phase_plan'],frozen)
        self.assertEqual(policy['parent_request_id'],row['id'])
        self.assertEqual(frozen['parent_request_id'],SOURCE)
        self.assertEqual(self.sql(f"select count(*) from account_run_requests where account_id='{A}' and id<>'{SOURCE}'").stdout.strip(),'1')
        Path(os.environ['LINEAGE_TEST_OUTPUT'],'human-v4-receipt.json').write_text(json.dumps(dict(
            status='PASS',authorization=auth['id'],request=row['id'],root=SOURCE,attempt=2,
            replay_same_request=True,invalid_lineage_rejected_before_consumption=True,
            frozen_plan_preserved=True,terminal_parent_is_current=True),indent=2)+'\n')


if __name__=='__main__': unittest.main()
