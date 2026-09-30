"""Offline DM446 admission. Network/device boundaries are recording doubles."""
import ast
import copy
from datetime import datetime, timedelta, timezone
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import dm_certification_admission as admission

ROOT = Path(__file__).resolve().parents[1]


def claims():
    now = datetime.now(timezone.utc)
    return dict(admission.EXACT, schema=admission.SCHEMA, permit_id=str(uuid4()),
                nonce=str(uuid4()), job_id=str(uuid4()), candidate_sha='a'*40,
                candidate_tree='b'*40, launcher_sha256='c'*64,
                message_sha256=admission.digest(b'salut test'),
                certification_not_before=(now-timedelta(minutes=1)).isoformat(),
                certification_expires_at=(now+timedelta(minutes=1)).isoformat())


def binding(c):
    return dict(permit_hash=admission.digest(admission.canonical(c)), run_id=str(uuid4()),
                request_id=str(uuid4()), lease_id=str(uuid4()), worker_id='dm446-cert-'+uuid4().hex,
                execution_id=str(uuid4()))


def prepared(c, b):
    return dict(account_id=c['account_id'], account_username=c['username'], run_id=b['run_id'],
                dm_type='outreach', session_status='prepared', exit_code=0, prepared_jobs_count=1,
                prepared_jobs=[dict(id=c['job_id'], account_id=c['account_id'],
                  recipient_username=c['recipient_username'], message_body=c['message_body'],
                  dm_type='outreach', reserved_by=b['worker_id'], status='reserved', attempts=0, max_attempts=1)])


class DMAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.c = claims(); self.b = binding(self.c)
        self.session = dict(claims=self.c, binding=self.b, pid=os.getpid(), dispatch_started=False)
        self.old_session, self.old_configured = admission._SESSION, admission._CONFIGURED
        admission._SESSION = None; admission._CONFIGURED = False

    def tearDown(self):
        admission._SESSION, admission._CONFIGURED = self.old_session, self.old_configured

    def test_configuration_uses_canonical_candidate_identity_and_is_once_only(self):
        import json,tempfile
        manifest={'candidate_identity':{'candidate_commit_sha':self.c['candidate_sha'],'candidate_tree_sha':self.c['candidate_tree']},'dm446_certification_permits':[self.c]}
        with tempfile.TemporaryDirectory(dir='/private/tmp',prefix='dm-config-') as tmp:
            path=Path(tmp)/'manifest.json';path.write_text(json.dumps(manifest))
            with patch('follow60_deployment_gate_v1.verify_deployment_candidate',return_value={'ok':True}),patch.object(admission,'_rpc',return_value=dict(ok=True,claims=self.c,binding=self.b,state='CHILD_STARTED',authority_live=True)) as rpc:
                admission.configure_from_signed_permit(manifest_path=path,signature_path=Path(tmp)/'signature',permit_id=self.c['permit_id'],binding=self.b)
                self.assertEqual(admission.current()['binding'],self.b)
                with self.assertRaisesRegex(admission.CertificationStop,'reconfigure_forbidden'):
                    admission.configure_from_signed_permit(manifest_path=path,signature_path=Path(tmp)/'signature',permit_id=self.c['permit_id'],binding=self.b)
                rpc.assert_called_once()

    def test_exact_claims(self):
        self.assertEqual(admission.validate_claims(self.c), self.c)
        self.assertEqual(admission.validate_binding(self.c, self.b), self.b)

    def test_new_recipient_exact_and_old_recipient_rejected(self):
        self.assertEqual(admission.EXACT['recipient_username'], 'nab_youss')
        for recipient in ('rex_gen_boost_ai', 'NAB_YOUSS', '@nab_youss', 'nab_youss ', 'other'):
            with self.subTest(recipient=recipient), self.assertRaises(admission.CertificationStop):
                admission.validate_claims(dict(self.c, recipient_username=recipient))

    def test_all_tuple_fields_are_strict(self):
        for key, value in admission.EXACT.items():
            with self.subTest(field=key):
                c = dict(self.c); c[key] = value+1 if type(value) is int else value+'x'
                with self.assertRaises(admission.CertificationStop): admission.validate_claims(c)

    def test_booleans_not_numbers(self):
        for key in ('max_jobs', 'max_send_attempts', 'version_code'):
            with self.subTest(field=key):
                c = dict(self.c); c[key] = True
                with self.assertRaises(admission.CertificationStop): admission.validate_claims(c)

    def test_claim_fields_and_hashes(self):
        for change in (lambda x:x.pop('nonce'), lambda x:x.update(extra=1),
                       lambda x:x.update(message_sha256='0'*64), lambda x:x.update(permit_id='a'),
                       lambda x:x.update(candidate_sha='g'*40), lambda x:x.update(launcher_sha256=None)):
            c=dict(self.c);change(c)
            with self.assertRaises(admission.CertificationStop): admission.validate_claims(c)

    def test_window_fail_closed(self):
        for value in ('', '2026-09-30T00:00:00', '2026-09-30T00:00:00.1234567Z',
                      '2026-02-30T00:00:00Z', '2026-09-30T00:00:00+00:99', None, True):
            with self.subTest(value=value), self.assertRaises(admission.CertificationStop): admission.utc(value)
        with self.assertRaises(admission.CertificationStop):
            admission.validate_claims(self.c, now=admission.utc(self.c['certification_expires_at']))

    def test_inactive_has_no_io(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(admission, '_rpc', side_effect=AssertionError('IO')):
            self.assertIsNone(admission.validate_runner(SimpleNamespace()))

    def test_present_unconfigured_never_falls_back(self):
        with patch.dict(os.environ, {'DM446_CERTIFICATION_PERMIT_ID':self.c['permit_id']}):
            with self.assertRaises(admission.CertificationStop): admission.validate_runner(SimpleNamespace())

    def test_context_cannot_cross_process_or_revoke(self):
        admission._SESSION = self.session
        self.session['pid'] += 1
        with self.assertRaises(admission.CertificationStop): admission.current()
        admission._CONFIGURED = True; admission.revoke_local()
        self.assertTrue(admission._CONFIGURED)
        with self.assertRaises(admission.CertificationStop): admission.current()

    def test_receipt_rejects_every_wrong_binding(self):
        for key in self.b:
            with self.subTest(key=key):
                b=dict(self.b);b[key]='wrong'
                with self.assertRaises(admission.CertificationStop):
                    admission.receipt(self.c,self.b,dict(ok=True,claims=self.c,binding=b,state='JOB_CLAIMED',authority_live=True),'JOB_CLAIMED')
        for state in ('REGISTERED','ADMITTED','TERMINAL','REVOKED'):
            with self.assertRaises(admission.CertificationStop):
                admission.receipt(self.c,self.b,dict(ok=True,claims=self.c,binding=self.b,state=state,authority_live=True),'JOB_CLAIMED')

    def test_job_field_binding_and_types(self):
        p=prepared(self.c,self.b)
        self.assertIs(admission.validate_job(self.c,self.b,p),p['prepared_jobs'][0])
        for key in p['prepared_jobs'][0]:
            bad=copy.deepcopy(p);bad['prepared_jobs'][0][key]='wrong'
            with self.subTest(key=key), self.assertRaises(admission.CertificationStop): admission.validate_job(self.c,self.b,bad)
        for key in ('attempts','max_attempts'):
            bad=copy.deepcopy(p);bad['prepared_jobs'][0][key]=False if key=='attempts' else True
            with self.assertRaises(admission.CertificationStop): admission.validate_job(self.c,self.b,bad)

    def test_empty_disabled_quota_and_duplicate_preparation(self):
        for state in ('no_quota','blocked_disabled','no_jobs'):
            p=prepared(self.c,self.b);p['session_status']=state;p['prepared_jobs']=[]
            with self.assertRaises(admission.CertificationStop): admission.validate_job(self.c,self.b,p)
        p=prepared(self.c,self.b);p['prepared_jobs']*=2
        with self.assertRaises(admission.CertificationStop): admission.validate_job(self.c,self.b,p)

    def test_actual_dispatch_is_single_use_and_prepared_object_preserved(self):
        admission._SESSION=self.session
        p=prepared(self.c,self.b);calls=[]
        def rpc(name, body):
            state='JOB_CLAIMED' if calls else 'CHILD_STARTED'
            return dict(ok=True,claims=self.c,binding=self.b,state=state,authority_live=True)
        def prepare(*args,**kwargs):
            calls.append(('prepare',kwargs));return p
        def dispatch(*args,**kwargs):
            self.assertIs(kwargs['prepared_outreach'],p);calls.append(('dispatch',kwargs));return 7
        with patch.dict('sys.modules', {
            'instagram_navigation':SimpleNamespace(_dm_446_context=lambda d:object()),
            'outreach_session_orchestrator':SimpleNamespace(prepare_outreach_session=prepare,dispatch_outreach_session=dispatch)
        }), patch.object(admission,'_rpc',side_effect=rpc):
            kwargs=dict(account_id=self.c['account_id'],account_username=self.c['username'],run_id=self.b['run_id'],verified_username=self.c['username'])
            self.assertEqual(admission.dispatch(object(),**kwargs),7)
            with self.assertRaises(admission.CertificationStop): admission.dispatch(object(),**kwargs)
        self.assertEqual([x[0] for x in calls],['prepare','dispatch'])
        self.assertEqual(calls[0][1]['max_jobs_override'],1)

    def test_actual_orchestrator_and_claim_composition(self):
        from contextlib import ExitStack
        import outreach_session_orchestrator as orchestrator
        import dm_sender_engine as sender
        import supabase_client as backend
        import config
        admission._SESSION=self.session;admission._CONFIGURED=True
        job=prepared(self.c,self.b)['prepared_jobs'][0]
        events=[]
        def claim(*args,**kwargs):
            events.append(('claim',args,kwargs));return job
        def send(d,**kwargs):
            events.append(('sender',kwargs))
            self.assertEqual(kwargs['prepared_jobs'],[job])
            self.assertIs(kwargs['prepared_jobs'][0],job)
            self.assertEqual(kwargs['max_jobs'],1)
            return 0,{'sender_status':'completed','jobs_claimed_count':1,'jobs_sent_count':1}
        day=backend.sast_business_day_window()[0]
        with ExitStack() as st:
            st.enter_context(patch.object(backend,'get_account_dm_settings',return_value=dict(outreach_enabled=True,outreach_per_session_limit=1,outreach_per_day_limit=1,total_dm_per_day_limit=1)))
            st.enter_context(patch.object(backend,'get_account_dm_counter_today',return_value=dict(counter_date=day,outreach_sent_count=0,total_dm_sent_count=0)))
            stale=st.enter_context(patch.object(backend,'requeue_stale_outreach_dm_jobs',return_value=[]))
            st.enter_context(patch.object(backend,'claim_dm_job_by_id',side_effect=claim))
            fallback=st.enter_context(patch.object(backend,'claim_next_dm_job',side_effect=AssertionError('no substitution')))
            st.enter_context(patch.object(config,'DM_SENDER_RESERVED_BY',self.b['worker_id']))
            st.enter_context(patch.dict(os.environ,{'DM_SENDER_ONLY_JOB_ID':self.c['job_id'],'OUTREACH_DM_REAL_SEND_ENABLED':'true'}))
            st.enter_context(patch.object(orchestrator,'run_dm_sender_send',side_effect=send))
            st.enter_context(patch('instagram_navigation._dm_446_context',return_value={'qualified':True}))
            st.enter_context(patch.object(admission,'_rpc',side_effect=[dict(ok=True,claims=self.c,binding=self.b,state=state,authority_live=True) for state in ('CHILD_STARTED','JOB_CLAIMED')]))
            self.assertEqual(admission.dispatch(object(),account_id=self.c['account_id'],account_username=self.c['username'],run_id=self.b['run_id'],verified_username=self.c['username']),0)
            fallback.assert_not_called()
            self.assertEqual(stale.call_args.args,(self.c['account_id'],))
        self.assertEqual([e[0] for e in events],['claim','sender'])
        self.assertIn(self.c['job_id'],str(events[0]))

    def test_real_main_exception_stops_without_ui_cleanup(self):
        import runner
        admission._CONFIGURED=True
        for error in (admission.CertificationStop('safe_reason'),RuntimeError('private backend payload')):
            with self.subTest(error=type(error).__name__), patch.object(runner,'_main_impl',side_effect=error), patch.object(runner,'log') as log, patch.object(runner.certification_admission,'is_enabled',return_value=False), patch.object(runner,'_return_with_cleanup',side_effect=AssertionError('no UI cleanup')):
                self.assertEqual(runner.main(),1)
                self.assertNotIn('private backend payload',str(log.call_args_list))
                self.assertIsNone(admission._SESSION)

    def test_runner_real_hooks_order(self):
        tree=ast.parse((ROOT/'runner.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_main_impl')
        locations={}
        for n in ast.walk(main):
            if isinstance(n,ast.Call):
                name=ast.unparse(n.func)
                locations.setdefault(name,[]).append(n.lineno)
        self.assertLess(locations['dm_certification.validate_runner'][0], locations['connect_device'][0])
        self.assertLess(locations['verify_active_instagram_account_matches_expected'][0], locations['dm_certification.dispatch'][0])
        self.assertEqual(len(locations['dm_certification.dispatch']),1)

    def test_real_send_function_never_retries_transport(self):
        import hashlib
        tree=ast.parse((ROOT/'instagram_navigation.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='send_dm_safe')
        for mode in ('verified','unverified','tap_exception','verification_exception','disabled','existing'):
            taps=[]
            def click():
                taps.append('send')
                if mode=='tap_exception':raise OSError('synthetic transport loss')
            def verify(*a,**k):
                if mode=='verification_exception':raise OSError('synthetic read loss')
                return mode=='verified','synthetic',{}
            env=dict(config=SimpleNamespace(ENABLE_REAL_DM_SEND=mode!='disabled',SEND_DM_SKIP_EXISTING_THREAD=True),
                hashlib=hashlib,Path=Path,log=lambda *a,**k:None,
                _dm_find_focus_composer=lambda d:object(),read_dm_composer_text=lambda d:'salut test',
                wait_for_dm_send_button_after_draft=lambda *a,**k:(SimpleNamespace(click=click),'ok',{}),
                _dm_screen_size_for_dm=lambda d:(1080,2340),_dm_composer_bounds_u2=lambda d:{},
                _dm_thread_message_signature=lambda *a:{},_dm_capture_send_debug_artifact=lambda *a:{},
                guard_instagram_action_rate_limit=lambda *a,**k:None,
                _dm_verify_outbound_message_after_send=verify,_dm_post_send_signal_poll=lambda *a,**k:(False,'synthetic'),
                _welcome_dm_thread_header_from_hierarchy=lambda x:'',_welcome_dm_thread_subtitle_from_hierarchy=lambda x:'',
                _evaluate_welcome_thread_identity=lambda **k:{'decision':'reject'},
                _dm_hierarchy_has_outbound_expected_message=lambda *a:False)
            unit=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),fn],type_ignores=[])
            exec(compile(ast.fix_missing_locations(unit),str(ROOT/'instagram_navigation.py'),'exec'),env)
            with self.subTest(mode=mode):
                if mode=='verification_exception':
                    with self.assertRaises(OSError):env['send_dm_safe'](object(),'nab_youss','salut test','empty_new_thread')
                else:
                    result=env['send_dm_safe'](object(),'nab_youss','salut test','existing_thread' if mode=='existing' else 'empty_new_thread')
                    self.assertEqual(result['sent'],mode=='verified')
                self.assertEqual(len(taps),0 if mode in ('disabled','existing') else 1)


if __name__ == '__main__': unittest.main()
