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

# Exact inverse of the signed XML-self delta. Full restored historical hashes
# enforce position and all other bytes; no whole-file exclusion or hash replacement.
SELF_XML_BASE_HASHES = {'runner.py': '290ee55729856b9d3f4f821bce43b4e0a10a4903755c95fcfd4b7dbdb90e6829', 'account_session_orchestrator.py': 'ff22ffb59d7b1dd73e502fe32d374084c76b9a63b2a75ca692c7b94081c6839c'}
SELF_XML_EXACT_HUNKS = {'runner.py': [('    auto_restart_resume_policy: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    account_session_snapshot: Any | None = None,\n    verified_current_account_username: str | None = None,\n) -> int:\n    """\n    Source profile → source followers list → follower profile → FOLLOW SAFE V1 → return to list.\n', '    auto_restart_resume_policy: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    account_session_snapshot: Any | None = None,\n) -> int:\n    """\n    Source profile → source followers list → follower profile → FOLLOW SAFE V1 → return to list.\n'), ('    global _RUNTIME_SKIPPED_USERNAMES\n    global _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION\n    _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = 0\n    _verified_current_account_key = _norm_ig_handle(verified_current_account_username or "")\n    _snapshot_payload = (\n        account_session_snapshot.to_payload()\n        if hasattr(account_session_snapshot, "to_payload")\n', '    global _RUNTIME_SKIPPED_USERNAMES\n    global _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION\n    _VISUAL_FOLLOWERS_OPEN_COUNT_THIS_SESSION = 0\n    _snapshot_payload = (\n        account_session_snapshot.to_payload()\n        if hasattr(account_session_snapshot, "to_payload")\n'), ('                    return None\n                for c in cand_list:\n                    ckey = _norm_ig_handle(str(c.get("username") or ""))\n                    if _verified_current_account_key and ckey == _verified_current_account_key:\n                        log(\n                            "info",\n                            "followers_candidate_skipped_source_account_self",\n                            username=c.get("username"),\n                            source_profile_username=source_profile_username,\n                            skip_reason="source_account_self",\n                        )\n                        _expl_v1.note_visible_skip("source_account_self")\n                        continue\n                    if ckey == src_key:\n                        log(\n                            "info",\n', '                    return None\n                for c in cand_list:\n                    ckey = _norm_ig_handle(str(c.get("username") or ""))\n                    if ckey == src_key:\n                        log(\n                            "info",\n'), ('        ),\n    )\n\n    verified_current_account_username = None\n    if (\n        supabase_mode\n        and not unfollow_session_run\n', '        ),\n    )\n\n    if (\n        supabase_mode\n        and not unfollow_session_run\n'), ('                target_username=account_username or None,\n            )\n            return _return_with_cleanup(d, 75)\n        if isinstance(identity.actual_logged_in_username, str):\n            verified_current_account_username = identity.actual_logged_in_username or None\n\n    if account_session_run and supabase_mode and account_id:\n        from auto_restart_runtime import load_resume_policy_from_env\n', '                target_username=account_username or None,\n            )\n            return _return_with_cleanup(d, 75)\n\n    if account_session_run and supabase_mode and account_id:\n        from auto_restart_runtime import load_resume_policy_from_env\n'), ('        )\n        asess_code = dispatch_account_session(\n            d,\n            verified_current_account_username=verified_current_account_username,\n            account_id=account_id,\n            account_username=account_username,\n            run_id=run_id or None,\n', '        )\n        asess_code = dispatch_account_session(\n            d,\n            account_id=account_id,\n            account_username=account_username,\n            run_id=run_id or None,\n'), ('        )\n        eng_code = _run_followers_list_engine_session(\n            d,\n            verified_current_account_username=verified_current_account_username,\n            source_profile_username=source_profile_username,\n            account_id=account_id,\n            run_id=run_id,\n', '        )\n        eng_code = _run_followers_list_engine_session(\n            d,\n            source_profile_username=source_profile_username,\n            account_id=account_id,\n            run_id=run_id,\n')], 'account_session_orchestrator.py': [('    auto_restart_resume_policy: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    account_session_snapshot: AccountSessionCPlusSnapshot | None = None,\n    verified_current_account_username: str | None = None,\n) -> dict[str, Any]:\n    total_targets = len(follow_targets)\n    max_targets = _resolve_max_follow_targets_per_run(total_targets, max_targets_per_run)\n', '    auto_restart_resume_policy: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    account_session_snapshot: AccountSessionCPlusSnapshot | None = None,\n) -> dict[str, Any]:\n    total_targets = len(follow_targets)\n    max_targets = _resolve_max_follow_targets_per_run(total_targets, max_targets_per_run)\n'), ('            "worker_runtime_identity": worker_runtime_identity,\n            "account_session_snapshot": account_session_snapshot,\n        }\n        if verified_current_account_username is not None:\n            call_kwargs["verified_current_account_username"] = verified_current_account_username\n        if follow60_canary_active:\n            call_kwargs.update(\n                {\n', '            "worker_runtime_identity": worker_runtime_identity,\n            "account_session_snapshot": account_session_snapshot,\n        }\n        if follow60_canary_active:\n            call_kwargs.update(\n                {\n'), ('    follow60_mainline_active: bool = False,\n    follow60_business_session_binding: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    verified_current_account_username: str | None = None,\n) -> int:\n    global _LAST_ACCOUNT_SESSION_SUMMARY\n    _LAST_ACCOUNT_SESSION_SUMMARY = {}\n', '    follow60_mainline_active: bool = False,\n    follow60_business_session_binding: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n) -> int:\n    global _LAST_ACCOUNT_SESSION_SUMMARY\n    _LAST_ACCOUNT_SESSION_SUMMARY = {}\n'), ('                )\n                rotation_result = _run_follow_target_rotation(\n                    d,\n                    verified_current_account_username=verified_current_account_username,\n                    account_id=aid,\n                    account_username=uname,\n                    run_id=run_id,\n', '                )\n                rotation_result = _run_follow_target_rotation(\n                    d,\n                    account_id=aid,\n                    account_username=uname,\n                    run_id=run_id,\n'), ('    follow60_mainline_active: bool = False,\n    follow60_business_session_binding: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n    verified_current_account_username: str | None = None,\n) -> int:\n    return run_account_session(\n        d,\n        verified_current_account_username=verified_current_account_username,\n        account_id=account_id,\n        account_username=account_username,\n        run_id=run_id,\n', '    follow60_mainline_active: bool = False,\n    follow60_business_session_binding: dict[str, Any] | None = None,\n    worker_runtime_identity: WorkerRuntimeIdentity | None = None,\n) -> int:\n    return run_account_session(\n        d,\n        account_id=account_id,\n        account_username=account_username,\n        run_id=run_id,\n')]}


