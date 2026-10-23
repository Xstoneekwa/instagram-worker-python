"""Run the candidate unit/regression selection with network and processes denied.
Run from Worker candidate root: python3 -B tests/run_outreach_slot_candidate.py
"""
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
# RealDatabaseTests for restriction resume require their separate PostgreSQL/PostgREST harness; deferred to FULL_DB_INTEGRATION_GATE.
modules=['test_outreach_campaign_scope','test_outreach_slot_v25','test_outreach_slot_v24','test_outreach_slot_v23','test_outreach_slot_v22','test_outreach_slot_v21','test_outreach_slot_v2','test_outreach_slot_contract','test_assignment_dispatch_resolver',
 'test_session_transition_buffer','test_business_deadline_2d','test_account_session_terminal_contract',
 'test_account_session_unfollow_skip','test_account_session_resume_state_contract',
 'test_ct_resume_prefix_scroll_follow_contract_v1','test_welcome_list_sender_restore',
 'test_cooperative_business_stop','test_account_run_request_lease_renewal_v1',
 'test_account_identity_guard_incidents','test_account_identity_guard_recovery',
 'test_instagram_action_restriction','test_instagram_account_restriction_classification',
 'test_mute_engine_v2_budget','test_like_verification_bounded_v1',
 'test_follow60_point_c_mute_like_ordering','test_follow_unfollow_daily_plan_handoff',
 'test_follow_partial_unfollow_handoff_point_d','test_human_confirmed_resume_claim']
with patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_FORBIDDEN_IN_TESTS')), \
     patch.object(subprocess,'Popen',side_effect=AssertionError('DEVICE_PROCESS_FORBIDDEN_IN_TESTS')):
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromNames(modules))
    sys.exit(not result.wasSuccessful())
