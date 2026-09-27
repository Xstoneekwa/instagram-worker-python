"""Offline admission contract, actual runner hook AST and subprocess durability."""
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import certification_admission as ca

ROOT = Path(__file__).resolve().parents[1]
BASE = 'a0d995be8df3ea1a773c572bc900aa1d3a360df6'
BINDING = dict(permit_id='test-permit', permit_hash='signed-hash', run_id='run',
               request_id='request', account_id='account', worker_id='worker', lease_id='lease',
               device_id='device', serial='serial', package='package', version_name='372', candidate_sha=BASE)
KEY = ('run', 'account', 'ct', 'alice')


def child_admit(directory, key, queue, hold=None):
    try:
        store = ca._PermitStore(directory, BINDING)
        queue.put(store.admit(key))
        if hold is not None:
            hold.wait(10)
        store.close()
    except ca.CertificationStop as exc:
        queue.put(exc.reason)


def child_crash(directory):
    ca._PermitStore(directory, BINDING).admit(KEY)
    os._exit(19)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ca._PermitStore(self.tmp.name, BINDING)
        self.addCleanup(self.store.close)
        ca._REQUESTED = True
        now = datetime.now(timezone.utc)
        self.claims = {'certification_not_before': (now-timedelta(minutes=1)).isoformat(),
                       'certification_expires_at': (now+timedelta(minutes=5)).isoformat()}
        ca._SESSION = (self.claims, dict(BINDING), self.store)
        self.addCleanup(self.reset)

    def reset(self):
        ca._REQUESTED = False
        ca._SESSION = None

    def gate(self, candidate=None, **overrides):
        import runner
        args=dict(run_id='run',request_id='request',account_id='account',source_ct='ct',
                  candidate={'username':'alice'} if candidate is None else candidate,normalize_handle=runner._norm_ig_handle,
                  valid_handle=runner._is_plausible_public_ig_username)
        args.update(overrides)
        with patch.object(ca, '_canonical_authority'):
            return ca.before_candidate_transport(**args)

    def test_first_second_and_no_transport(self):
        transports=[]
        self.assertEqual(self.gate(),'FIRST_ADMISSION_GRANTED');transports.append('alice')
        with self.assertRaises(ca.CertificationStop) as error:
            self.gate({'username':'bob'});transports.append('bob')
        self.assertEqual(error.exception.reason,'SECOND_CANDIDATE_BLOCKED')
        self.assertEqual(transports,['alice'])
        self.assertEqual(self.store._read()['state'],'TERMINAL')

    def test_first_failure_before_cycle_complete_does_not_reset(self):
        self.gate()
        # No cycle completion API exists; a failure cannot refund admission.
        with self.assertRaises(ca.CertificationStop):self.gate({'username':'bob'})

    def test_same_candidate_recovery(self):
        self.gate()
        self.assertEqual(self.gate({'username':'@ALICE'}),'SAME_CANDIDATE_REENTRY_GRANTED')

    def test_unknown_identity_terminal_and_no_skip(self):
        calls=[]
        with self.assertRaises(ca.CertificationStop) as error:
            self.gate({'username':'vf_row_1','username_pending_profile_read':True});calls.append('open')
        self.assertEqual(error.exception.reason,ca.IDENTITY_UNPROVED)
        self.assertEqual(calls,[])
        with self.assertRaises(ca.CertificationStop):self.gate()

    def test_identity_matrix(self):
        bad=[{}, {'username':'alice','username_pending_profile_read':True},
             {'username':'alice','visual_candidate_id':'row'}, {'resolved_username_hint':'alice'},
             {'username':'alice','resolved_username_hint':'bob'}, {'username':'vfp_12'},
             {'username':True}, {'username':'__visual1'}, {'username':'alice','resolved_username_hint':123}]
        for n,candidate in enumerate(bad):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as tmp:
                store=ca._PermitStore(tmp,dict(BINDING,permit_id=str(n)))
                ca._SESSION=(self.claims,dict(BINDING),store)
                with self.assertRaises(ca.CertificationStop) as error:self.gate(candidate)
                self.assertEqual(error.exception.reason,ca.IDENTITY_UNPROVED)
                store.close()
        ca._SESSION=(self.claims,dict(BINDING),self.store)

    def test_wrong_run_request_account(self):
        for field in ('run_id','request_id','account_id'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                store=ca._PermitStore(tmp,BINDING);ca._SESSION=(self.claims,dict(BINDING),store)
                with self.assertRaises(ca.CertificationStop) as error:self.gate(**{field:'other'})
                self.assertEqual(error.exception.reason,'BINDING_MISMATCH');store.close()
        ca._SESSION=(self.claims,dict(BINDING),self.store)

    def test_disabled_no_identity_disk_backend_or_validation(self):
        ca._REQUESTED=False
        with patch.object(ca,'validate_execution_context',side_effect=AssertionError('read')):
            self.assertEqual(ca.before_candidate_transport(run_id=None,request_id=None,account_id=None,
                source_ct=None,candidate=None,normalize_handle=None,valid_handle=None),'DISABLED')

    def test_terminal_no_reuse(self):
        self.store.terminal('failed');self.store.close()
        again=ca._PermitStore(self.tmp.name,BINDING)
        try:
            with self.assertRaises(ca.CertificationStop) as error:again.admit(KEY)
            self.assertEqual(error.exception.reason,'PERMIT_ALREADY_TERMINAL')
        finally:again.close()

    def test_rebind_every_field_rejected(self):
        self.store.admit(KEY);self.store.close()
        for field in ('run_id','request_id','lease_id','device_id','serial','account_id','package','candidate_sha','permit_hash'):
            with self.subTest(field=field), self.assertRaises(ca.CertificationStop):
                ca._PermitStore(self.tmp.name,dict(BINDING,**{field:'other'}))

    def test_missing_state_tombstone_is_not_reissued(self):
        self.store.close();self.store.path.unlink()
        with self.assertRaises(ca.CertificationStop):ca._PermitStore(self.tmp.name,BINDING)
        self.assertFalse(self.store.path.exists())

    def test_corrupt_state_is_not_reset(self):
        self.store.close();self.store.path.write_text('{')
        with self.assertRaises(ca.CertificationStop):ca._PermitStore(self.tmp.name,BINDING)
        self.assertEqual(self.store.path.read_text(),'{')

    def test_same_process_threads_one_first_grant(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            result=list(pool.map(self.store.admit,[KEY,KEY]))
        self.assertEqual(result.count('FIRST_ADMISSION_GRANTED'),1)
        self.assertEqual(result.count('SAME_CANDIDATE_REENTRY_GRANTED'),1)

    def test_other_executor_same_key_cannot_reenter(self):
        self.store.admit(KEY)
        ctx=multiprocessing.get_context('spawn');q=ctx.Queue();p=ctx.Process(target=child_admit,args=(self.tmp.name,KEY,q))
        p.start();p.join(15);self.assertEqual(p.exitcode,0);self.assertEqual(q.get(timeout=2),'PERMIT_INVALID');q.close()

    def test_crash_then_restart_same_and_different(self):
        self.store.close()
        ctx=multiprocessing.get_context('spawn');p=ctx.Process(target=child_crash,args=(self.tmp.name,))
        p.start();p.join(15);self.assertEqual(p.exitcode,19)
        again=ca._PermitStore(self.tmp.name,BINDING)
        try:
            self.assertEqual(again.admit(KEY),'SAME_CANDIDATE_REENTRY_GRANTED')
            with self.assertRaises(ca.CertificationStop) as error:again.admit((*KEY[:-1],'bob'))
            self.assertEqual(error.exception.reason,'SECOND_CANDIDATE_BLOCKED')
        finally:again.close()

    def test_concurrent_first_different_candidates_one_wins(self):
        self.store.close()
        ctx=multiprocessing.get_context('spawn');q=ctx.Queue();hold=ctx.Event()
        ps=[ctx.Process(target=child_admit,args=(self.tmp.name,(*KEY[:-1],name),q,hold)) for name in ('alice','bob')]
        for p in ps:p.start()
        results=[q.get(timeout=15),q.get(timeout=15)];hold.set()
        for p in ps:p.join(15);self.assertEqual(p.exitcode,0)
        self.assertEqual(results.count('FIRST_ADMISSION_GRANTED'),1)
        self.assertEqual(results.count('PERMIT_INVALID'),1);q.close()

    def test_missing_ct_and_hint_only_never_open(self):
        for ct,candidate in (('',{'username':'alice'}), ('ct',{'resolved_username_hint':'alice'})):
            with tempfile.TemporaryDirectory() as tmp:
                store=ca._PermitStore(tmp,BINDING);ca._SESSION=(self.claims,dict(BINDING),store)
                with self.assertRaises(ca.CertificationStop) as error:self.gate(candidate,source_ct=ct)
                self.assertEqual(error.exception.reason,ca.IDENTITY_UNPROVED);store.close()
        ca._SESSION=(self.claims,dict(BINDING),self.store)

    def test_symlink_state_is_rejected_without_target_write(self):
        self.store.close();self.store.path.unlink()
        target=Path(self.tmp.name)/'target';target.write_text('kept')
        self.store.path.symlink_to(target)
        with self.assertRaises(ca.CertificationStop):ca._PermitStore(self.tmp.name,BINDING)
        self.assertEqual(target.read_text(),'kept')

    def test_durability_failure_never_returns_grant(self):
        with patch.object(self.store,'_sync_parent',side_effect=OSError('fsync failed')):
            with self.assertRaises(ca.CertificationStop) as error:self.gate()
            self.assertEqual(error.exception.reason,'PERMIT_INVALID')
        # A replace may already be durable: never return AVAILABLE or refund it.
        self.assertEqual(self.store._read()['state'],'CONSUMED_BOUND')
        with self.assertRaises(ca.CertificationStop):self.store.admit((*KEY[:-1],'bob'))

    def test_request_cancel_and_expiry_fail_closed(self):
        import auto_restart_device_lock as locks,account_run_control as requests,supabase_client as runs
        lease={k:BINDING[k] for k in ('device_id','worker_id','request_id','account_id','lease_id')}
        lease['lease_expires_at']=self.claims['certification_expires_at']
        req=dict(id='request',account_id='account',run_id='run',claimed_by='worker',status='running',
                 lease_expires_at=lease['lease_expires_at'],metadata_safe={'device_lock_binding':dict(lease,generation='lease')})
        run=dict(id='run',account_id='account',status='running')
        with patch.object(locks,'_load_device_lock',return_value=lease),patch.object(requests,'get_account_run_request',return_value=req),patch.object(runs,'load_run_row',return_value=run):
            req['cancel_reason']='operator_stop'
            with self.assertRaises(ca.CertificationStop):ca._canonical_authority(BINDING)
            req.pop('cancel_reason');req['lease_expires_at']='2000-01-01T00:00:00Z'
            with self.assertRaises(ca.CertificationStop):ca._canonical_authority(BINDING)

    def test_expired_window(self):
        self.claims['certification_expires_at']='2000-01-01T00:00:00Z'
        with self.assertRaises(ca.CertificationStop):self.gate()

    def test_invalid_configuration_stays_enabled(self):
        self.reset()
        with self.assertRaises(ca.CertificationStop):ca.configure_from_signed_permit(
            manifest_path='/does/not/exist',signature_path='/does/not/exist',permit_id='x',execution_binding={},state_directory=self.tmp.name)
        self.assertTrue(ca.is_enabled())
        with self.assertRaises(ca.CertificationStop):ca.validate_execution_context()

    def test_canonical_authority_rejects_wrong_and_terminal_rows(self):
        import auto_restart_device_lock as locks,account_run_control as requests,supabase_client as runs
        lease={k:BINDING[k] for k in ('device_id','worker_id','request_id','account_id','lease_id')}
        lease['lease_expires_at']=self.claims['certification_expires_at']
        req=dict(id='request',account_id='account',run_id='run',claimed_by='worker',status='running')
        req.update(lease_expires_at=lease['lease_expires_at'],metadata_safe={'device_lock_binding':dict(lease,generation='lease')})
        run=dict(id='run',account_id='account',status='running')
        with patch.object(locks,'_load_device_lock',return_value=lease),patch.object(requests,'get_account_run_request',return_value=req),patch.object(runs,'load_run_row',return_value=run):
            ca._canonical_authority(BINDING)
            for field in ('device_id','worker_id','request_id','account_id','lease_id'):
                saved=lease[field];lease[field]='wrong'
                with self.assertRaises(ca.CertificationStop):ca._canonical_authority(BINDING)
                lease[field]=saved
            run['status']='completed'
            with self.assertRaises(ca.CertificationStop):ca._canonical_authority(BINDING)


class RunnerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=(ROOT/'runner.py').read_text();cls.tree=ast.parse(cls.source)
        cls.baseline=ast.parse(subprocess.check_output(['git','show',BASE+':runner.py'],cwd=ROOT).decode())

    def test_every_actual_open_is_immediately_guarded(self):
        found=[]
        for parent in ast.walk(self.tree):
            for _,body in ast.iter_fields(parent):
                if not isinstance(body,list):continue
                for i,stmt in enumerate(body):
                    if not isinstance(stmt,ast.stmt):continue
                    direct=[]
                    if isinstance(stmt,ast.Assign):direct=[stmt.value]
                    if isinstance(stmt,ast.If) and isinstance(stmt.test,ast.UnaryOp):direct=[stmt.test.operand]
                    for call in direct:
                        if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id in ('open_follower_profile_from_list','open_visual_follower_candidate_from_screenshot'):
                            hook=body[i-1];self.assertIsInstance(hook,ast.If)
                            self.assertEqual(ast.unparse(hook.test),'certification_admission.is_enabled()')
                            self.assertIn('certification_admission.before_candidate_transport',ast.unparse(hook))
                            found.append((hook,stmt))
        self.assertEqual(len(found),3)
        # Execute the actual hook + actual call AST at each site, with bounded I/O doubles.
        for hook,stmt in found:
            for enabled in (False,True):
                calls=[]
                class Gate:
                    def is_enabled(self):return enabled
                    def before_candidate_transport(self,**kw):raise ca.CertificationStop(ca.IDENTITY_UNPROVED)
                env=dict(certification_admission=Gate(),run_id='run',_CURRENT_RUN_REQUEST_ID='request',
                         _CURRENT_ACCOUNT_ID='account',account_id='account',source_profile_username='ct',
                         best={},pick={},_norm_ig_handle=lambda v:v,_is_plausible_public_ig_username=lambda v:True,
                         d=object(),pkg='package',open_follower_profile_from_list=lambda *a,**k:calls.append('open') or True,
                         open_visual_follower_candidate_from_screenshot=lambda *a,**k:calls.append('open') or {})
                node=ast.If(test=stmt.test,body=[ast.Pass()],orelse=[]) if isinstance(stmt,ast.If) else stmt
                code=compile(ast.fix_missing_locations(ast.Module(body=[hook,node],type_ignores=[])),'actual-runner-hook','exec')
                if enabled:
                    with self.assertRaises(ca.CertificationStop):exec(code,env)
                    self.assertEqual(calls,[])
                else:exec(code,env);self.assertEqual(calls,['open'])

    def test_normal_runner_ast_unchanged_except_authorized_hooks(self):
        class Strip(ast.NodeTransformer):
            def visit_Import(self,n):
                return None if any(a.name=='certification_admission' for a in n.names) else n
            def visit_If(self,n):
                if ast.unparse(n.test)=='certification_admission.is_enabled()':return None
                return self.generic_visit(n)
            def visit_FunctionDef(self,n):
                if n.name=='main':
                    # main audited independently; remove only the dedicated handler/finally and guard.
                    n=self.generic_visit(n);t=n.body[0]
                    t.handlers=[h for h in t.handlers if ast.unparse(h.type)!='certification_admission.CertificationStop']
                    t.finalbody=[]
                    return n
                return self.generic_visit(n)
        neutral = '    # Early acquisition aborts can publish before the Followers loop starts.\n    _followers_loop_finally_stop = ""\n\n'
        self.assertEqual(self.source.count(neutral), 1)
        candidate=Strip().visit(ast.parse(self.source.replace(neutral, "", 1)))
        self.assertEqual(ast.dump(candidate,include_attributes=False),ast.dump(self.baseline,include_attributes=False))

    def test_approved_neutral_init_rejects_other_runner_deltas(self):
        original = self.source
        neutral = '    # Early acquisition aborts can publish before the Followers loop starts.\n    _followers_loop_finally_stop = ""\n\n'
        for altered in (original.replace(neutral, '', 1), original + neutral,
                        original.replace(neutral, neutral.replace('= ""', '= "changed"'), 1),
                        original + '\nUNRELATED_PRODUCTION_CHANGE = True\n'):
            with self.subTest(altered_hash=hashlib.sha256(altered.encode()).hexdigest()):
                self.source = altered
                try:
                    with self.assertRaises(AssertionError):
                        self.test_normal_runner_ast_unchanged_except_authorized_hooks()
                finally:
                    self.source = original

    def test_main_stop_reaches_terminal_without_ui_cleanup(self):
        import runner
        exc=ca.CertificationStop(ca.IDENTITY_UNPROVED)
        with patch.object(ca,'is_enabled',return_value=True),patch.object(ca,'validate_execution_context'),patch.object(ca,'mark_terminal') as end,patch.object(runner,'_main_impl',side_effect=exc),patch.object(runner,'log'),patch.object(runner,'_update_run_status_safe') as update,patch.object(runner,'_return_with_cleanup',side_effect=AssertionError('UI cleanup')),patch.object(runner,'_CURRENT_RUN_ID','run'):
            self.assertEqual(runner.main(),1);end.assert_called_once()
            self.assertEqual(update.call_args.kwargs['performance_summary']['root_failure_code'],ca.IDENTITY_UNPROVED)
            self.assertEqual(update.call_args.kwargs['status'],'stopped')

    # Exact hunks authorized by a0-ui-446 Approval1 and its test amendment.
    # No function/AST exclusions: reversing these must reconstruct all A0 bytes.
    _APPROVED_446_HUNKS = {'device.py': [(b'            )\n    # Version-scoped evidence reuse: no additional device read on either version.\n    if (vn, vc) == ("446.0.0.49.77", 385211304) or "_instagram_446_observed_context" in vars(d):\n        from instagram_ui_446_compat import retain_context\n        retain_context(d, serial=serial, package=pkg, version_name=vn,\n                       version_code=vc, unsafe=unsafe)\n    if not unsafe and serial and pkg and type(vc) is int:\n        vars(d)["_instagram_search_observed_version"] = (id(d), serial, pkg, vn, vc)\n    return payload\n', b'            )\n    return payload\n'), (b'    # Revoke before any read: a failed re-check cannot retain Search authority.\n    vars(d)["_instagram_search_observed_version"] = None\n', b'')], 'instagram_navigation.py': [(b'\ndef _follow_ui_state_and_fresh_hierarchy(\n    d: u2.Device, *, expected_username: str = ""\n) -> tuple[str, str]:\n    """Use one hierarchy as both Follow-state proof and late-popup authority."""\n', b'\ndef _follow_ui_state_and_fresh_hierarchy(d: u2.Device) -> tuple[str, str]:\n    """Use one hierarchy as both Follow-state proof and late-popup authority."""\n'), (b'        hierarchy = ""\n    if "_instagram_446_observed_context" in vars(d):\n        from instagram_ui_446_compat import context_matches, relationship_from_xml\n        package = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")\n        context = vars(d)["_instagram_446_observed_context"]\n        if not hierarchy or not context_matches(context, d, get_device_serial(d), package):\n            return "unknown", hierarchy\n        return relationship_from_xml(hierarchy, package=package,\n                                     expected_username=expected_username), hierarchy\n    if not hierarchy:\n', b'        hierarchy = ""\n    if not hierarchy:\n'), (b'            _fresh_state, _fresh_verify_hierarchy = (\n                _follow_ui_state_and_fresh_hierarchy(d, expected_username=str(username or ""))\n            )\n', b'            _fresh_state, _fresh_verify_hierarchy = (\n                _follow_ui_state_and_fresh_hierarchy(d)\n            )\n'), (b'    if not _open_446_explore_search_entry(d, pkg):\n        return False\n\n', b''), (b'def _open_446_explore_search_entry(d: u2.Device, package: str) -> bool:\n    """One qualified446 Explore header action;372 remains an I/O-free no-op."""\n    from run_latency_observability_v1 import _TimedProxy, _UI_METHODS\n    from instagram_ui_446_compat import context_matches, explore_search_entry_from_xml\n    key = "_instagram_446_observed_context"\n\n    def authority():\n        owner = d\n        if isinstance(d, _TimedProxy):\n            if type(d) is not _TimedProxy or vars(d).get("_kind") != "device":\n                raise ValueError("search_context_owner_unproved")\n            owner = vars(d).get("_raw")\n            if owner is None or isinstance(owner, _TimedProxy):\n                raise ValueError("search_context_owner_unproved")\n        observed = vars(owner).get("_instagram_search_observed_version")\n        serial = get_device_serial(owner)\n        if (type(observed) is not tuple or len(observed) != 5\n                or observed[:3] != (id(owner), serial, package)\n                or not serial or not package or type(observed[4]) is not int):\n            raise ValueError("search_version_authority_unproved")\n        if observed[3:] == ("372.0.0.48.60", 377611059):\n            if key in vars(owner):\n                raise ValueError("search_version_authority_contradictory")\n            return owner, observed, None\n        context = vars(owner).get(key)\n        if (observed[3:] != ("446.0.0.49.77", 385211304)\n                or not context_matches(context, owner, serial, package)):\n            raise ValueError("version_context_unproved")\n        return owner, observed, context\n\n    try:\n        owner, observed, context = authority()\n        if context is None:\n            return True\n        proof = explore_search_entry_from_xml(d.dump_hierarchy(compressed=False), package=package)\n        if proof is None:\n            raise ValueError("explore_entry_unproved")\n        # Re-resolve the exact live ancestry, never any Search/Not-now match.\n        control = d(resourceId=package + ":id/explore_action_bar")\n        for suffix in ("explore_action_bar_container", "action_bar_search_hints_text_layout",\n                       "action_bar_search_edit_text"):\n            control = control.child(resourceId=package + ":id/" + suffix)\n        # child() returns a raw selector in the existing instrumentation API.\n        # Keep the final scoped click observable without changing that API.\n        if type(d) is _TimedProxy:\n            control = _TimedProxy(control, kind="uiautomator",\n                                  emit=vars(d)["_emit"], methods=_UI_METHODS)\n        if control.count != 1:\n            raise ValueError("search_entry_not_unique")\n        tab = d(resourceId=package + ":id/search_tab")\n        if tab.count != 1:\n            raise ValueError("search_tab_not_unique")\n        def matches(info, node, *, search_tab_text=False):\n            from instagram_ui_446_compat import _bounds\n            b = info.get("bounds") or {}\n            return (info.get("resourceName") == node["resource-id"]\n                    and info.get("className") == node["class"]\n                    and (info.get("text", "") == node.get("text", "")\n                         or (search_tab_text and info.get("text") is None\n                             and node.get("text") == ""))\n                    and info.get("contentDescription", "") == node.get("content-desc", "")\n                    and info.get("enabled") is True and info.get("clickable") is True\n                    and info.get("selected") == (node.get("selected") == "true")\n                    and info.get("focused") == (node.get("focused") == "true")\n                    and tuple(b.get(k) for k in ("left", "top", "right", "bottom")) == _bounds(node))\n        if not matches(tab.info, proof["search_tab"], search_tab_text=True):\n            raise ValueError("search_tab_changed")\n        if not matches(control.info, proof["action_bar_search_edit_text"]):\n            raise ValueError("search_entry_changed")\n        current_owner, current_observed, current_context = authority()\n        if (current_owner is not owner or current_observed is not observed\n                or current_context is not context):\n            raise ValueError("version_context_changed")\n        control.click()\n        log("info", "open_search_446_header_clicked", editable_postcondition="pending")\n        return True\n    except Exception as exc:\n        log("warning", "open_search_446_entry_rejected", reason=str(exc) if isinstance(exc, ValueError)\n            else type(exc).__name__)\n        return False\n\n\n', b'')]}

    def _assert_navigation_a0_with_exact_446_delta(self, name, candidate):
        normalized = candidate
        for current, original in self._APPROVED_446_HUNKS.get(name, ()):
            self.assertEqual(normalized.count(current), 1,
                             "approved446 hunk missing, duplicated or modified: " + name)
            normalized = normalized.replace(current, original, 1)
        self.assertEqual(normalized, subprocess.check_output(
            ['git', 'show', BASE + ':' + name], cwd=ROOT))

    def test_navigation_exact_a0_bytes(self):
        for name in ('device.py','own_profile_navigation.py','account_identity_guard.py','instagram_navigation.py','follow60_mainline_v2.py'):
            self._assert_navigation_a0_with_exact_446_delta(name, (ROOT/name).read_bytes())

    def test_navigation_freeze_rejects_unrelated_byte_changes(self):
        for name in ('device.py','own_profile_navigation.py','account_identity_guard.py','instagram_navigation.py','follow60_mainline_v2.py'):
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self._assert_navigation_a0_with_exact_446_delta(
                    name, (ROOT/name).read_bytes() + b'\n# unrelated change\n')

    def test_navigation_freeze_rejects_changed_or_missing_approved_hunk(self):
        for name, hunks in self._APPROVED_446_HUNKS.items():
            source = (ROOT/name).read_bytes()
            for current, original in hunks:
                for replacement in (original, current + b'# changed hunk\n'):
                    with self.subTest(name=name), self.assertRaises(AssertionError):
                        self._assert_navigation_a0_with_exact_446_delta(
                            name, source.replace(current, replacement, 1))

    def test_navigation_freeze_rejects_duplicate_approved_hunk(self):
        for name, hunks in self._APPROVED_446_HUNKS.items():
            for current, _ in hunks:
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    self._assert_navigation_a0_with_exact_446_delta(
                        name, (ROOT/name).read_bytes() + current)

    def test_no_ui_dependency_or_bare_except(self):
        tree=ast.parse((ROOT/'certification_admission.py').read_text())
        names=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertFalse(any(n and (n.startswith('ui_') or 'navigation' in n or n=='runner') for n in names))
        self.assertFalse(any(isinstance(n,ast.ExceptHandler) and n.type is None for n in ast.walk(self.tree)))
        self.assertTrue(issubclass(ca.CertificationStop,BaseException));self.assertFalse(issubclass(ca.CertificationStop,Exception))


if __name__=='__main__':unittest.main()