def restore_exact_self_xml_delta(name, source):
    for approved, original in SELF_XML_EXACT_HUNKS[name]:
        if source.count(approved) != 1:
            raise AssertionError('self_xml_hunk_missing_altered_or_duplicated:' + name)
        source = source.replace(approved, original, 1)
    if hashlib.sha256(source.encode()).hexdigest() != SELF_XML_BASE_HASHES[name]:
        raise AssertionError('self_xml_unapproved_delta_or_position:' + name)
    return source


# Exact signed Like Verify delta. Whole-source hashes pin hunk locations as well
# as bytes; no function/range exclusion and no generic whitespace normalization.
LIKE_BUDGET_SOURCE_SHA256 = 'fe3fba2c7b5051a4e5f95adfd98a316ac294de3b87d2b26e6eaa84b0617e2d30'
LIKE_BUDGET_BASE_SHA256 = 'db4a882e77feef02ff8db074e869bfbb79a37680e1428c2567704b3c33342c37'
LIKE_BUDGET_EXACT_HUNKS = [('\ndef _ui_post_viewer_action_button_liked_strict(\n    d: u2.Device,\n    *,\n    semantic_deadline: float | None = None,\n) -> tuple[bool, str, float, dict[str, Any]]:\n    """Trusted already-liked: exact Unlike (or localized) on the action heart button."""\n    checks: list[tuple[str, Callable[[], object], float]] = [\n', '\ndef _ui_post_viewer_action_button_liked_strict(\n    d: u2.Device,\n) -> tuple[bool, str, float, dict[str, Any]]:\n    """Trusted already-liked: exact Unlike (or localized) on the action heart button."""\n    checks: list[tuple[str, Callable[[], object], float]] = [\n'), ('        ("ui_text_unlike_exact", lambda: d(textMatches="(?i)^Unlike$"), 0.9),\n    ]\n    for method, pred, conf in checks:\n        if semantic_deadline is not None and time.perf_counter() >= semantic_deadline:\n            return False, "", 0.0, {}\n        try:\n            el = pred()\n            if not el.exists(timeout=0.14):\n', '        ("ui_text_unlike_exact", lambda: d(textMatches="(?i)^Unlike$"), 0.9),\n    ]\n    for method, pred, conf in checks:\n        try:\n            el = pred()\n            if not el.exists(timeout=0.14):\n'), ('\ndef _ui_post_viewer_liked_state_for_verify(\n    d: u2.Device,\n    *,\n    semantic_deadline: float | None = None,\n) -> tuple[bool, str, float, dict[str, Any]]:\n    """\n    Post-tap verification only: accept Unlike / Double tap to unlike on the real heart control.\n    Does not use broad engagement or \'Liked by\' (filtered in _verify_post_tap_text_is_unlike_semantic).\n    """\n    ok, method, conf, proof = _ui_post_viewer_action_button_liked_strict(\n        d, semantic_deadline=semantic_deadline\n    )\n    if ok:\n        return True, method, conf, proof\n\n', '\ndef _ui_post_viewer_liked_state_for_verify(\n    d: u2.Device,\n) -> tuple[bool, str, float, dict[str, Any]]:\n    """\n    Post-tap verification only: accept Unlike / Double tap to unlike on the real heart control.\n    Does not use broad engagement or \'Liked by\' (filtered in _verify_post_tap_text_is_unlike_semantic).\n    """\n    ok, method, conf, proof = _ui_post_viewer_action_button_liked_strict(d)\n    if ok:\n        return True, method, conf, proof\n\n'), ('        ),\n    ]\n    for method_label, pred, conf in probes:\n        if semantic_deadline is not None and time.perf_counter() >= semantic_deadline:\n            return False, "", 0.0, {}\n        try:\n            el = pred()\n            if not el.exists(timeout=0.16):\n', '        ),\n    ]\n    for method_label, pred, conf in probes:\n        try:\n            el = pred()\n            if not el.exists(timeout=0.16):\n'), ('                return True, "ui_action_button_unlike", conf, proof\n        except Exception:\n            continue\n\n    if semantic_deadline is not None and time.perf_counter() >= semantic_deadline:\n        return False, "", 0.0, {}\n\n    try:\n        el = d(resourceId="com.instagram.android:id/row_feed_button_like")\n', '                return True, "ui_action_button_unlike", conf, proof\n        except Exception:\n            continue\n\n    try:\n        el = d(resourceId="com.instagram.android:id/row_feed_button_like")\n'), ('                fail_snap["semantic_result"] = continuity_reason\n                break\n            ok_ui, method_ui, conf_ui, proof_ui = (\n                _ui_post_viewer_liked_state_for_verify(d, semantic_deadline=semantic_deadline)\n            )\n            if proof_ui:\n                fail_snap["last_ui_candidate_method"] = str(\n', '                fail_snap["semantic_result"] = continuity_reason\n                break\n            ok_ui, method_ui, conf_ui, proof_ui = (\n                _ui_post_viewer_liked_state_for_verify(d)\n            )\n            if proof_ui:\n                fail_snap["last_ui_candidate_method"] = str(\n')]


