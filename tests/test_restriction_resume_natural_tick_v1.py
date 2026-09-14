"""Real disposable PostgreSQL/PostgREST admission tests; never contact a device."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
import urllib.request
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
A='00000000-0000-0000-0000-000000009002'
DEVICE='00000000-0000-0000-0000-000000009006'
APP='00000000-0000-0000-0000-000000009007'
ASSIGNMENT='00000000-0000-0000-0000-000000009008'
PRIOR='00000000-0000-0000-0000-000000009009'
SOURCE='00000000-0000-0000-0000-000000009010'
INCIDENT='00000000-0000-0000-0000-000000009011'
PLAN='00000000-0000-0000-0000-000000009012'
WORKER='run-dispatcher:local-cert'
MIGRATION=ROOT/'supabase/migrations/20260914021526_restriction_resume_natural_tick_v1.sql'

class RealDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.socket=Path(os.environ['RESTRICTION_TEST_SOCKET'])
        if cls.socket.parent!=Path('/private/tmp') or not cls.socket.name.startswith('restriction-resume-pg17-'):
            raise RuntimeError('isolated_socket_required')
        import supabase_client
        cls.db=supabase_client
        from urllib.parse import urlparse
        origin=urlparse(os.environ['SUPABASE_URL']);native=urllib.request.urlopen
        def local_only(req,*args,**kwargs):
            u=urlparse(req.full_url if hasattr(req,'full_url') else str(req))
            if (u.scheme,u.netloc)!=(origin.scheme,origin.netloc):raise RuntimeError('non_local_network_forbidden')
            return native(req,*args,**kwargs)
        cls.guard=patch('urllib.request.urlopen',side_effect=local_only);cls.guard.start();cls.addClassCleanup(cls.guard.stop)

    @classmethod
    def sql(cls,q,ok=True):
        r=subprocess.run(['/opt/homebrew/opt/postgresql@17/bin/psql','-X','-qAt','-v','ON_ERROR_STOP=1',
            '-h',str(cls.socket),'-p','55484','-U','postgres','-d','postgres'],input=q,text=True,capture_output=True)
        if ok and r.returncode:raise AssertionError(r.stderr)
        return r.stdout.strip() if ok else r

    def setUp(self):self.sql('select restriction_test.reset()')
    def rpc(self,name,body=None):return self.db.call_rpc_once(name,body or {},timeout_seconds=10)
    def auth(self):return json.loads(self.sql(f"select row_to_json(a) from incident_resume_authorizations a where account_id='{A}' limit 1"))
    def row(self,id):return json.loads(self.sql(f"select row_to_json(q) from account_run_requests q where id='{id}'"))
    def enqueue(self):
        a=self.auth();f=dict(schema='AUTO_RESTART_RESUME_PLAN_V2',plan_version=2,resume_kind='instagram_restriction_preflight',
          restriction_preflight_only=True,account_id=A,assignment_id=ASSIGNMENT,device_id=DEVICE,app_instance_id=APP,
          incident_id=INCIDENT,authorization_id=a['id'],resume_plan_id=PLAN,prior_run_id=PRIOR,
          phase_order=['welcome','follow','unfollow'],phases_to_run=dict(welcome=False,follow=False,unfollow=False),
          quota_remaining=dict(welcome=0,follow=0,unfollow=0,outreach=0,total=0),retry_generation=a['retry_generation'])
        m=dict(source='auto_restart_tick',auto_restart=True,recovery_mode='human_confirmed_resume',
          worker_id=WORKER,execution_worker_id=WORKER,restriction_preflight_only=True,incident_id=INCIDENT,
          authorization_id=a['id'],resume_plan_id=PLAN,prior_run_id=PRIOR,original_run_id=PRIOR,
          authorization_source_request_id=SOURCE,expected_worker_sha=a['expected_worker_sha'],cause_fixed_version=a['cause_fixed_version'],
          resume_plan=f)
        result=self.rpc('consume_resume_authorization_and_create_request_v4',dict(p_authorization_id=a['id'],
            p_worker_id=WORKER,p_device_id=DEVICE,p_metadata_safe=m))
        return self.row(result['request_id'])
    def claim(self):
        import account_run_control
        return account_run_control.claim_next_account_run_request(WORKER,allowed_run_types=['account_session'])
    def admit(self,q,legacy=False,**overrides):
        b=q['metadata_safe']['device_lock_binding']
        args=dict(p_request_id=q['id'],p_worker_id=WORKER,p_assignment_id=ASSIGNMENT,p_device_id=DEVICE,
          p_app_instance_id=APP,p_expected_package='com.instagram.android',p_scheduled_window_start=None,
          p_scheduled_window_end=None,p_lease_seconds=120)
        if not legacy:args['p_expected_lease_id']=b['lease_id']
        args.update(overrides)
        return self.rpc('admit_account_run_attempt_v1',args)
    def reject_before_device(self,q):
        self.rpc('complete_account_run_request',dict(p_request_id=q['id'],p_worker_id=WORKER,p_status='blocked',
          p_error_code='account_not_active',p_error_message_safe='Account session atomic admission rejected before Worker spawn.'))
        from auto_restart_device_lock import release_device_lock
        r=release_device_lock(device_id=DEVICE,worker_id=WORKER,request_id=q['id'],expected_lease_id=q['metadata_safe']['device_lock_binding']['lease_id'],account_id=A)
        self.assertTrue(r.get('released'),r)
    def test_00_reproduce_before_patch_then_identical_request_passes(self):
        self.assertEqual(self.auth()['status'],'armed')
        q=self.enqueue();q=self.claim()
        self.assertEqual(self.admit(q)['reason'],'account_not_active')
        self.assertIsNone(self.row(q['id'])['run_id'])
        self.sql(MIGRATION.read_text())
        time.sleep(.4)  # PostgREST notification reload, only this disposable process.
        result=self.admit(q)
        self.assertTrue(result['ok'],(result,self.rpc('account_package_runtime_contract_status',{'p_account_id':A})))
        self.assertEqual(result['irreversible_work_state'],'PRE_DEVICE')
        self.assertEqual(self.sql(f"select admin_lifecycle_status from ig_accounts where id='{A}'"),'paused')
        self.assertEqual(self.admit(self.row(q['id']))['run_id'],result['run_id'])
    def test_01_canonical_dispatcher_admission_and_legacy_overload(self):
        from account_run_request_consumer import _admit_account_session_v1
        for legacy in (False,True):
            self.sql('select restriction_test.reset()');q=self.enqueue();q=self.claim()
            if legacy:self.assertTrue(self.admit(q,legacy=True)['ok'])
            else:
                run=_admit_account_session_v1(request_id=q['id'],worker_id=WORKER,
                  dispatch_ctx=dict(assignment_id=ASSIGNMENT,device_id=DEVICE,app_instance_id=APP,package_name='com.instagram.android'),
                  device_lock_binding=q['metadata_safe']['device_lock_binding'])
                self.assertEqual(run,self.row(q['id'])['run_id'])
    def test_02_historical_rejection_recovers_once_and_keeps_request(self):
        q=self.enqueue();q=self.claim();self.reject_before_device(q);before=self.row(q['id'])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:self.rpc('restore_restriction_admission_credit_v1'),range(4)))
        self.assertEqual(sum(r['restriction_admission_credits_restored'] for r in results),1)
        self.assertEqual(before,self.row(q['id']))
        a=self.auth();self.assertEqual(a['status'],'armed');self.assertEqual(a['retry_generation'],1)
        self.assertEqual(a['metadata_safe']['restriction_admission_credit_v1']['request_id'],q['id'])
        self.assertTrue(self.rpc('incident_resume_authorization_preflight_v2',{'p_authorization_id':a['id']})['next_tick_eligible'])
        q2=self.enqueue();self.assertNotEqual(q['id'],q2['id']);q2=self.claim()
        self.reject_before_device(q2)
        self.assertEqual(self.rpc('restore_restriction_admission_credit_v1')['restriction_admission_credits_restored'],0)
        self.assertEqual(self.auth()['status'],'consumed')
    def test_03_paused_business_request_and_corrupt_proofs_rejected(self):
        q=self.enqueue();q=self.claim()
        original=q['metadata_safe']
        mutations=[('flag_string',lambda m:m.update(restriction_preflight_only='true')),
          ('missing_auth',lambda m:m.pop('authorization_id')),
          ('cross_account',lambda m:m['resume_plan'].update(account_id=DEVICE)),
          ('business_phase',lambda m:m['resume_plan']['phases_to_run'].update(follow=True)),
          ('quota',lambda m:m['resume_plan']['quota_remaining'].update(total=1)),
          ('instance',lambda m:m['resume_plan'].update(app_instance_id=DEVICE)),
          ('worker',lambda m:m.update(expected_worker_sha='b'*40)),
          ('ordinary',lambda m:(m.pop('restriction_preflight_only'),m['resume_plan'].pop('restriction_preflight_only')))]
        for name,change in mutations:
            m=copy.deepcopy(original);change(m)
            self.sql(f"update account_run_requests set metadata_safe='{json.dumps(m)}'::jsonb where id='{q['id']}'")
            with self.subTest(name=name):self.assertFalse(self.admit(self.row(q['id']))['ok'])
        self.assertEqual(self.sql(f"select count(*) from ig_runs where account_id='{A}' and id<>'{PRIOR}'"),'0')
    def test_04_expired_auth_and_independent_manual_pause_block(self):
        cases=["update incident_resume_authorizations set expires_at=now()-interval '1 minute'",
          "update ig_account_settings set manual_stop_requested=true",
          "update ig_account_settings set account_status='paused_manual_review'",
          "update instagram_account_restriction_holds set previous_admin_lifecycle_status='paused'",
          "update account_assignments set schedule_mode='manual_only',slot_kind='manual_only',starts_at=null,ends_at=null",
          "update account_run_requests set lease_expires_at=now()-interval '1 minute' where status='claimed'"]
        for query in cases:
            self.sql('select restriction_test.reset()');q=self.enqueue();q=self.claim();self.sql(query)
            with self.subTest(query=query):self.assertFalse(self.admit(q)['ok'])
    def test_05_real_preflight_failure_never_restores_credit(self):
        q=self.enqueue();q=self.claim();result=self.admit(q);self.assertTrue(result['ok'],(result,self.rpc('account_package_runtime_contract_status',{'p_account_id':A})))
        self.rpc('complete_account_run_request',dict(p_request_id=q['id'],p_worker_id=WORKER,p_status='blocked',p_error_code='account_not_active'))
        self.assertEqual(self.rpc('restore_restriction_admission_credit_v1')['restriction_admission_credits_restored'],0)
        self.assertEqual(self.sql('select status from instagram_account_restriction_holds'),'verification_required')
    def test_06_success_clears_hold_through_existing_rpc(self):
        q=self.enqueue();q=self.claim();result=self.admit(q);self.assertTrue(result['ok'],(result,self.rpc('account_package_runtime_contract_status',{'p_account_id':A})))
        # The real Worker calls this only after fresh physical proof. No device in this fixture.
        released=self.rpc('release_instagram_action_restriction_hold_v1',dict(p_account_id=A,p_incident_id=INCIDENT,p_preflight_run_id=result['run_id']))
        self.assertTrue(released['ok'],released)
        self.assertEqual(self.sql('select status from instagram_account_restriction_holds'),'cleared')
        self.assertEqual(self.sql(f"select admin_lifecycle_status from ig_accounts where id='{A}'"),'active')
    def test_07_migration_replay_and_internal_permissions(self):
        self.sql(MIGRATION.read_text())
        for name,args in [('restriction_resume_request_authorized_v1','uuid'),('restore_restriction_admission_credit_v1','')]:
            for role in ('anon','authenticated'):
                self.assertEqual(self.sql(f"select has_function_privilege('{role}','public.{name}({args})','execute')"),'f')
            self.assertEqual(self.sql(f"select has_function_privilege('service_role','public.{name}({args})','execute')"),'t')


# Load the exact unchanged Backend source through TypeScript's installed compiler.
# All HTTP is pinned to this disposable PostgREST and the real local admin handler.
NODE_TICK = r'''
const fs=require('node:fs'),http=require('node:http'),Module=require('node:module');
const ts=require('typescript');
const originalLoad=Module._load;
require.extensions['.ts']=()=>{throw Error('USE_LOCAL_TS_LOADER');};
Module._load=function(request,parent,isMain){
 if(request.startsWith('@/'))request=process.env.RESTRICTION_TEST_BACKEND_ROOT+request.slice(1);
 let file;try{file=Module._resolveFilename(request,parent,isMain);}catch{return originalLoad.call(this,request,parent,isMain);}
 if(typeof file!=='string'||!file.endsWith('.ts'))return originalLoad.call(this,request,parent,isMain);
 if(Module._cache[file])return Module._cache[file].exports;
 const mod=new Module(file,parent);mod.filename=file;mod.paths=Module._nodeModulePaths(require('node:path').dirname(file));
 Module._cache[file]=mod;
 const js=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText;
 mod._compile(js.replace(/import\.meta\.main/g,'false'),file+'.cjs');mod.loaded=true;return mod.exports;
};
const root=process.env.RESTRICTION_TEST_BACKEND_ROOT;
const worker=process.cwd();
const upstream=process.env.SUPABASE_URL;
if(!new URL(upstream).hostname.match(/^127\.0\.0\.1$/))throw Error('LOCAL_ONLY');
global.Deno={env:{get:n=>process.env[n]}};
process.env.ADMIN_DASHBOARD_INTERNAL_API_TOKEN='local-only-admin-test';
process.env.INSTAGRAM_RUN_CONTROL_DISPATCHER_WORKER_ID='run-dispatcher:local-cert';
const native=global.fetch,allowed=new Set([new URL(upstream).origin]);
global.fetch=async(input,init)=>{
 const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
 if(!allowed.has(url.origin))throw Error('NON_LOCAL_NETWORK_FORBIDDEN:'+url.origin);
 const response=await native(input,init);
 if(!response.ok)console.log('LOCAL_HTTP_FAILURE',url.pathname,await response.clone().text());
 return response;
};
const {handleRequest}=require(worker+'/supabase/functions/admin-dashboard/index.ts');
const server=http.createServer(async(req,res)=>{
 try{
  const data=[];for await(const c of req)data.push(c);
  const body=data.length?Buffer.concat(data):undefined;
  const headers={...req.headers};delete headers.host;
  const init={method:req.method,headers,...(body?{body,duplex:'half'}:{})};
  const r=req.url.startsWith('/admin')?await handleRequest(new Request(process.env.ADMIN_DASHBOARD_API_URL,init)):
     await fetch(upstream+req.url,init);
  res.writeHead(r.status,Object.fromEntries(r.headers));res.end(Buffer.from(await r.arrayBuffer()));
 }catch(e){res.writeHead(500);res.end(String(e));}
});
server.listen(0,'127.0.0.1',async()=>{
 try{
  const origin='http://127.0.0.1:'+server.address().port;allowed.add(origin);
  process.env.NEXT_PUBLIC_SUPABASE_URL=process.env.SUPABASE_URL=origin;
  process.env.ADMIN_DASHBOARD_API_URL=origin+'/admin';
  const {createSupabaseClient}=require(root+'/lib/supabase.ts');
  const db=createSupabaseClient();
  let result;
  if(process.env.RESTRICTION_TEST_NODE_MODE==='eligibility'){
    const {evaluateRunStartEligibility}=require(root+'/lib/instagram-dashboard/run-control.ts');
    result=await evaluateRunStartEligibility(process.env.RESTRICTION_TEST_ACCOUNT,'account_session',{trigger:'scheduler'});
  } else {
    const {runAutoRestartTick}=require(root+'/lib/instagram-dashboard/auto-restart-tick.ts');
    result=await runAutoRestartTick(db,{workerId:'run-dispatcher:local-cert',internal:true});
  }
  console.log('FIXTURE_RESULT='+JSON.stringify(result));
 }catch(e){console.error(e.stack);process.exitCode=1;}finally{server.close();}
});
'''

def run_backend(cls,mode='tick'):
    env={**os.environ,'NODE_PATH':'/Users/admin/Projects/boost-ai-frontend/node_modules',
         'RESTRICTION_TEST_NODE_MODE':mode,'RESTRICTION_TEST_ACCOUNT':A}
    r=subprocess.run(['/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node','-'],
        input=NODE_TICK,cwd=ROOT,env=env,text=True,capture_output=True,timeout=45)
    Path(os.environ['RESTRICTION_TEST_OUTPUT'],f'backend-{mode}.log').write_text(r.stdout+r.stderr)
    if r.returncode:raise AssertionError(r.stdout[-3000:]+r.stderr[-5000:])
    return json.loads(next(l.removeprefix('FIXTURE_RESULT=') for l in r.stdout.splitlines() if l.startswith('FIXTURE_RESULT=')))

def test_natural_tick_and_post_preflight_eligibility(self):
    result=run_backend(self)
    self.assertEqual(result['status'],200,result)
    self.assertEqual(result['result']['enqueued_count'],1,result)
    q=self.claim();self.assertIsNotNone(q)
    self.assertTrue(q['metadata_safe']['restriction_preflight_only'])
    admitted=self.admit(q);self.assertTrue(admitted['ok'],admitted)
    run=admitted['run_id']
    self.rpc('release_instagram_action_restriction_hold_v1',dict(p_account_id=A,p_incident_id=INCIDENT,p_preflight_run_id=run))
    self.sql(f"update ig_runs set status='completed',finished_at=now(),performance_summary='{{\"restriction_preflight_only\":true,\"physical_preflight_passed\":true,\"reason\":\"restriction_physical_preflight_passed\",\"business_actions_executed\":0}}' where id='{run}';")
    self.rpc('complete_account_run_request',dict(p_request_id=q['id'],p_worker_id=WORKER,p_status='completed'))
    from auto_restart_device_lock import release_device_lock
    release_device_lock(device_id=DEVICE,worker_id=WORKER,request_id=q['id'],expected_lease_id=q['metadata_safe']['device_lock_binding']['lease_id'],account_id=A)
    eligible=run_backend(self,'eligibility')
    self.assertTrue(eligible['ok'],eligible)
    # This physical attempt completed; no new automatic verification credit is permitted.
    self.assertEqual(self.rpc('restore_restriction_admission_credit_v1')['restriction_admission_credits_restored'],0)

RealDatabaseTests.test_08_real_natural_tick_and_post_preflight_eligibility=test_natural_tick_and_post_preflight_eligibility

def test_additional_authority_and_binding_negatives(self):
    cases=[
      "update incident_resume_authorizations set consumed_by_request_id=null",
      "update incident_resume_authorizations set source_request_id=null",
      "update incident_resume_authorizations set frozen_phase_plan='{}'",
      "update incident_resume_authorizations set expected_worker_sha=repeat('b',40)",
      "update instagram_account_restriction_holds set status='active'",
      "update account_incidents set status='open',resolved_at=null",
      "update account_incidents set metadata=metadata||'{\"security_incident\":true}'::jsonb",
      "update ig_account_settings set pause_account_days=1",
      "update ig_account_settings set pause_account_until='2099-01-01'",
      "update ig_accounts set admin_lifecycle_status='cancelled'",
      "update account_assignments set starts_at=starts_at+interval '6 hours',ends_at=ends_at+interval '6 hours'",
    ]
    for query in cases:
        self.sql('select restriction_test.reset()');q=self.enqueue();q=self.claim()
        self.sql(query)
        with self.subTest(query=query):self.assertFalse(self.admit(q)['ok'])
    for args in [dict(p_expected_lease_id=APP),dict(p_expected_package='wrong.package'),dict(p_app_instance_id=DEVICE),dict(p_assignment_id=APP)]:
        self.sql('select restriction_test.reset()');q=self.enqueue();q=self.claim()
        with self.subTest(args=args):self.assertFalse(self.admit(q,**args)['ok'])

def test_historical_credit_negative_proofs(self):
    cases=["update incident_resume_authorizations set expires_at=now()-interval '1 minute'",
      "update ig_account_settings set manual_stop_requested=true",
      "update account_run_requests set started_at=now() where status='blocked'",
      "update account_run_requests set error_code='restriction_physical_preflight_failed' where status='blocked'",
      "update account_run_requests set cancel_requested_at=now() where status='blocked'",
      "update account_run_requests set completed_at=null where status='blocked'",
      "update account_incidents set metadata=metadata||'{\"security_incident\":true}'::jsonb"]
    for query in cases:
        self.sql('select restriction_test.reset()');q=self.enqueue();q=self.claim();self.reject_before_device(q);self.sql(query)
        with self.subTest(query=query):
            self.assertEqual(self.rpc('restore_restriction_admission_credit_v1')['restriction_admission_credits_restored'],0)
            self.assertEqual(self.auth()['status'],'consumed')

def test_historical_credit_is_consumed_by_real_natural_tick(self):
    q=self.enqueue();q=self.claim();self.reject_before_device(q);before=self.row(q['id'])
    result=run_backend(self)
    self.assertEqual(result['status'],200,result)
    self.assertEqual(result['result']['enqueued_count'],1,result)
    self.assertEqual(self.row(q['id']),before)
    new=self.claim();self.assertNotEqual(new['id'],q['id'])
    self.assertEqual(self.auth()['retry_generation'],1)
    self.assertTrue(self.admit(new)['ok'])

RealDatabaseTests.test_09_additional_authority_and_binding_negatives=test_additional_authority_and_binding_negatives
RealDatabaseTests.test_10_historical_credit_negative_proofs=test_historical_credit_negative_proofs
RealDatabaseTests.test_11_historical_credit_is_consumed_by_real_natural_tick=test_historical_credit_is_consumed_by_real_natural_tick

def test_fence_remains_authoritative(self):
    tx='restriction-local-fence'
    fence=self.rpc('acquire_phonefarm_worker_promotion_fence_v1',dict(p_transaction_id=tx,p_candidate_sha='a'*40))
    self.assertTrue(fence.get('active'),fence)
    try:
        with self.assertRaisesRegex(Exception,'phonefarm_runtime_admission_fenced'):
            self.enqueue()
        self.assertEqual(self.auth()['status'],'armed')
    finally:self.rpc('release_phonefarm_worker_promotion_fence_v1',dict(p_transaction_id=tx,p_terminal_state='ROLLED_BACK'))

def test_predecessor_drift_is_rejected_without_overwrite(self):
    original=self.sql("select pg_get_functiondef('public.reconcile_resolved_incident_resume_windows_v2()'::regprocedure)")
    changed=original.replace('  v_base jsonb;','  v_base jsonb;\n  -- synthetic parallel change')
    self.sql(changed+';')
    try:
        rejected=self.sql(MIGRATION.read_text(),ok=False)
        self.assertNotEqual(rejected.returncode,0)
        self.assertIn('restriction_resume_dependency_drift',rejected.stderr)
        self.assertIn('synthetic parallel change',self.sql("select pg_get_functiondef('public.reconcile_resolved_incident_resume_windows_v2()'::regprocedure)"))
    finally:self.sql(original+';')

def test_second_unrelated_account(self):
    global A,DEVICE,APP,ASSIGNMENT,PRIOR,SOURCE,INCIDENT,PLAN
    names=('A','DEVICE','APP','ASSIGNMENT','PRIOR','SOURCE','INCIDENT','PLAN')
    saved={n:globals()[n] for n in names}
    for n,v in saved.items():globals()[n]=v.replace('000000009','000000008')
    try:
        b=Path(os.environ['RESTRICTION_TEST_BACKEND_ROOT'])/'tests/auto-restart-real-pg17-v2'
        for name in ('seed.sql','ready.sql','settings.sql'):
            self.sql((b/name).read_text().replace('000000009','000000008').replace('local_cert_identity','local_cert_identity_two').replace('local-source','local-source-two').replace('local-identity','local-identity-two').replace('Synthetic no ADB','Synthetic second no ADB'))
        self.sql(f"""insert into account_commercial_packages(account_id,package_code) values('{A}','growth');
          select reconcile_legacy_account_assignment_binding_v1('{A}','local-fixture-two');
          update account_assignments set starts_at=(select starts_at from restriction_test.assignments limit 1),
           ends_at=(select ends_at from restriction_test.assignments limit 1) where id='{ASSIGNMENT}';
          update account_session_resume_plans p set scheduled_window_start=a.starts_at,scheduled_window_end=a.ends_at,
           resume_window_key=a.id::text||':'||a.starts_at::text,restart_allowed=true,restart_block_reason=''
           from account_assignments a where p.assignment_id=a.id and p.account_id='{A}';
          update account_incidents set incident_type='instagram_account_restriction',reason='instagram_account_restriction_unknown_scope',severity='critical'
           where id='{INCIDENT}';""")
        self.rpc('transition_account_incident_human_review_v3',dict(p_incident_id=INCIDENT,p_action='resolve',p_expected_version=1,
          p_actor_type='admin',p_actor_id='580d7856-d60f-4838-a5f9-3b405d6ae79b',p_source='admin_dashboard',
          p_note='Synthetic second resolution',p_resolution_reason='cause_fixed',p_idempotency_key='local-second-resolution',
          p_expected_worker_sha='a'*40,p_cause_fixed_version='synthetic-local-restriction'))
        q=self.enqueue();q=self.claim();self.assertEqual(q['account_id'],A)
        self.assertTrue(self.admit(q)['ok'])
    finally:
        for n,v in saved.items():globals()[n]=v

RealDatabaseTests.test_12_fence_remains_authoritative=test_fence_remains_authoritative
RealDatabaseTests.test_13_predecessor_drift_rejected=test_predecessor_drift_is_rejected_without_overwrite
RealDatabaseTests.test_99_second_unrelated_account=test_second_unrelated_account


if __name__ == "__main__":
    unittest.main()
