from __future__ import annotations

import inspect
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest import mock

import pytest

import account_session_orchestrator
import follow_persistence_intent
import runner
import social_memory


def _intent(*, action: str, run: str, session: str, attempt: str) -> None:
    follow_persistence_intent.create_mutation_intent(
        action_id=action,
        action_type="follow",
        account_id="account",
        run_id=run,
        request_id=f"request-{run}",
        business_session_id=session,
        attempt_id=attempt,
        intent_generation=attempt,
        candidate_username=f"candidate-{action}",
    )


def test_p0c_loader_reads_only_explicit_current_lineage_and_generation() -> None:
    with tempfile.TemporaryDirectory() as root, mock.patch.dict(
        "os.environ", {"FOLLOW_PERSISTENCE_INTENT_ROOT": root}
    ):
        _intent(action="current", run="run-2", session="session", attempt="2")
        _intent(action="lineage", run="run-1", session="session", attempt="2")
        _intent(action="wrong-generation", run="run-0", session="session", attempt="1")
        _intent(action="historical-noise", run="run-noise", session="session", attempt="2")

        with mock.patch.object(
            follow_persistence_intent,
            "load_all_nonterminal_intents",
            side_effect=AssertionError("global scan forbidden"),
        ):
            loaded = follow_persistence_intent.load_scoped_nonterminal_intents(
                account_id="account",
                current_run_id="run-2",
                business_session_id="session",
                attempt_id="2",
                intent_generation="2",
                lineage_run_ids=["run-1", "run-0"],
                strict_lineage_only=True,
            )

        assert [item["action_id"] for item in loaded] == ["current", "lineage"]
        assert Path(root, "run-noise").is_dir()


def test_social_batch_authorizes_absence_only_for_exact_complete_envelope() -> None:
    keys = ["alpha", "beta"]
    exact = {
        "schema": "SOCIAL_MEMORY_BATCH_EXACT_V1",
        "account_id": "account",
        "requested_keys": keys,
        "normalization_version": social_memory.SOCIAL_MEMORY_NORMALIZATION_VERSION,
        "query_generation": "run",
        "revision_generation": "revision",
        "rows_by_key": {"alpha": {"username": "alpha"}},
        "complete": True,
        "truncated": False,
    }
    states = social_memory.resolve_exact_batch_state(
        exact,
        account_id="account",
        requested_keys=keys,
        query_generation="run",
        revision_generation="revision",
    )
    assert states["alpha"][0] == social_memory.KNOWN_PROCESSED
    assert states["beta"] == (social_memory.KNOWN_NOT_PROCESSED, None)

    for broken in (
        {**exact, "complete": False},
        {**exact, "truncated": True},
        {**exact, "requested_keys": ["alpha"]},
        {**exact, "account_id": "other"},
        {**exact, "query_generation": "old"},
        {**exact, "revision_generation": "old"},
    ):
        result = social_memory.resolve_exact_batch_state(
            broken,
            account_id="account",
            requested_keys=keys,
            query_generation="run",
            revision_generation="revision",
        )
        assert all(state == social_memory.UNKNOWN for state, _row in result.values())


def test_viewport_batch_unknown_is_not_cached_as_absence() -> None:
    runner._SOCIAL_MEMORY_EXACT_PHASE_CACHE.clear()
    candidates = [{"username": "alpha"}, {"resolved_username_hint": "Beta"}]
    with mock.patch.object(
        runner.supabase_client,
        "fetch_social_memory_batch_exact",
        return_value={"complete": False, "truncated": True},
    ):
        runner._prime_social_memory_exact_viewport(
            account_id="account",
            run_id="run",
            candidates=candidates,
            supabase_mode=True,
            revision_generation="revision",
        )
    assert runner._SOCIAL_MEMORY_EXACT_PHASE_CACHE == {}