def restore_exact_like_budget_delta(source):
    if hashlib.sha256(source.encode()).hexdigest() != LIKE_BUDGET_SOURCE_SHA256:
        raise AssertionError("like_budget_source_changed")
    for approved, original in LIKE_BUDGET_EXACT_HUNKS:
        if source.count(approved) != 1:
            raise AssertionError("like_budget_hunk_missing_altered_or_duplicated")
        source = source.replace(approved, original, 1)
    if hashlib.sha256(source.encode()).hexdigest() != LIKE_BUDGET_BASE_SHA256:
        raise AssertionError("like_budget_reverse_not_exact_baseline")
    return source


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
        hook = '        stable, proof_reason = build_stable_candidate_proof_v2(\n            binding=binding,\n            device_context=d,\n'
        original_hook = '        stable, proof_reason = build_stable_candidate_proof_v2(\n            binding=binding,\n'
        self.assertEqual(self.source.count(hook), 1)
        self.assertEqual(self.source.count('            device_context=d,\n'), 1)
        candidate=Strip().visit(ast.parse(restore_exact_self_xml_delta("runner.py", self.source).replace(neutral, "", 1).replace(hook, original_hook, 1)))
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

    def test_exact_v2_context_wiring_rejects_other_runner_deltas(self):
        original=self.source
        keyword='            device_context=d,\n'
        for altered in (original.replace(keyword,'',1), original.replace(keyword,keyword*2,1),
                        original.replace(keyword,'            device_context=other,\n',1),
                        original+'\nUNRELATED_V2_CHANGE = True\n'):
            with self.subTest(altered=hashlib.sha256(altered.encode()).hexdigest()):
                self.source=altered
                try:
                    with self.assertRaises(AssertionError):
                        self.test_normal_runner_ast_unchanged_except_authorized_hooks()
                finally:self.source=original

    def test_main_stop_reaches_terminal_without_ui_cleanup(self):
        import runner
        exc=ca.CertificationStop(ca.IDENTITY_UNPROVED)
        with patch.object(ca,'is_enabled',return_value=True),patch.object(ca,'validate_execution_context'),patch.object(ca,'mark_terminal') as end,patch.object(runner,'_main_impl',side_effect=exc),patch.object(runner,'log'),patch.object(runner,'_update_run_status_safe') as update,patch.object(runner,'_return_with_cleanup',side_effect=AssertionError('UI cleanup')),patch.object(runner,'_CURRENT_RUN_ID','run'):
            self.assertEqual(runner.main(),1);end.assert_called_once()
            self.assertEqual(update.call_args.kwargs['performance_summary']['root_failure_code'],ca.IDENTITY_UNPROVED)
            self.assertEqual(update.call_args.kwargs['status'],'stopped')

    # Exact hunks authorized by a0-ui-446 Approval1 and its test amendment.
    # No function/AST exclusions: reversing these must reconstruct all A0 bytes.
    _APPROVED_446_HUNKS = {'device.py': [(b'            )\n    # Version-scoped evidence reuse: no additional device read on either version.\n    if (vn, vc) == ("446.0.0.49.77", 385211304) or "_instagram_446_observed_context" in vars(d):\n        from instagram_ui_446_compat import retain_context\n        retain_context(d, serial=serial, package=pkg, version_name=vn,\n                       version_code=vc, unsafe=unsafe)\n    if not unsafe and serial and pkg and type(vc) is int:\n        vars(d)["_instagram_search_observed_version"] = (id(d), serial, pkg, vn, vc)\n    return payload\n', b'            )\n    return payload\n'), (b'    # Revoke before any read: a failed re-check cannot retain Search authority.\n    vars(d)["_instagram_search_observed_version"] = None\n', b'')], 'instagram_navigation.py': [(b'\ndef _follow_ui_state_and_fresh_hierarchy(\n    d: u2.Device, *, expected_username: str = ""\n) -> tuple[str, str]:\n    """Use one hierarchy as both Follow-state proof and late-popup authority."""\n', b'\ndef _follow_ui_state_and_fresh_hierarchy(d: u2.Device) -> tuple[str, str]:\n    """Use one hierarchy as both Follow-state proof and late-popup authority."""\n'), (b'        hierarchy = ""\n    if "_instagram_446_observed_context" in vars(d):\n        from instagram_ui_446_compat import context_matches, relationship_from_xml\n        package = str(getattr(config, "INSTAGRAM_PACKAGE", "") or "")\n        context = vars(d)["_instagram_446_observed_context"]\n        if not hierarchy or not context_matches(context, d, get_device_serial(d), package):\n            return "unknown", hierarchy\n        return relationship_from_xml(hierarchy, package=package,\n                                     expected_username=expected_username), hierarchy\n    if not hierarchy:\n', b'        hierarchy = ""\n    if not hierarchy:\n'), (b'            _fresh_state, _fresh_verify_hierarchy = (\n                _follow_ui_state_and_fresh_hierarchy(d, expected_username=str(username or ""))\n            )\n', b'            _fresh_state, _fresh_verify_hierarchy = (\n                _follow_ui_state_and_fresh_hierarchy(d)\n            )\n'), (b'    if not _open_446_explore_search_entry(d, pkg):\n        return False\n\n', b''), (b'def _open_446_explore_search_entry(d: u2.Device, package: str) -> bool:\n    """One qualified446 Explore header action;372 remains an I/O-free no-op."""\n    from run_latency_observability_v1 import _TimedProxy, _UI_METHODS\n    from instagram_ui_446_compat import context_matches, explore_search_entry_from_xml\n    key = "_instagram_446_observed_context"\n\n    def authority():\n        owner = d\n        if isinstance(d, _TimedProxy):\n            if type(d) is not _TimedProxy or vars(d).get("_kind") != "device":\n                raise ValueError("search_context_owner_unproved")\n            owner = vars(d).get("_raw")\n            if owner is None or isinstance(owner, _TimedProxy):\n                raise ValueError("search_context_owner_unproved")\n        observed = vars(owner).get("_instagram_search_observed_version")\n        serial = get_device_serial(owner)\n        if (type(observed) is not tuple or len(observed) != 5\n                or observed[:3] != (id(owner), serial, package)\n                or not serial or not package or type(observed[4]) is not int):\n            raise ValueError("search_version_authority_unproved")\n        if observed[3:] == ("372.0.0.48.60", 377611059):\n            if key in vars(owner):\n                raise ValueError("search_version_authority_contradictory")\n            return owner, observed, None\n        context = vars(owner).get(key)\n        if (observed[3:] != ("446.0.0.49.77", 385211304)\n                or not context_matches(context, owner, serial, package)):\n            raise ValueError("version_context_unproved")\n        return owner, observed, context\n\n    try:\n        owner, observed, context = authority()\n        if context is None:\n            return True\n        proof = explore_search_entry_from_xml(d.dump_hierarchy(compressed=False), package=package)\n        if proof is None:\n            raise ValueError("explore_entry_unproved")\n        # Re-resolve the exact live ancestry, never any Search/Not-now match.\n        control = d(resourceId=package + ":id/explore_action_bar")\n        for suffix in ("explore_action_bar_container", "action_bar_search_hints_text_layout",\n                       "action_bar_search_edit_text"):\n            control = control.child(resourceId=package + ":id/" + suffix)\n        # child() returns a raw selector in the existing instrumentation API.\n        # Keep the final scoped click observable without changing that API.\n        if type(d) is _TimedProxy:\n            control = _TimedProxy(control, kind="uiautomator",\n                                  emit=vars(d)["_emit"], methods=_UI_METHODS)\n        if control.count != 1:\n            raise ValueError("search_entry_not_unique")\n        tab = d(resourceId=package + ":id/search_tab")\n        if tab.count != 1:\n            raise ValueError("search_tab_not_unique")\n        def matches(info, node, *, search_tab_text=False, qualified_header=False):\n            from instagram_ui_446_compat import _bounds\n            b = info.get("bounds") or {}\n            return ((info.get("resourceName") == node["resource-id"]\n                     or (qualified_header and info.get("resourceName") is None\n                         and node["resource-id"] == package + ":id/action_bar_search_edit_text"))\n                    and info.get("className") == node["class"]\n                    and (info.get("text", "") == node.get("text", "")\n                         or (search_tab_text and info.get("text") is None\n                             and node.get("text") == ""))\n                    and info.get("contentDescription", "") == node.get("content-desc", "")\n                    and info.get("enabled") is True and info.get("clickable") is True\n                    and info.get("selected") == (node.get("selected") == "true")\n                    and info.get("focused") == (node.get("focused") == "true")\n                    and tuple(b.get(k) for k in ("left", "top", "right", "bottom")) == _bounds(node))\n        if not matches(tab.info, proof["search_tab"], search_tab_text=True):\n            raise ValueError("search_tab_changed")\n        # The unique, ancestry-scoped RID selector above proves header identity.\n        # UiObject fallback does not populate its optional resourceName echo.\n        if not matches(control.info, proof["action_bar_search_edit_text"], qualified_header=True):\n            raise ValueError("search_entry_changed")\n        current_owner, current_observed, current_context = authority()\n        if (current_owner is not owner or current_observed is not observed\n                or current_context is not context):\n            raise ValueError("version_context_changed")\n        control.click()\n        log("info", "open_search_446_header_clicked", editable_postcondition="pending")\n        return True\n    except Exception as exc:\n        log("warning", "open_search_446_entry_rejected", reason=str(exc) if isinstance(exc, ValueError)\n            else type(exc).__name__)\n        return False\n\n\n', b''), (b'def _post_open_intent_surface_from_final_proof(\n    evidence: dict[str, Any] | None,\n    *,\n    expected_package: str,\n    device_context: Any = None,\n)', b'def _post_open_intent_surface_from_final_proof(\n    evidence: dict[str, Any] | None,\n    *,\n    expected_package: str,\n)'), (b'    activity_lower = activity.lower()\n    if "instagram" not in activity_lower or "mainactivity" not in activity_lower:\n        # Extend only the existing, version-qualified original446 authority.\n        qualified_main_tab = False\n        if (package == "com.instagram.android" and device_context is not None\n                and activity in ("com.instagram.android.activity.MainTabActivity",\n                                 ".activity.MainTabActivity")):\n            try:\n                qualified_main_tab = _followers_row_activity_allowed(\n                    device_context, package, activity)\n            except (AttributeError, TypeError):\n                qualified_main_tab = False\n        if not qualified_main_tab:\n            return "", ""\n    return package, activity', b'    activity_lower = activity.lower()\n    if "instagram" not in activity_lower or "mainactivity" not in activity_lower:\n        return "", ""\n    return package, activity'), (b'                _post_open_intent_surface_from_final_proof(\n                    _intent_evidence,\n                    expected_package=pkg,\n                    device_context=d,\n                )', b'                _post_open_intent_surface_from_final_proof(\n                    _intent_evidence,\n                    expected_package=pkg,\n                )'), (b'    activity_lower = current_activity.lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n    if (not instagram_main_activity and package_exact\n            and current_package == "com.instagram.android"\n            and current_activity in ("com.instagram.android.activity.MainTabActivity",\n                                     ".activity.MainTabActivity")):\n        try:\n            instagram_main_activity = _followers_row_activity_allowed(\n                d, current_package, current_activity)\n        except (AttributeError, TypeError):\n            instagram_main_activity = False\n', b'    activity_lower = current_activity.lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n'), (b'    activity_lower = str(current_activity or "").lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n    if (not instagram_main_activity and package_exact\n            and current_package == "com.instagram.android"\n            and current_activity in ("com.instagram.android.activity.MainTabActivity",\n                                     ".activity.MainTabActivity")):\n        try:\n            instagram_main_activity = _followers_row_activity_allowed(\n                d, current_package, current_activity)\n        except (AttributeError, TypeError):\n            instagram_main_activity = False\n', b'    activity_lower = str(current_activity or "").lower()\n    instagram_main_activity = bool(\n        "instagram" in activity_lower and "mainactivity" in activity_lower\n    )\n'), (b'        stage_activity_allowed = bool(\n            "InstagramMainActivity" in str(stage_ctx.get("activity") or "")\n        )\n        if (not stage_activity_allowed\n                and str(stage_ctx.get("package") or "") == "com.instagram.android"\n                and str(stage_ctx.get("activity") or "") in (\n                    "com.instagram.android.activity.MainTabActivity",\n                    ".activity.MainTabActivity")\n                and str(stage_ctx.get("activity") or "")\n                == str(meta0.get("current_activity") or "")):\n            try:\n                stage_activity_allowed = _followers_row_activity_allowed(\n                    d, str(stage_ctx.get("package") or ""),\n                    str(stage_ctx.get("activity") or ""))\n            except (AttributeError, TypeError):\n                stage_activity_allowed = False\n        stage_scoped_open_context_ok = bool(\n', b'        stage_scoped_open_context_ok = bool(\n'), (b'            and stage_activity_allowed\n', b'            and "InstagramMainActivity"\n            in str(stage_ctx.get("activity") or "")\n'), (b'        activity_allowed = bool(\n            "instagram" in live_activity.lower() and "mainactivity" in live_activity.lower()\n        )\n        if (not activity_allowed and live_package == "com.instagram.android"\n                and live_activity in (\n                    "com.instagram.android.activity.MainTabActivity", ".activity.MainTabActivity")):\n            try:\n                activity_allowed = _followers_row_activity_allowed(d, live_package, live_activity)\n            except (AttributeError, TypeError):\n                activity_allowed = False\n        if not activity_allowed:\n            return False, "liketapcontext_viewer_mismatch", age_ms\n', b'        if "instagram" not in live_activity.lower() or "mainactivity" not in live_activity.lower():\n            return False, "liketapcontext_viewer_mismatch", age_ms\n')]}
    _APPROVED_446_HUNKS["instagram_navigation.py"].extend([(b'def _followers_row_activity_allowed(d, package: str, activity: str) -> bool:\n    """Local membership only; consumes the already retained version evidence."""\n    if not package:\n        return False\n    if activity == "com.instagram.mainactivity.InstagramMainActivity":\n        return True\n    if package != "com.instagram.android" or activity not in (\n        "com.instagram.android.activity.MainTabActivity", ".activity.MainTabActivity"\n    ):\n        return False\n    from run_latency_observability_v1 import _TimedProxy\n    from instagram_ui_446_compat import context_matches\n    owner = d\n    if isinstance(d, _TimedProxy):\n        if type(d) is not _TimedProxy or vars(d).get("_kind") != "device":\n            return False\n        owner = vars(d).get("_raw")\n        if owner is None or isinstance(owner, _TimedProxy):\n            return False\n    observed = vars(owner).get("_instagram_search_observed_version")\n    serial = get_device_serial(owner)\n    return (type(observed) is tuple and len(observed) == 5\n            and observed[:3] == (id(owner), serial, package) and bool(serial)\n            and type(observed[4]) is int\n            and observed[3:] == ("446.0.0.49.77", 385211304)\n            and context_matches(vars(owner).get("_instagram_446_observed_context"),\n                                owner, serial, package))\n\n\n', b''), (b'    if not _followers_row_activity_allowed(d, pkg, current_activity):\n', b'    if current_activity and "MainActivity" not in current_activity:\n')])

    _APPROVED_446_HUNKS["instagram_navigation.py"].append((b'    if not _followers_row_activity_allowed(d, expected_package, current_activity):\n', b'    if not (\n        "instagrammainactivity" in activity_norm\n        or activity_norm.endswith(".mainactivity")\n        or "profileactivity" in activity_norm\n    ):\n'))

    def _assert_navigation_a0_with_exact_446_delta(self, name, candidate):
        normalized = (restore_exact_like_budget_delta(candidate.decode()).encode()
                      if name == "instagram_navigation.py" else candidate)
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



