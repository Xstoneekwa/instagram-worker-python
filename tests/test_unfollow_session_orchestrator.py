import tempfile
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import follow_persistence_intent
import unfollow_session_orchestrator as subject


def test_reconciliation_precedes_daily_counter_and_plan_in_session_entrypoint():
    source = inspect.getsource(subject.run_unfollow_session)
    assert source.index("_reconcile_unfollow_mutation_intents(") < source.index(
        "count_successful_unfollows_today"
    )
    assert source.index("count_successful_unfollows_today") < source.index(
        "plan_unfollow_targets("
    )


def test_unfollow_rpc_sql_is_action_locked_idempotent_and_service_role_only():
    sql = Path(
        "supabase/migrations/20260818190000_ambiguous_unfollow_persistence_v1.sql"
    ).read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in sql
    assert "where id = p_action_id" in sql
    assert "'status', 'idempotent_replay'" in sql
    assert "'counter_delta', 0" in sql
    assert "'counter_delta', 1" in sql
    assert "grant execute on function public.persist_verified_unfollow_success_v1" in sql
    assert "to service_role" in sql
    assert "from public, anon, authenticated" in sql


def test_verified_unfollow_uses_idempotent_rpc_and_terminalizes_intent():
    intent = {
        "action_id": "action",
        "run_id": "run",
        "physical_attempt_started_at": "2026-08-18T10:00:00+00:00",
        "business_session_id": "session",
        "source_target_id": "target",
        "source_ct_username": "ct",
    }
    result = {
        "ok": True,
        "status": "persisted",
        "invariants_confirmed": True,
        "counter_delta": 1,
    }
    with mock.patch.object(
        subject.supabase_client, "persist_verified_unfollow_success_rpc", return_value=result
    ) as rpc, mock.patch.object(
        subject.follow_persistence_intent, "update_intent_stage"
    ) as terminalize:
        out = subject._persist_unfollow_outcome_for_session(
            "account",
            "arnaud_blanchard74",
            run_id="run",
            settings=SimpleNamespace(mode="unfollow-after-delay"),
            verify_ok=True,
            interaction_row_id="interaction",
            failure_reason="",
            mutation_intent=intent,
            request_id="request",
            business_date_sast="2026-08-18",
        )
    assert out["ok"] is True
    rpc.assert_called_once()
    terminalize.assert_called_once()


def test_ambiguous_unfollow_does_not_write_canonical_projection():
    intent = {"action_id": "action", "run_id": "run"}
    with mock.patch.object(
        subject.supabase_client, "persist_verified_unfollow_success_rpc"
    ) as rpc, mock.patch.object(
        subject.supabase_client, "record_unfollow_interaction_outcome"
    ) as legacy, mock.patch.object(
        subject.follow_persistence_intent, "update_intent_stage"
    ) as update:
        out = subject._persist_unfollow_outcome_for_session(
            "account",
            "candidate",
            run_id="run",
            settings=SimpleNamespace(mode="unfollow-after-delay"),
            verify_ok=False,
            interaction_row_id="interaction",
            failure_reason="timeout",
            mutation_intent=intent,
            request_id="request",
            business_date_sast="2026-08-18",
        )
    assert out["ambiguous"] is True
    rpc.assert_not_called()
    legacy.assert_not_called()
    assert update.call_args.kwargs["stage"] == "ambiguous"


def test_unfollow_auto_restart_receipt_first_uses_prior_run_without_ui():
    with tempfile.TemporaryDirectory() as root, mock.patch.dict(
        "os.environ", {"FOLLOW_PERSISTENCE_INTENT_ROOT": root}
    ):
        prior_run = "prior-run"
        current_run = "current-run"
        session_id = "business-session"
        intent = follow_persistence_intent.create_mutation_intent(
            action_id="action",
            action_type="unfollow",
            account_id="account",
            run_id=prior_run,
            request_id="request",
            business_session_id=session_id,
            candidate_username="Arnaud_Blanchard74",
            interaction_row_id="interaction",
            source_ct_username="bmybusinesses",
        )
        follow_persistence_intent.update_intent_stage(
            run_id=prior_run,
            action_id="action",
            stage="physical_attempt_started",
        )
        receipt = {
            "id": intent["action_id"],
            "account_id": "account",
            "run_id": prior_run,
            "username": "arnaud_blanchard74",
            "interaction_type": "unfollow",
        }
        with mock.patch.object(
            subject.supabase_client, "get_unfollow_persistence_event", return_value=receipt
        ) as lookup, mock.patch.object(
            subject, "open_exact_profile_for_unfollow"
        ) as open_profile:
            assert subject._reconcile_unfollow_mutation_intents(
                object(),
                account_id="account",
                run_id=current_run,
                business_session_id=session_id,
            )
            assert subject._reconcile_unfollow_mutation_intents(
                object(),
                account_id="account",
                run_id=current_run,
                business_session_id=session_id,
            )
        lookup.assert_called_once()
        open_profile.assert_not_called()


def test_unfollow_prepared_recovery_abandons_without_receipt_or_ui():
    intent = {
        "action_id": "prepared-action",
        "action_type": "unfollow",
        "account_id": "account",
        "run_id": "run",
        "candidate_username": "candidate",
        "stage": "prepared",
    }
    with mock.patch.object(
        subject.follow_persistence_intent,
        "load_scoped_nonterminal_intents",
        return_value=[intent],
    ), mock.patch.object(
        subject.supabase_client, "get_unfollow_persistence_event"
    ) as receipt, mock.patch.object(
        subject, "open_exact_profile_for_unfollow"
    ) as open_profile, mock.patch.object(
        subject.follow_persistence_intent, "update_intent_stage"
    ) as update:
        assert subject._reconcile_unfollow_mutation_intents(
            object(), account_id="account", run_id="run", business_session_id="session"
        )
    receipt.assert_not_called()
    open_profile.assert_not_called()
    assert update.call_args.kwargs["stage"] == "abandoned_before_physical_attempt"


def test_unfollow_reconciliation_two_unknown_states_stops_without_retry_or_rpc():
    intent = {
        "action_id": "action",
        "action_type": "unfollow",
        "account_id": "account",
        "run_id": "run",
        "request_id": "request",
        "candidate_username": "candidate",
        "interaction_row_id": "interaction",
        "stage": "ambiguous",
    }
    with mock.patch.object(
        subject.follow_persistence_intent,
        "load_scoped_nonterminal_intents",
        return_value=[intent],
    ), mock.patch.object(
        subject.supabase_client, "get_unfollow_persistence_event", return_value=None
    ), mock.patch.object(
        subject, "open_exact_profile_for_unfollow", return_value={"ok": True}
    ), mock.patch.object(
        subject, "verify_unfollow_target_profile_strict", return_value={"ok": True}
    ), mock.patch.object(
        subject,
        "verify_unfollow_action_success_after_tap",
        return_value={"ok": False},
    ) as verify, mock.patch.object(
        subject.supabase_client, "persist_verified_unfollow_success_rpc"
    ) as rpc, mock.patch.object(
        subject.follow_persistence_intent, "update_intent_stage"
    ) as update:
        assert not subject._reconcile_unfollow_mutation_intents(
            object(), account_id="account", run_id="run", business_session_id="session"
        )
    assert verify.call_count == 2
    rpc.assert_not_called()
    assert update.call_args.kwargs["stage"] == "unresolved"