def test_snapshot_is_frozen_and_p0c_precedes_resume_load_in_real_call_graph() -> None:
    snapshot = account_session_orchestrator.AccountSessionCPlusSnapshot(
        schema_version="ACCOUNT_SESSION_CPLUS_SNAPSHOT_V1",
        created_at="now",
        account_id="account",
        request_id="request",
        run_id="run",
        attempt_id="1",
        business_session_id="session",
        worker_runtime_root="/release",
        worker_full_sha="a" * 40,
        instagram_package="com.instagram.android",
        commercial_policy_revision="policy",
        settings_revision="settings",
        resolved_follow_cap=50,
        business_action_deadline="deadline",
        deadline_revision="deadline-revision",
        p0c_intent_generation="1",
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.run_id = "other"  # type: ignore[misc]

    source = inspect.getsource(runner._run_followers_list_engine_session)
    assert source.index("_recover_verified_follow_persistence_intents(") < source.index(
        "target_followers_resume_controller.load_and_plan()"
    )
    assert "total_budget_seconds=8.0" in source
    assert "request_timeout=min(1.5" in inspect.getsource(
        runner._recover_verified_follow_persistence_intents
    )
    recovery_source = inspect.getsource(
        runner._recover_verified_follow_persistence_intents
    )
    assert "retry_backoff_cap=0.25" in recovery_source
    assert "ambiguous_canonical_reread=False" in recovery_source
    assert "strict_lineage_only=True" in recovery_source
    assert 'if account_id and not _snapshot_valid:' in source
    assert 'and not _snapshot_valid' in source
    assert 'commercial_policy_revision' in source
    assert 'settings_revision' in source


def test_p0c_result_states_are_bounded_and_receipt_first() -> None:
    common = {
        "account_id": "account",
        "run_id": "run",
        "supabase_mode": True,
        "business_session_id": "session",
        "attempt_id": "2",
        "intent_generation": "2",
        "lineage_run_ids": ["prior"],
    }
    with mock.patch.object(
        runner, "_follow_persistence_intent_enabled_for_account", return_value=True
    ), mock.patch.object(
        runner.follow_persistence_intent,
        "load_scoped_nonterminal_intents",
        return_value=[],
    ):
        assert (
            runner._recover_verified_follow_persistence_intents(None, **common)
            == "RECOVERY_NOT_REQUIRED"
        )

    intent = {
        "action_id": "action",
        "run_id": "run",
        "stage": "follow_physically_verified",
        "candidate_username": "candidate",
        "account_id": "account",
        "business_session_id": "session",
        "attempt_id": "2",
        "intent_generation": "2",
    }
    with mock.patch.object(
        runner, "_follow_persistence_intent_enabled_for_account", return_value=True
    ), mock.patch.object(
        runner.follow_persistence_intent,
        "load_scoped_nonterminal_intents",
        return_value=[intent],
    ):
        assert (
            runner._recover_verified_follow_persistence_intents(
                None, total_budget_seconds=0.0, **common
            )
            == "RECOVERY_PENDING_RETRYABLE"
        )

    receipt = {
        "id": "action",
        "account_id": "account",
        "run_id": "run",
        "username": "candidate",
        "interaction_type": "follow",
    }
    with mock.patch.object(
        runner, "_follow_persistence_intent_enabled_for_account", return_value=True
    ), mock.patch.object(
        runner.follow_persistence_intent,
        "load_scoped_nonterminal_intents",
        return_value=[intent],
    ), mock.patch.object(
        runner.supabase_client, "get_follow_persistence_event", return_value=receipt
    ), mock.patch.object(
        runner.follow_persistence_intent, "update_intent_stage"
    ) as update:
        assert (
            runner._recover_verified_follow_persistence_intents(None, **common)
            == "RECOVERY_RESOLVED"
        )
        assert update.call_args.kwargs["stage"] == "persisted"

    with mock.patch.object(
        runner, "_follow_persistence_intent_enabled_for_account", return_value=True
    ), mock.patch.object(
        runner.follow_persistence_intent,
        "load_scoped_nonterminal_intents",
        return_value=[intent],
    ), mock.patch.object(
        runner.supabase_client,
        "get_follow_persistence_event",
        side_effect=TimeoutError,
    ):
        assert (
            runner._recover_verified_follow_persistence_intents(None, **common)
            == "RECOVERY_INFRA_UNAVAILABLE"
        )


def test_social_memory_unknown_uses_unit_fallback_and_fails_closed() -> None:
    runner._SOCIAL_MEMORY_EXACT_PHASE_CACHE.clear()
    runner._SOCIAL_MEMORY_REVISION_BY_RUN["run"] = "revision"
    incomplete = {"complete": False, "truncated": True}
    with mock.patch.object(
        runner.supabase_client,
        "fetch_social_memory_batch_exact",
        return_value=incomplete,
    ), mock.patch.object(
        runner.supabase_client, "load_interacted_user", return_value=None
    ) as fallback:
        result = runner._social_memory_load_and_evaluate(
            target_username="fresh_candidate_cplus",
            source_profile="source",
            account_id="account",
            run_id="run",
            supabase_mode=True,
        )
        assert result.allowed is True
        fallback.assert_called_once()

    runner._SOCIAL_MEMORY_EXACT_PHASE_CACHE.clear()
    with mock.patch.object(
        runner.supabase_client,
        "fetch_social_memory_batch_exact",
        return_value=incomplete,
    ), mock.patch.object(
        runner.supabase_client, "load_interacted_user", side_effect=TimeoutError
    ):
        result = runner._social_memory_load_and_evaluate(
            target_username="unknown_candidate_cplus",
            source_profile="source",
            account_id="account",
            run_id="run",
            supabase_mode=True,
        )
        assert result.allowed is False
        assert result.reason == "social_memory_unknown"