class LikeBudgetExactFreezeTests(unittest.TestCase):
    def test_exact_hunks_reconstruct_signed_baseline(self):
        source = (ROOT / 'instagram_navigation.py').read_text()
        expected = subprocess.check_output(['git','show',
            '8519f1c16284a7f4f4185e898b0b281d8ea27e72:instagram_navigation.py'],cwd=ROOT,text=True)
        self.assertEqual(restore_exact_like_budget_delta(source),expected)
        for approved, original in LIKE_BUDGET_EXACT_HUNKS:
            self.assertEqual(source.count(approved),1)

    def test_missing_changed_duplicate_and_moved_hunks_fail(self):
        source = (ROOT / 'instagram_navigation.py').read_text()
        for approved, original in LIKE_BUDGET_EXACT_HUNKS:
            for mutated in (source.replace(approved,original,1),
                            source.replace(approved,approved+'# unauthorized byte\n',1),
                            source+approved,
                            source.replace(approved,'',1)+approved):
                with self.subTest(hunk=hashlib.sha256(approved.encode()).hexdigest()), self.assertRaises(AssertionError):
                    restore_exact_like_budget_delta(mutated)

    def test_unrelated_semantic_and_business_mutations_fail(self):
        source = (ROOT / 'instagram_navigation.py').read_text()
        names = ('_ui_post_viewer_action_button_liked_strict',
                 '_ui_post_viewer_liked_state_for_verify','visual_verify_post_liked',
                 '_visual_filled_heart_red_ratio','_hierarchy_liked_state_for_post_verify',
                 '_followers_row_activity_allowed','visual_like_open_post')
        tree = ast.parse(source)
        for name in names:
            node = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name == name)
            lines = source.splitlines(True)
            lines.insert(node.end_lineno,'# unauthorized semantic/budget/selector/ROI/XML/old446 delta\n')
            with self.subTest(name=name), self.assertRaises(AssertionError):
                restore_exact_like_budget_delta(''.join(lines))
        for old,new in [('timeout=0.14','timeout=0.15'),
                        ('row_feed_button_like','row_feed_button_wrong'),
                        ('semantic_deadline=semantic_deadline','semantic_deadline=None')]:
            self.assertIn(old,source)
            with self.subTest(change=old), self.assertRaises(AssertionError):
                restore_exact_like_budget_delta(source.replace(old,new,1))
        with self.assertRaises(AssertionError):
            restore_exact_like_budget_delta(source+'\n')

    def test_reentry_follow_mute_return_ct_v1_routing_remain_exact(self):
        hashes = {'follow_action_engine.py': '3240d8d6aa4f3ed59a3615706f8104644696910ca0e71a9f4e44598f7a6fb9df', 'follow60_mainline_v2.py': '572a6a9e66c6cf434ef7335675f85e73ab215593eb6e86d99f2677c1c7ba0652', 'runner.py': '290ee55729856b9d3f4f821bce43b4e0a10a4903755c95fcfd4b7dbdb90e6829', 'follow60_ordering_v2_behavioral_canary_v1.py': '57f75c698c550e8ac943ac7da67498058c5cbe2c8139200d8eb1966a892eb92e'}
        def assert_exact(name, content):
            if hashlib.sha256(content).hexdigest() != hashes[name]:
                raise AssertionError('business_companion_changed:' + name)
        for name in hashes:
            content = (ROOT/name).read_bytes()
            if name == "runner.py":
                content = restore_exact_self_xml_delta(name, content.decode()).encode()
            assert_exact(name,content)
            for bad in (content+b'\n',content[:-1],b'# changed reentry/fallback\n'+content):
                with self.subTest(name=name), self.assertRaises(AssertionError):
                    assert_exact(name,bad)


