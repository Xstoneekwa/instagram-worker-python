from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import instagram_navigation as nav
import post_follow_stage_outbox as outbox
from tests import test_post_follow_like_samsung_fast as fixtures


class LikeTerminalContractTests(unittest.TestCase):
    def phase(self, *, config=None, overrides=None, budget_expired=False,
              blacklist=False, commercial_block=False, profile_proved=True):
        device = mock.MagicMock()
        clock = [100.0]
        binding = fixtures._stage_binding(action_id='action', run_id='run')
        kwargs = dict(pkg='com.instagram.android', source_profile_username='ct',
                      follower_username='cand', visual_candidate_id='action',
                      follow_success_verified=True, follow_state_after='following',
                      skipped_tap=False, expected_stage_binding=binding,
                      account_id=binding['account_id'], run_id='run')
        kwargs.update(overrides or {})
        def read_profile(_device):
            if budget_expired:
                clock[0] += 17.0
            return 'cand' if profile_proved else 'another_profile'
        with ExitStack() as stack:
            fixtures._patch_like_phase_common(stack, contract_ctx=fixtures._like_phase_contract_ctx())
            stack.enter_context(mock.patch.object(nav.time, 'perf_counter', side_effect=lambda: clock[0]))
            stack.enter_context(mock.patch.object(nav.config, 'POST_FOLLOW_POST_LIKES_BUDGET_S', 16.0))
            for name, value in (config or {}).items():
                stack.enter_context(mock.patch.object(nav.config, name, value, create=True))
            stack.enter_context(mock.patch.object(nav.account_protection_lists, 'is_interaction_blocked', return_value=blacklist))
            stack.enter_context(mock.patch('account_commercial_policy.commercial_policy_boundary_blocks_phase', return_value=commercial_block))
            stack.enter_context(mock.patch.object(nav, '_validate_post_mute_sheet_closed_proof', return_value=(False, {}, 0, 'absent')))
            stack.enter_context(mock.patch.object(nav, 'read_current_profile_username_for_follow_gate', side_effect=read_profile))
            stack.enter_context(mock.patch('follow_60s_canary.enabled', return_value=False))
            stack.enter_context(mock.patch.object(nav.random, 'randint', return_value=100))
            if not profile_proved:
                stack.enter_context(mock.patch('navigation_engine.observe_instagram_state', return_value={'state': 'UNKNOWN', 'confidence': 0.0}))
            like = stack.enter_context(mock.patch.object(nav, 'visual_like_open_post', side_effect=AssertionError('unexpected Like')))
            post = stack.enter_context(mock.patch.object(nav, 'visual_open_recent_post_from_profile', side_effect=AssertionError('unexpected post open')))
            persist = stack.enter_context(mock.patch('supabase_client.record_post_like_interaction_success'))
            stack.enter_context(mock.patch.object(nav, 'log'))
            result = nav.run_post_follow_post_likes_phase(device, **kwargs)
            like.assert_not_called()
            post.assert_not_called()
            persist.assert_not_called()
        self.assertEqual(result['liked_count'], 0)
        self.assertEqual(result['attempted_count'], 0)
        self.assertIs(result['real_tap_sent'], False)
        self.assertIs(result['fresh_like_verified'], False)
        self.assertIs(result['like_phase_terminal'], True)
        self.assertTrue(result['like_phase_terminal_reason'])
        return result

    def assert_safe_skip(self, result, reason):
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('safe_skip', reason))
        self.assertTrue(result['ok'])

    def test_exact_budget_expires_during_profile_guard_before_first_attempt(self):
        result = self.phase(budget_expired=True)
        self.assertIs(result['like_budget_exhausted_before_attempt'], True)
        self.assert_safe_skip(result, 'no_verified_likes')

    def test_policy_disabled_is_terminal_without_changing_policy(self):
        self.assert_safe_skip(self.phase(config={'POST_FOLLOW_POST_LIKES_ENABLED': False}), 'likes_disabled_by_config')

    def test_real_like_disabled_is_terminal_without_any_tap(self):
        self.assert_safe_skip(self.phase(config={'ENABLE_REAL_VISUAL_POST_LIKE': False}), 'real_post_like_disabled_by_config')

    def test_percentage_skip_keeps_same_draw(self):
        self.assert_safe_skip(self.phase(config={'POST_FOLLOW_POST_LIKES_PERCENTAGE': 50}), 'likes_percentage_roll_skip')

    def test_like_quota_reached_has_no_mutation_or_counter_increment(self):
        self.assert_safe_skip(self.phase(overrides={'session_likes_used': 150}), 'likes_skipped_session_quota')

    def test_blacklist_is_not_removed_and_skips_like_only(self):
        self.assert_safe_skip(self.phase(blacklist=True), 'interaction_blacklist')

    def test_commercial_revision_change_remains_blocking(self):
        self.assertEqual(nav._post_follow_like_terminal_binding(self.phase(commercial_block=True), {}), ('', ''))

    def test_no_follow_does_not_become_cycle_admissible(self):
        result = self.phase(overrides={'follow_success_verified': False})
        self.assertEqual(result['skipped_reason'], 'follow_not_verified')
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('', ''))

    def test_missing_binding_fails_before_policy_skip(self):
        result = self.phase(overrides={'expected_stage_binding': {}}, config={'POST_FOLLOW_POST_LIKES_ENABLED': False})
        self.assertEqual(result['skipped_reason'], 'liketapcontext_stage_binding_missing')
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('', ''))

    def test_mismatched_candidate_fails_closed(self):
        binding = fixtures._stage_binding(action_id='action', run_id='run')
        binding['candidate_username'] = 'wrong'
        result = self.phase(overrides={'expected_stage_binding': binding})
        self.assertEqual(result['skipped_reason'], 'liketapcontext_candidate_binding_mismatch')
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('', ''))

    def test_identity_unproved_stays_blocking_and_has_terminal_reason(self):
        result = self.phase(profile_proved=False)
        self.assertEqual(result['skipped_reason'], 'profile_not_confirmed_before_likes')
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('', ''))

    def test_unknown_no_verified_likes_cannot_be_blanket_promoted(self):
        result = nav._post_follow_post_likes_out_template()
        result.update(skipped_reason='no_verified_likes', attempted_count=1)
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('', ''))

    def test_existing_no_post_and_already_done_safe_skips_unchanged(self):
        for reason in ('post_like_skipped_no_posts_yet', 'likes_skipped_already_liked', 'post_like_skipped_no_post_grid'):
            with self.subTest(reason=reason):
                result = nav._post_follow_post_likes_out_template()
                result.update(ok=True, skipped_reason=reason)
                self.assert_safe_skip(result, reason)

    def test_identity_recovery_without_tap_keeps_existing_safe_terminal(self):
        result = nav._post_follow_post_likes_out_template()
        result.update(phase_outcome='failed_safe_continue', post_follow_likes_recoverable_failure_count=1,
                      skipped_reason='likes_failed_open_post_safe_continue')
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('safe_skip', 'likes_failed_open_post_safe_continue'))

    def test_attempted_like_cannot_be_safe_skip_even_if_ok_is_wrong(self):
        for outcome in ('skipped', 'failed_safe_continue'):
            with self.subTest(outcome=outcome):
                result = nav._post_follow_post_likes_out_template()
                result.update(ok=True, phase_outcome=outcome, real_tap_sent=True,
                              post_follow_likes_recoverable_failure_count=1)
                self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('', ''))

    def test_ambiguous_like_cannot_be_hidden_by_prior_verified_like(self):
        result = dict(liked_count=1, like_action_state='LIKE_ACTION_PERFORMED_NOW',
                      real_tap_sent=True, fresh_like_verified=True, like_mutation_ambiguous=True)
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {'like_verified': True}), ('', ''))

    def test_real_tap_followed_by_failed_verification_keeps_ambiguity(self):
        device = mock.MagicMock()
        with ExitStack() as stack:
            fixtures._patch_like_phase_common(stack, contract_ctx=fixtures._like_phase_contract_ctx())
            stack.enter_context(mock.patch.object(nav.account_protection_lists, 'is_interaction_blocked', return_value=False))
            stack.enter_context(mock.patch.object(nav, '_post_follow_likes_open_top_left_legacy_visual_safe', return_value={
                'ok': True, 'post_detected': True, 'failure_reason': '',
                'open_strategy': 'vision_open_top_left_legacy_safe', 'tap_x': 180, 'tap_y': 1282,
                'detect_reason': 'like_unlike_ui', 'viewer_detect_path': 'phase_a2_exact_like_desc_fast',
                'likes_perf_post_open': {},
            }))
            stack.enter_context(mock.patch.object(nav, 'visual_post_already_liked', return_value={
                'already_liked': False, 'detection_method': 'post_follow_open_like_proof_reuse', 'confidence': 0.91,
            }))
            like = stack.enter_context(mock.patch.object(nav, 'visual_like_open_post', return_value={
                'ok': True, 'real_tap_sent': True, 'tap_x': 79, 'tap_y': 1891, 'confidence': 0.8,
                'likes_perf_like': {},
            }))
            verify = stack.enter_context(mock.patch.object(nav, 'visual_verify_post_liked', return_value={
                'liked_verified': False, 'verification_method': 'no_fresh_like_proof', 'verify_attempts_count': 1,
            }))
            stack.enter_context(mock.patch.object(nav, 'visual_return_to_profile_from_post', return_value={'ok': True}))
            persist = stack.enter_context(mock.patch('supabase_client.record_post_like_interaction_success'))
            stack.enter_context(mock.patch.object(nav, 'log'))
            result = nav.run_post_follow_post_likes_phase(
                device, pkg='com.instagram.android', source_profile_username='ct',
                follower_username='cand', visual_candidate_id='action', follow_success_verified=True,
                follow_state_after='following', skipped_tap=False,
            )
        like.assert_called_once()
        verify.assert_called_once()
        persist.assert_not_called()
        self.assertEqual(result['liked_count'], 0)
        self.assertEqual(result['attempted_count'], 1)
        self.assertIs(result['real_tap_sent'], True)
        self.assertIs(result['fresh_like_verified'], False)
        self.assertIs(result['like_mutation_ambiguous'], True)
        self.assertIs(result['like_phase_terminal'], True)
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('', ''))

    def test_success_still_requires_real_fresh_like_and_exact_stage_ack(self):
        result = dict(liked_count=1, like_action_state='LIKE_ACTION_PERFORMED_NOW',
                      real_tap_sent=True, fresh_like_verified=True)
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {'like_verified': True}), ('verified', 'like_verified'))
        for field in ('real_tap_sent', 'fresh_like_verified'):
            self.assertEqual(nav._post_follow_like_terminal_binding({**result, field: False}, {'like_verified': True}), ('', ''))
        self.assertEqual(nav._post_follow_like_terminal_binding(result, {}), ('', ''))

    def ledger(self, result, *, partial_mute=False, return_ct=True, wrong_root=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'outbox.sqlite3'
            binding = dict(account_id='account', original_run_id='run', request_id='request', action_id='action',
                           candidate_username='cand', source_profile='ct', attempt_id=1, business_session_id='root')
            active = dict(binding_kind='mainline', account_id='account', run_id='run', request_id='request',
                          control_id='run', worker_sha='a' * 40)
            status, reason = nav._post_follow_like_terminal_binding(result, {})
            stages = ['mute_posts_verified'] + ([] if partial_mute else ['mute_stories_verified'])
            if return_ct:
                stages.append('return_ct_exact')
            for stage in stages:
                data = dict(binding_kind='mainline', control_id='run', worker_sha='a' * 40)
                if stage == 'return_ct_exact':
                    data.update(like_terminal_status=status, like_terminal_reason=reason)
                current_binding = dict(binding)
                if wrong_root and stage == 'return_ct_exact':
                    current_binding['business_session_id'] = 'wrong-root'
                outbox.journal_stage(**current_binding, stage=stage, payload=data,
                                     verified_at='2026-09-03T04:34:03+00:00', path=path)
            with mock.patch('supabase_client.persist_follow60_post_follow_v3', return_value={
                'ok': True, 'binding_valid': True, 'inserted_stages': stages, 'duplicate_stages': [],
            }) as persist, mock.patch('supabase_client.ack_follow60_completed_cycle_v2', return_value={
                'ok': True, 'new_cycle_count': 1, 'next_candidate_permitted': True,
            }) as ledger:
                flushed = outbox.flush_pending(active_binding=active, path=path)
            return flushed, persist, ledger

    def test_budget_skip_plus_two_mutes_and_exact_return_completes_mainline_cycle(self):
        result = self.phase(budget_expired=True)
        flushed, persist, ledger = self.ledger(result)
        self.assertTrue(flushed['ok'])
        self.assertEqual(flushed['pending'], 0)
        ledger.assert_called_once()
        self.assertEqual(ledger.call_args.kwargs['like_terminal_status'], 'safe_skip')
        self.assertEqual(ledger.call_args.kwargs['business_session_id'], 'root')
        self.assertNotIn('like_verified', [x['stage'] for x in persist.call_args.kwargs['stages']])

    def test_partial_mute_still_cannot_complete_cycle(self):
        flushed, _, ledger = self.ledger(self.phase(budget_expired=True), partial_mute=True)
        self.assertFalse(flushed['ok'])
        self.assertEqual(flushed['reason'], 'follow60_candidate_local_post_follow_recovery_required')
        ledger.assert_not_called()

    def test_every_valid_policy_skip_can_close_only_with_exact_other_stages(self):
        cases = (
            {'config': {'POST_FOLLOW_POST_LIKES_ENABLED': False}},
            {'config': {'ENABLE_REAL_VISUAL_POST_LIKE': False}},
            {'config': {'POST_FOLLOW_POST_LIKES_PERCENTAGE': 50}},
            {'overrides': {'session_likes_used': 150}},
            {'blacklist': True},
        )
        for case in cases:
            with self.subTest(case=case):
                skipped = self.phase(**case)
                flushed, persist, ledger = self.ledger(skipped)
                self.assertTrue(flushed['ok'])
                ledger.assert_called_once()
                self.assertEqual(ledger.call_args.kwargs['like_terminal_status'], 'safe_skip')
                self.assertNotIn('like_verified', [x['stage'] for x in persist.call_args.kwargs['stages']])

    def test_missing_exact_return_never_emits_cycle_ack(self):
        _, _, ledger = self.ledger(self.phase(budget_expired=True), return_ct=False)
        ledger.assert_not_called()

    def test_mixed_business_root_is_rejected_before_rpc(self):
        flushed, persist, ledger = self.ledger(self.phase(budget_expired=True), wrong_root=True)
        self.assertFalse(flushed['ok'])
        persist.assert_not_called()
        ledger.assert_not_called()

    def test_ambiguous_like_blocks_ledger_after_safe_return(self):
        result = nav._post_follow_post_likes_out_template()
        result.update(real_tap_sent=True, like_mutation_ambiguous=True, phase_outcome='failed_safe_continue',
                      post_follow_likes_recoverable_failure_count=1)
        flushed, _, ledger = self.ledger(result)
        self.assertFalse(flushed['ok'])
        ledger.assert_not_called()


if __name__ == '__main__':
    unittest.main()
