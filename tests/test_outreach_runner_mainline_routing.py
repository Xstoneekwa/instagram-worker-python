"""Run the real pre-device source gates, with real Follow60 configuration.

Only external integrity/persistence I/O is controlled; no replacement routing
function or fake configure result. No runner.main(), UI or network is invoked.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import os
from pathlib import Path
import time
from typing import Any
import unittest
from unittest.mock import Mock, patch

import follow_60s_canary as follow60
import post_follow_stage_outbox as outbox
import account_session_orchestrator as sessions
from tests.test_certification_admission import restore_exact_ui_profile_delta, restore_exact_product_delta
from dm_real_send_flags import resolve_outreach_dm_real_send_enabled, resolve_welcome_dm_real_send_enabled

ROOT = Path(__file__).resolve().parents[1]
ADDITION = '        if supabase_mode and run_request_id and not _follow60_canary_active and not outreach_session_run:\n'
BASELINE_RUNNER_SHA256 = 'b34f60c651ed4e64e8e484bb7228fe7e133ee9542368a6f971a2c94c305216ab'
PRESERVED = {'supabase/functions/outreach-enqueue/index.ts': '4af73cb69f955d3ead51f0bb3c92bf9d041f3a528ac4483c079b09baa365aee1', 'account_session_orchestrator.py': 'e57bde7972243c6e8a3a096bb996be7e9edfec38cc5c3858cf157e03c2514dad', 'follow60_mainline_v2.py': '572a6a9e66c6cf434ef7335675f85e73ab215593eb6e86d99f2677c1c7ba0652', 'unfollow_session_orchestrator.py': '64e032439844e701d644d4c04dc33d7602c0ac28d9274f50a329e8a0c3c9083d', 'auto_restart_dispatcher_tick.py': '2c05c8eecc2cdee74b26eefa4b915a991f016fd3bfdaea77634ee555849819c9', 'supabase/migrations/20260531185200_create_account_package_summary.sql': '3573f4d50654f44fc5bc8e7df8c0d72050580d64b2dd891b13f9e8d5cb02efc9', 'config.py': 'eb48834ebdccd5af5f72f6a48cfa8ce11101d574987e88c4c455a4146b15144a', 'dm_certification_admission.py': '492e2dbf94c0687f45d5c5bb280c598a30a485e87dd3c370e8355dddc41a0da4', 'welcome_session_orchestrator.py': '9c094874aa4ea3dc45d89eb1127740ffb82a3351616a7fa49cb833f89ce18a3b'}


class OutreachRunnerRoutingTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / 'runner.py').read_text()
        tree = ast.parse(self.source)
        self.integrity = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                              and ast.unparse(n.test) == 'supabase_mode and run_request_id and (not _follow60_canary_active)')
        self.activation = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                               and ast.unparse(n.test) == 'supabase_mode and run_request_id and (not _follow60_canary_active) and (not outreach_session_run)')
        self.assertEqual(self.integrity.end_lineno + 1, self.activation.lineno)

    def gate(self, kind, *, integrity_ok=True, mainline_enabled=True, outbox_ok=True, active=False, supabase=True, request='request'):
        old_runtime = follow60._RUNTIME
        self.addCleanup(setattr, follow60, '_RUNTIME', old_runtime)
        events = []
        integrity = Mock(return_value={'ok': integrity_ok, 'reason': 'test_integrity_rejected'})
        configure = Mock(wraps=follow60.configure_mainline)
        persistence = Mock()
        flush = Mock(return_value={'ok': outbox_ok, 'reason': 'test_outbox_rejected'})
        namespace = dict(supabase_mode=supabase, run_request_id=request,
            _follow60_canary_active=active, outreach_session_run=kind == 'outreach_session',
            dispatch_run_type=kind, account_id='account', account_username='account_name',
            run_id='run', _SESSION_SOCIAL_ID='session', _worker_sha='a' * 40,
            _follow60_attempt_id=1, _canary_resume_policy={}, linked=True,
            config=type('Config', (), {'INSTAGRAM_PACKAGE': 'com.instagram.android'}),
            DEFAULT_FOLLOW_ENGINE='FOLLOW60_V2_MAINLINE_V1',
            __file__=str(ROOT / 'runner.py'), Path=Path, time=time, Any=Any,
            verify_runtime_integrity=integrity, _configure_follow60_mainline=configure,
            _ensure_follow_persistence_run_binding=persistence,
            _install_follow60_activation_components=follow60.install_activation_components,
            _follow60_activation_component_status=follow60.activation_component_status,
            acquire_pre_follow_mono_capture=lambda: None, build_pre_follow_tap_context=lambda: None,
            log=lambda level, event, **data: events.append((event, data)))
        function = ast.parse('def execute_gate():\n    return locals()\n').body[0]
        function.body = [copy.deepcopy(self.integrity), copy.deepcopy(self.activation), function.body[0]]
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        with patch.dict(os.environ, {'FOLLOW60_MAINLINE_ENABLED': str(mainline_enabled).lower()}), \
             patch.object(follow60, 'log'), patch.object(outbox, 'flush_pending', flush):
            exec(compile(module, str(ROOT / 'runner.py'), 'exec'), namespace)
            result = namespace['execute_gate']()
        return result, events, integrity, configure, persistence, flush

    def assert_standalone(self):
        result, events, integrity, configure, persistence, flush = self.gate('outreach_session')
        self.assertIsInstance(result, dict)
        integrity.assert_called_once()
        configure.assert_not_called(); persistence.assert_not_called(); flush.assert_not_called()
        self.assertNotIn('_follow60_business_session_binding', result)
        self.assertEqual([e for e, _ in events], ['follow60_integrity_check_completed'])

    def assert_full_cycle(self, plan, addon):
        result, events, integrity, configure, persistence, flush = self.gate('account_session')
        self.assertIsInstance(result, dict)
        self.assertTrue(result['_follow60_engine_active'])
        self.assertEqual(result['_follow60_business_session_binding']['run_id'], 'run')
        self.assertEqual(result['_follow60_business_session_binding']['request_id'], 'request')
        integrity.assert_called_once(); configure.assert_called_once(); persistence.assert_called_once(); flush.assert_called_once()
        self.assertEqual(configure.call_args.kwargs['run_type'], 'account_session')
        self.assertEqual(events[-1][0], 'follow60_mainline_activation_ready')
        # Plan defaults are canonical source, not synthetic permissions injected
        # into the gate. They must not change the account_session run kind.
        defaults = (ROOT/'supabase/migrations/20260531185200_create_account_package_summary.sql').read_text()
        rows = {'growth': "('growth', 'Growth', 80, 80, 80, 80, false, false",
                'pro': "('pro', 'Pro', 120, 120, 120, 120, true, false",
                'premium': "('premium', 'Premium', 120, 120, 120, 120, true, false"}
        self.assertIn(rows[plan], defaults)
        with patch.object(sessions.config, 'ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED', addon):
            self.assertIs(sessions._account_session_outreach_addon_enabled(), addon)

    def test_standalone_outreach_keeps_integrity_without_mainline(self): self.assert_standalone()
    def test_growth_without_addon(self): self.assert_full_cycle('growth', False)
    def test_growth_with_addon(self): self.assert_full_cycle('growth', True)
    def test_pro_native_welcome(self): self.assert_full_cycle('pro', False)
    def test_pro_with_addon(self): self.assert_full_cycle('pro', True)
    def test_premium_native_welcome(self): self.assert_full_cycle('premium', False)
    def test_premium_with_addon(self): self.assert_full_cycle('premium', True)

    def assert_preserved_bytes(self, name, source):
        if name == "supabase/functions/outreach-enqueue/index.ts":
            source = restore_exact_product_delta(name, source.decode()).encode()
        if name == "account_session_orchestrator.py":
            source = restore_exact_ui_profile_delta(name, source.decode()).encode()
        if name == 'dm_certification_admission.py':
            # The certification recipient is claim-bound; no historical fixed
            # recipient literal may remain in the validator.
            self.assertNotIn(b"'recipient_username': 'sunetbassonmua'", source)
        self.assertEqual(hashlib.sha256(source).hexdigest(), PRESERVED[name])

    def assert_preserved(self, name):
        self.assert_preserved_bytes(name, (ROOT/name).read_bytes())

    def test_certification_recipient_preservation_rejects_wrong_or_duplicate_literal(self):
        name = 'dm_certification_admission.py'
        source = (ROOT/name).read_bytes()
        current = b"recipient = claims['recipient_username']"
        self.assertEqual(source.count(current), 1)
        for replacement in (b"recipient = claims['other']",
                            b"recipient = 'sunetbassonmua'",
                            current + b"; recipient = 'other'",
                            current + b" # unrelated"):
            bad = source.replace(current, replacement, 1)
            self.assertNotEqual(bad, source)
            with self.subTest(source=hashlib.sha256(bad).hexdigest()), self.assertRaises(AssertionError):
                self.assert_preserved_bytes(name, bad)

    def test_certification_recipient_preservation_rejects_unrelated_byte_change(self):
        name = 'dm_certification_admission.py'
        source = (ROOT/name).read_bytes()
        with self.assertRaises(AssertionError):
            self.assert_preserved_bytes(name, source + b'\n')

    def test_entitlement_rejection_preserved(self):
        self.assert_preserved('supabase/functions/outreach-enqueue/index.ts')
        self.assertIn('account_outreach_entitlement_required', (ROOT/'supabase/functions/outreach-enqueue/index.ts').read_text())

    def test_entitlement_authority_preserved(self):
        self.assert_preserved('supabase/functions/outreach-enqueue/index.ts')
        self.assertIn('client_account_has_outreach_entitlement', (ROOT/'supabase/functions/outreach-enqueue/index.ts').read_text())

    def test_growth_has_no_native_welcome_default(self): self.assert_full_cycle('growth', False)
    def test_pro_premium_welcome_independent_of_outreach(self):
        env={'WELCOME_DM_REAL_SEND_ENABLED':'true','OUTREACH_DM_REAL_SEND_ENABLED':'false'}
        self.assertTrue(resolve_welcome_dm_real_send_enabled(environ=env,emit_log=False)[0])
        self.assertFalse(resolve_outreach_dm_real_send_enabled(environ=env,emit_log=False)[0])
        self.assert_preserved('account_session_orchestrator.py')

    def test_follow_unchanged(self): self.assert_preserved('follow60_mainline_v2.py')
    def test_unfollow_unchanged(self): self.assert_preserved('unfollow_session_orchestrator.py')
    def test_scheduler_unchanged(self): self.assert_preserved('auto_restart_dispatcher_tick.py')
    def test_onboarding_defaults_unchanged(self): self.assert_preserved('supabase/migrations/20260531185200_create_account_package_summary.sql')
    def test_caps_and_flags_unchanged(self):
        self.assert_preserved('config.py')
        source = (ROOT / 'config.py').read_text()
        old = source.replace('min(30, max(0, _env_int("OUTREACH_HARD_MAX_PER_SESSION", 30)))',
                             'max(0, _env_int("OUTREACH_HARD_MAX_PER_SESSION", 5))').replace(
                             'min(30, max(0, _env_int("OUTREACH_HARD_MAX_PER_DAY", 30)))',
                             'max(0, _env_int("OUTREACH_HARD_MAX_PER_DAY", 40))')
        self.assertEqual(hashlib.sha256(old.encode()).hexdigest(),
                         '23514e1d6ab0217566f100e501ebb41f58ac15968b1949f4f7bd09a9da0a665f')
    def test_standalone_not_converted_to_full_cycle(self):
        self.assert_standalone()
        self.assert_preserved('dm_certification_admission.py')
        self.assertIn('if outreach_session_run:', self.source)
    def test_full_cycle_not_converted_to_standalone(self): self.assert_full_cycle('premium', True)
    def test_addon_remains_after_unfollow(self):
        self.assert_preserved('account_session_orchestrator.py')
        source=(ROOT/'account_session_orchestrator.py').read_text()
        boundary=source.index('boundary="after_unfollow_terminal_before_outreach"')
        self.assertGreater(source.index('_run_account_session_outreach_addon(', boundary), boundary)
    def test_welcome_remains_native(self):
        self.assert_preserved('account_session_orchestrator.py')
        self.assert_preserved('welcome_session_orchestrator.py')

    def test_integrity_rejection_stops_both_modes(self):
        for kind in ('outreach_session','account_session'):
            with self.subTest(kind=kind):
                result, events, _, configure, persistence, flush = self.gate(kind, integrity_ok=False)
                self.assertEqual(result,96)
                self.assertEqual(events[-1][0],'FOLLOW60_MAINLINE_INTEGRITY_MISMATCH')
                configure.assert_not_called();persistence.assert_not_called();flush.assert_not_called()

    def test_mainline_disabled_still_stops_full_cycle(self):
        result, events, _, configure, persistence, flush=self.gate('account_session',mainline_enabled=False)
        self.assertEqual(result,96);configure.assert_called_once();persistence.assert_not_called();flush.assert_not_called()
        self.assertEqual(events[-1][0],'follow60_mainline_configuration_failed_pre_device')

    def test_outbox_failure_still_stops_full_cycle(self):
        result,events,_,_,_,flush=self.gate('account_session',outbox_ok=False)
        self.assertEqual(result,96);flush.assert_called_once()
        self.assertEqual(events[-1][0],'follow60_mainline_outbox_replay_blocked_before_ui')

    def test_other_run_modes_are_not_changed(self):
        for kind in ('unfollow_session','dm_welcome_session_send','unfollow_outreach_pipeline'):
            with self.subTest(kind=kind):
                result,events,integrity,configure,_,_=self.gate(kind)
                self.assertEqual(result,96);integrity.assert_called_once();configure.assert_called_once()
                self.assertEqual(events[-1][0],'follow60_mainline_configuration_failed_pre_device')

    def test_existing_canary_and_local_predicates_preserved(self):
        for args in ({'active':True},{'supabase':False},{'request':''}):
            result,_,integrity,configure,_,_=self.gate('account_session',**args)
            self.assertIsInstance(result,dict);integrity.assert_not_called();configure.assert_not_called()

    def test_exact_single_conditional_is_entire_runner_delta(self):
        self.assertEqual(self.source.count(ADDITION),1)
        restored = restore_exact_ui_profile_delta('runner.py', restore_exact_product_delta('runner.py', self.source))
        original=restored.replace(ADDITION,'',1)
        self.assertEqual(hashlib.sha256(original.encode()).hexdigest(),BASELINE_RUNNER_SHA256)