class SelfXmlExactFreezeTests(unittest.TestCase):
    def test_exact_inverse_and_missing_altered_duplicate_or_moved_hunks(self):
        for name, hunks in SELF_XML_EXACT_HUNKS.items():
            source = (ROOT/name).read_text()
            restored = restore_exact_self_xml_delta(name, source)
            self.assertEqual(hashlib.sha256(restored.encode()).hexdigest(), SELF_XML_BASE_HASHES[name])
            for approved, original in hunks:
                for bad in (source.replace(approved, original, 1),
                            source.replace(approved, approved.replace('verified_current_account', 'wrong_current_account'), 1),
                            source + approved,
                            source.replace(approved, '', 1) + approved):
                    with self.subTest(name=name), self.assertRaises(AssertionError):
                        restore_exact_self_xml_delta(name, bad)

    def test_business_and_orchestrator_mutations_remain_rejected(self):
        for name, surfaces in [('runner.py', ('reentry', 'Follow', 'Mute', 'Return CT', 'checkpoint/resume', 'extra byte')),
                               ('account_session_orchestrator.py', ('Target Availability', 'rotation', 'unapproved propagation'))]:
            source = (ROOT/name).read_text()
            tree = ast.parse(source)
            # Inject a byte inside real functions as well as at file end. Nothing
            # outside the authorized hunks may be swallowed by normalization.
            functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
            for i, surface in enumerate(surfaces):
                node = functions[i % len(functions)]
                lines = source.splitlines(True)
                lines.insert(node.end_lineno, '# unauthorized ' + surface + '\n')
                with self.subTest(name=name, surface=surface), self.assertRaises(AssertionError):
                    restore_exact_self_xml_delta(name, ''.join(lines))
            with self.assertRaises(AssertionError):
                restore_exact_self_xml_delta(name, source + '\n')


