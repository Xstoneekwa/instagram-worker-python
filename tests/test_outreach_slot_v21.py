import unittest
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch, Mock
import outreach_slot_contract as contract
import test_outreach_slot_contract as fixtures
import test_outreach_slot_v2 as helpers
from supabase_client import SupabaseRestError

class DeadlineRaceTests(unittest.TestCase):
    def simulate(self, *, sent=25, action='before_send', refusal='deadline', cleanup_ok=True, write_fails=False, legacy=False):
        f=fixtures.SlotTests();f.setUp();b=f.budget(sent_confirmed_in_slot=sent)
        if action=='claim':
            b.context['execution_policy']={**b.context['execution_policy'],'mode':'bootstrap','slot_id':b.context['slot_id'],'target_dm':30}
        f.clock.advance(2399.999);calls=[];terminal={}
        def transport(name,payload):
            calls.append((name,payload))
            if name=='finish_outreach_slot_v2':
                if write_fails:raise ConnectionError('terminal write failed')
                if not terminal:
                    terminal.update(fixtures.terminal_receipt(b.context,sent=sent,reason=payload['p_reason'],cleanup=payload['p_cleanup_ok']))
                return dict(terminal)
            if action=='claim' and name=='outreach_boundary_v21' and payload['p_action']=='context':
                return {**{k:b.context[k] for k in ('request_id','account_id','slot_id','slot_end')},
                    'schema':'OUTREACH_BOUNDARY_V21','action':'context','ok':True,
                    'result':{**b.context,'sent_confirmed_in_slot':sent}}
            f.clock.advance(.002)
            if legacy:raise SupabaseRestError('supabase_rpc_failed',status=400,detail='outreach_slot_expired_or_changed')
            if isinstance(refusal,Exception):raise refusal
            envelope={k:b.context[k] for k in ('request_id','account_id','slot_id','slot_end')}
            envelope.update(schema='OUTREACH_BOUNDARY_V21',action=action)
            if refusal=='deadline':return {**envelope,'ok':False,'reason_code':'SLOT_DEADLINE_REACHED','server_observed_at':datetime.fromtimestamp(f.clock.now,timezone.utc).isoformat()}
            if refusal=='success':return {**envelope,'ok':True,'result':{'id':'job'}}
            if refusal=='malformed':return {'ok':False,'reason_code':'SLOT_DEADLINE_REACHED'}
            return {**envelope,'ok':False,'reason_code':refusal}
        b.rpc=transport if legacy else lambda name,payload:contract.call_slot_rpc_v21(transport,b.context,name,payload)
        def invoke():
            if action=='context':return b.refresh()
            if action=='claim':return b.claim()
            return b.before_send('job')
        env={'_main_with_existing_handlers':invoke,'_CURRENT_DEVICE':SimpleNamespace(press=Mock()),
             'set_wait_event_callback':Mock(),'time':SimpleNamespace(monotonic=lambda:f.clock.mono),
             'log':Mock(),'_CURRENT_RUN_ID':'run'}
        cleanup=helpers.actual_function('runner.py','_outreach_cleanup_and_finish',env)
        env['_outreach_cleanup_and_finish']=cleanup
        main=helpers.actual_function('runner.py','main',env)
        with patch.object(contract,'active',return_value=b),patch('device.shell',return_value=(0 if cleanup_ok else 1,'','')),patch('cooperative_business_stop.StopContext.from_env',return_value=None):
            code=main()
        return code,terminal,calls,b

    def test_exact_before_and_after(self):
        code,t,_,_=self.simulate(legacy=True)
        self.assertEqual((code,t['stop_reason'],t['cleanup_ok']),(1,'technical_failure',True))
        for action in ('context','claim','before_send'):
            code,t,calls,_=self.simulate(action=action)
            self.assertEqual((code,t['stop_reason'],t['remaining_target'],t['business_failure']),(0,'slot_deadline',5,False))
            self.assertEqual(len([c for c in calls if c[0]=='outreach_boundary_v21']),2 if action=='claim' else 1)

    def test_full_target_deadline_normal(self):
        code,t,_,_=self.simulate(sent=30)
        self.assertEqual((code,t['partial_completion'],t['remaining_target']),(0,False,0))

    def test_other_failures_remain_technical_even_after_end(self):
        errors=[ConnectionRefusedError('connection refused'),TimeoutError('DB timeout'),
                SupabaseRestError('permission_denied',status=403),
                SupabaseRestError('rpc_failed',status=400,detail='unexpected SQL slot error'),
                SupabaseRestError('rpc_internal',status=500),
                SupabaseRestError('rpc_failed',status=400,detail='SLOT_DEADLINE_REACHED'),
                'WRONG_ASSIGNMENT','MISSING_ASSIGNMENT','WRONG_SLOT','WRONG_PACKAGE','WRONG_OWNERSHIP','malformed']
        for error in errors:
            with self.subTest(error=error):
                code,t,_,_=self.simulate(refusal=error)
                self.assertEqual((code,t['stop_reason'],t['business_failure']),(1,'technical_failure',True))

    def test_cleanup_and_receipt_failures_not_masked(self):
        code,t,_,_=self.simulate(cleanup_ok=False)
        self.assertEqual((code,t['stop_reason'],t['cleanup_ok']),(1,'cleanup_failure',False))
        code,t,calls,_=self.simulate(write_fails=True)
        self.assertEqual(code,1);self.assertEqual(t,{})
        self.assertEqual([n for n,p in calls],['outreach_boundary_v21','finish_outreach_slot_v2'])

    def test_successful_rpc_crossing_end_no_send(self):
        code,t,calls,_=self.simulate(refusal='success')
        self.assertEqual((code,t['stop_reason']),(0,'slot_deadline'))
        # before_send raises before returning to its tap caller; no tap in test.
        self.assertEqual(len(calls),2)

    def test_terminal_retry_reuses_same_receipt_and_lineage(self):
        code,t,calls,b=self.simulate()
        self.assertEqual(b.finish(cleanup_ok=True,cleanup_ms=1,run_id="run"),t)
        self.assertEqual(len(calls),2)
        self.assertEqual(b.target,30);self.assertEqual(b.sent,25)

    def test_local_at_and_after_boundary_no_rpc(self):
        for delta in (0,.001):
            f=fixtures.SlotTests();f.setUp();b=f.budget();f.clock.advance(2400+delta)
            for operation in (b.claim,lambda:b.before_send('job')):
                with self.assertRaisesRegex(contract.SlotBlocked,'^slot_deadline$'):operation()
            self.assertEqual(f.calls,[])

    def test_deadline_proof_must_match_binding_and_server_time(self):
        f=fixtures.SlotTests();f.setUp();c=f.ctx
        good={k:c[k] for k in ('request_id','account_id','slot_id','slot_end')}
        good.update(schema='OUTREACH_BOUNDARY_V21',action='context',ok=False,reason_code='SLOT_DEADLINE_REACHED',server_observed_at=c['slot_end'])
        for changes in ({'slot_id':'wrong'},{'request_id':'wrong'},{'slot_end':'wrong'},
                        {'account_id':'wrong'},{'server_observed_at':c['slot_start']},
                        {'reason_code':'unexpected_slot_error'},{'ok':None}):
            with self.subTest(changes=changes),self.assertRaises(contract.SlotBlocked) as err:
                contract.call_slot_rpc_v21(lambda *a:{**good,**changes},c,'outreach_slot_context_v1',{})
            self.assertNotEqual(str(err.exception),'slot_deadline')