class TwoCandidateDurableCertificationTests(unittest.TestCase):
    """Actual fsynced permit and actual canonical SQLite writer, no admission mock."""
    def setUp(self):
        import follow60_ordering_v2_ledger_v1 as lv
        self.lv=lv
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name).resolve()/'receipts.sqlite'
        p=patch.object(lv,'DEFAULT_PATH',self.path);p.start();self.addCleanup(p.stop)
        self.binding=dict(BINDING,max_candidate_admissions=2)
        self.store=ca._PermitStore(self.tmp.name,self.binding);self.addCleanup(self.store.close)

    def cycle(self, name, action, *, complete=True):
        scope=self.lv.LedgerScope('account','run','request','session','target',action,name)
        store=self.lv.DurableOrderingLedgerV1(scope,path=self.path)
        payloads=[('profile_certified',{'proof_hash':'profile-'+name}),('post_opened',{'verified':True}),
            ('like_verified',{'like_action_state':'LIKE_ACTION_PERFORMED_NOW','real_tap_sent':True,'fresh_like_verified':True,'candidate_username':name,'action_id':action,'stable_proof_hash':'proof-'+name,'media_binding':'media-'+name}),
            ('profile_reentry_verified',{'verified':True}),('follow_pending',{}),
            ('follow_verified',{'tap_sent':True,'follow_state_after':'following','candidate_username':name}),
            ('mute_posts_verified',{'verified':True}),('mute_stories_verified',{'verified':True}),
            ('return_ct_exact',{'verified':True}),('cycle_complete',{'critical_persistence_acknowledged':True,'candidate_username':name})]
        for stage,payload in (payloads if complete else payloads[:-1]):store.apply_receipt(stage,payload)
        return store

    def test_two_complete_cycles_third_blocked_zero_transport(self):
        transports=[]
        self.assertEqual(self.store.admit(KEY),'FIRST_ADMISSION_GRANTED');transports.append('alice')
        self.cycle('alice','action-a')
        self.assertEqual(self.store.admit(('run','account','ct','bob')),'SECOND_ADMISSION_GRANTED');transports.append('bob')
        self.cycle('bob','action-b')
        cycles=ca.read_certification_cycles(self.binding,self.store._read()['slots'])
        self.assertEqual([c.scope.action_id for c in cycles],['action-a','action-b'])
        self.assertEqual(self.store.admit(('run','account','ct','bob')),'SAME_CANDIDATE_REENTRY_GRANTED')
        with self.assertRaises(ca.CertificationStop) as e:
            self.store.admit(('run','account','ct','charlie'));transports.append('charlie')
        self.assertEqual(e.exception.reason,'THIRD_CANDIDATE_BLOCKED')
        self.assertEqual(transports,['alice','bob'])

    def test_missing_or_incomplete_first_cycle_blocks_second(self):
        self.store.admit(KEY);self.cycle('alice','action-a',complete=False)
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(('run','account','ct','bob'))
        self.assertEqual(e.exception.reason,'PREVIOUS_CYCLE_UNPROVED')
        self.assertEqual(len(self.store._read()['slots']),1)

    def test_old_receipts_cannot_authorize_second(self):
        self.cycle('alice','action-a');self.store.admit(KEY)
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(('run','account','ct','bob'))
        self.assertEqual(e.exception.reason,'PREVIOUS_CYCLE_UNPROVED')

    def test_prior_candidate_reentry_rejected_after_second_admission(self):
        self.store.admit(KEY);self.cycle('alice','action-a');self.store.admit(('run','account','ct','bob'))
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(KEY)
        self.assertEqual(e.exception.reason,'PRIOR_CANDIDATE_REENTRY_BLOCKED')

    def test_restart_never_refunds_slots(self):
        self.store.admit(KEY);self.store.close()
        with self.assertRaises(ca.CertificationStop) as e:ca._PermitStore(self.tmp.name,self.binding)
        self.assertEqual(e.exception.reason,'PERMIT_RESTART_FORBIDDEN')

    def test_concurrent_session_cannot_open_same_permit(self):
        with self.assertRaises(ca.CertificationStop):ca._PermitStore(self.tmp.name,self.binding)
        self.assertEqual(self.store.admit(KEY),'FIRST_ADMISSION_GRANTED')

    def test_changed_first_payload_rejected_after_second(self):
        import sqlite3
        self.store.admit(KEY);self.cycle('alice','action-a');self.store.admit(('run','account','ct','bob'));self.cycle('bob','action-b')
        with sqlite3.connect(self.path) as conn:conn.execute("UPDATE follow60_ordering_v2_receipts SET payload_json='{}' WHERE action_id='action-a' AND stage='return_ct_exact'")
        with self.assertRaises(ValueError):ca.read_certification_cycles(self.binding,self.store._read()['slots'])

    def test_extra_action_rejected(self):
        self.store.admit(KEY);self.cycle('alice','action-a');self.cycle('other','action-x')
        with self.assertRaises(ca.CertificationStop):self.store.admit(('run','account','ct','bob'))

    def test_fresh_tokens_and_memory_between_two_cycles(self):
        import time
        import followers_surface_proof as proof
        import social_memory
        from types import SimpleNamespace
        proof.reset_for_tests();self.addCleanup(proof.reset_for_tests)
        scope=proof.FollowersSurfaceScope('serial','app','account','target','instagram_followers_list')
        tokens=[]
        for name,action in [('alice','action-a'),('bob','action-b')]:
            self.store.admit(('run','account','ct',name))
            # The unchanged opener obtains current-generation geometry after
            # certification admission; this exercises the real token primitive.
            proof.capture_viewport(scope,'<hierarchy/>',[{'username':name,'bounds':{'left':100,'top':700,'right':500,'bottom':820},'row_center':[300,760],'resource_id':'follow_list_username'}],package_name='com.instagram.android',activity_name='.activity.MainTabActivity',captured_at_monotonic=time.monotonic())
            token,reason=proof.issue_row_action_token(scope,name)
            self.assertIsNotNone(token,reason);tokens.append(token)
            self.assertTrue(proof.consume_row_action_token(token)[0])
            self.assertFalse(proof.consume_row_action_token(token)[0])
            self.cycle(name,action)
            proof.invalidate(scope,reason='candidate_cycle_navigation',navigation_changed=True)
        self.assertNotEqual(tokens[0],tokens[1])
        self.assertFalse(proof.consume_row_action_token(tokens[0])[0])
        denied=social_memory.evaluate_follow_eligibility(target_username='alice',source_profile='ct',db_row={'account_id':'account','username':'alice','posts_liked_count':1},runtime_followed={'alice'},runtime_unfollowed=set(),runtime_interacted={'alice'},runtime_skipped=set(),config=SimpleNamespace(SOCIAL_MEMORY_ENABLED=True))
        self.assertFalse(denied.allowed)
        self.assertEqual(len(ca.read_certification_cycles(self.binding,self.store._read()['slots'])),2)

    def test_third_is_blocked_even_without_second_completion(self):
        self.store.admit(KEY);self.cycle('alice','action-a');self.store.admit(('run','account','ct','bob'))
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(('run','account','ct','charlie'))
        self.assertEqual(e.exception.reason,'THIRD_CANDIDATE_BLOCKED')
        with self.assertRaises(ValueError):ca.read_certification_cycles(self.binding,self.store._read()['slots'])

    def test_terminal_before_first_transport_is_readable(self):
        self.store.terminal(ca.IDENTITY_UNPROVED)
        self.assertEqual(self.store._read()['state'],'TERMINAL')
        with self.assertRaises(ca.CertificationStop):self.store.admit(KEY)



    def test_restart_after_second_slot_is_also_forbidden(self):
        self.store.admit(KEY);self.cycle('alice','action-a');self.store.admit(('run','account','ct','bob'))
        self.store.close()
        with self.assertRaises(ca.CertificationStop) as e:ca._PermitStore(self.tmp.name,self.binding)
        self.assertEqual(e.exception.reason,'PERMIT_RESTART_FORBIDDEN')

    def test_receipt_scope_and_future_time_rejected_without_second_grant(self):
        import sqlite3
        self.store.admit(KEY);self.cycle('alice','action-a')
        with sqlite3.connect(self.path) as conn:
            saved=conn.execute("SELECT created_at_monotonic FROM follow60_ordering_v2_receipts WHERE stage='cycle_complete'").fetchone()[0]
            conn.execute("UPDATE follow60_ordering_v2_receipts SET created_at_monotonic=? WHERE stage='cycle_complete'",(saved+1000,))
        with self.assertRaisesRegex(ValueError,'stale_receipt'):ca.read_certification_cycles(self.binding,self.store._read()['slots'])
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE follow60_ordering_v2_receipts SET created_at_monotonic=? WHERE stage='cycle_complete'",(saved,))
            conn.execute("UPDATE follow60_ordering_v2_receipts SET request_id='different' WHERE stage='cycle_complete'")
        with self.assertRaises(ca.CertificationStop) as e:self.store.admit(('run','account','ct','bob'))
        self.assertEqual(e.exception.reason,'PREVIOUS_CYCLE_UNPROVED')
        self.assertEqual(len(self.store._read()['slots']),1)


def two_candidate_process(directory, binding, queue=None, hold=None):
    try:
        store=ca._PermitStore(directory,binding)
        grant=store.admit(KEY)
        if queue is None:os._exit(19)
        queue.put(grant)
        if hold is not None:hold.wait(10)
        store.close()
    except ca.CertificationStop as exc:
        if queue is not None:queue.put(exc.reason)
        else:os._exit(20)


class TwoCandidateProcessTests(unittest.TestCase):
    def test_crash_restart_and_concurrent_second_executor_fail_closed(self):
        binding=dict(BINDING,max_candidate_admissions=2)
        ctx=multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as temp:
            q=ctx.Queue();hold=ctx.Event()
            a=ctx.Process(target=two_candidate_process,args=(temp,binding,q,hold));a.start()
            try:
                self.assertEqual(q.get(timeout=15),'FIRST_ADMISSION_GRANTED')
                b=ctx.Process(target=two_candidate_process,args=(temp,binding,q));b.start();b.join(15)
                self.assertEqual(b.exitcode,0);self.assertEqual(q.get(timeout=2),'PERMIT_INVALID')
            finally:
                hold.set();a.join(15)
                if a.is_alive():a.kill();a.join()
                q.close()
            self.assertEqual(a.exitcode,0)
            with self.assertRaises(ca.CertificationStop) as e:ca._PermitStore(temp,binding)
            self.assertEqual(e.exception.reason,'PERMIT_RESTART_FORBIDDEN')
        with tempfile.TemporaryDirectory() as temp:
            a=ctx.Process(target=two_candidate_process,args=(temp,binding));a.start();a.join(15)
            if a.is_alive():a.kill();a.join();self.fail('child timeout')
            self.assertEqual(a.exitcode,19)
            before=next(Path(temp).glob('*.json')).read_bytes()
            with self.assertRaises(ca.CertificationStop) as e:ca._PermitStore(temp,binding)
            self.assertEqual(e.exception.reason,'PERMIT_RESTART_FORBIDDEN')
            self.assertEqual(next(Path(temp).glob('*.json')).read_bytes(),before)


class TwoCandidateConfigurationTests(unittest.TestCase):
    def configure_fixture(self, mutate=lambda m,b:None, *, verified=True):
        # Only external signature/read RPC responses are fixtures. Exact
        # configure, canonical row validation and durable store execute.
        from contextlib import ExitStack
        import follow60_deployment_gate_v1 as dg
        import auto_restart_device_lock as locks,account_run_control as requests,supabase_client as db
        now=datetime.now(timezone.utc)
        a=dict(permit_id='fixture',nonce='fresh-fixture',candidate_sha=BASE,account_id='76c1dff8-d16a-40cf-83df-8138d2ca5bd4',device_id='ddec36ad-19d8-4f00-9f7d-a8c2d29fe540',serial='RFGL62N646V',package='com.instagram.android',version_name='446.0.0.49.77',certification_not_before=(now-timedelta(minutes=1)).isoformat(),certification_expires_at=(now+timedelta(minutes=2)).isoformat(),max_candidate_admissions=2)
        sql={k:a[k] for k in ('permit_id','nonce','candidate_sha','device_id','serial','package','version_name','max_candidate_admissions')}
        sql.update(account=a['account_id'],app_instance_id='36495109-0581-408c-baf0-d419c5caf534',version_code=385211304,expires_at=a['certification_expires_at'])
        b={k:a[k] for k in ('account_id','device_id','serial','package','version_name','candidate_sha')}
        b.update(run_id='real-generated-run',request_id='real-generated-request',worker_id='fixture-worker',lease_id='fixture-lease')
        m=dict(certified_candidate_sha=BASE,a0_candidate_admission_permits=[a],physical_canary_permits=[sql]);mutate(m,b)
        lease={k:b[k] for k in ('device_id','worker_id','request_id','account_id','lease_id')};lease['lease_expires_at']=(now+timedelta(minutes=3)).isoformat()
        req=dict(id=b['request_id'],run_id=b['run_id'],account_id=b['account_id'],claimed_by=b['worker_id'],status='running',lease_expires_at=lease['lease_expires_at'],metadata_safe={'device_lock_binding':dict(lease,generation=b['lease_id'])})
        run=dict(id=b['run_id'],account_id=b['account_id'],status='running')
        with tempfile.TemporaryDirectory() as t,ExitStack() as stack:
            home=Path(t).resolve();state=home/'Library/Application Support/BotApp/follow60-lock-v3.1/a0-candidate-admissions';state.mkdir(parents=True)
            mp=home/'manifest.json';mp.write_text(json.dumps(m))
            stack.enter_context(patch.object(Path,'home',return_value=home))
            stack.enter_context(patch.object(dg,'verify_deployment_candidate',return_value={'ok':verified}))
            stack.enter_context(patch.object(locks,'_load_device_lock',return_value=lease))
            stack.enter_context(patch.object(requests,'get_account_run_request',return_value=req))
            stack.enter_context(patch.object(db,'load_run_row',return_value=run))
            ca._REQUESTED=False;ca._SESSION=None
            try:
                ca.configure_from_signed_permit(manifest_path=mp,signature_path=home/'fixture.sig',permit_id='fixture',execution_binding=b,state_directory=state)
                ca.validate_execution_context()
                return ca._SESSION[2].limit
            finally:
                if ca._SESSION is not None:ca._SESSION[2].close()
                ca._REQUESTED=False;ca._SESSION=None

    def test_exact_signed_two_and_legacy_one(self):
        self.assertEqual(self.configure_fixture(),2)
        def one(m,b):
            for k in ('a0_candidate_admission_permits','physical_canary_permits'):m[k][0]['max_candidate_admissions']=1
        self.assertEqual(self.configure_fixture(one),1)

    def test_invalid_authority_and_limits(self):
        with self.assertRaises(ca.CertificationStop):self.configure_fixture(verified=False)
        for value in (True,False,0,3,-1,2.0,'2',None):
            def wrong(m,b):
                for k in ('a0_candidate_admission_permits','physical_canary_permits'):m[k][0]['max_candidate_admissions']=value
            with self.subTest(value=value),self.assertRaises(ca.CertificationStop):self.configure_fixture(wrong)

    def test_wrong_exact_tuple_even_when_execution_agrees(self):
        for key in ('account_id','device_id','serial','package','version_name','candidate_sha'):
            def wrong(m,b):
                m['a0_candidate_admission_permits'][0][key]='wrong';b[key]='wrong'
                m['physical_canary_permits'][0]['account' if key=='account_id' else key]='wrong'
            with self.subTest(key=key),self.assertRaises(ca.CertificationStop):self.configure_fixture(wrong)

    def test_collections_must_agree_and_sql_identity_exact(self):
        for key in ('nonce','candidate_sha','account','serial','package','device_id','version_name','app_instance_id','version_code','max_candidate_admissions','expires_at'):
            def wrong(m,b):m['physical_canary_permits'][0][key]='wrong'
            with self.subTest(key=key),self.assertRaises(ca.CertificationStop):self.configure_fixture(wrong)
        def absent(m,b):m['physical_canary_permits']=[]
        with self.assertRaises(ca.CertificationStop):self.configure_fixture(absent)
        def duplicate(m,b):m['physical_canary_permits']*=2
        with self.assertRaises(ca.CertificationStop):self.configure_fixture(duplicate)
