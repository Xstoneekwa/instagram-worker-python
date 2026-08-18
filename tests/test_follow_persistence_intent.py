import tempfile
from pathlib import Path
from unittest import mock

import follow_persistence_intent


def test_generic_mutation_intent_is_fsynced_and_loadable():
    with tempfile.TemporaryDirectory() as root, mock.patch.dict(
        "os.environ", {"FOLLOW_PERSISTENCE_INTENT_ROOT": root}
    ):
        intent = follow_persistence_intent.create_mutation_intent(
            action_id="action",
            action_type="unfollow",
            account_id="account",
            run_id="run",
            request_id="request",
            business_session_id="session",
            attempt_id="1",
            candidate_username="@Arnaud_Blanchard74",
            source_target_id="target",
            source_ct_username="ct",
            business_date="2026-08-18",
            worker_sha="sha",
            settings_revision="revision",
            interaction_row_id="interaction",
            mode="unfollow-after-delay",
        )
        assert intent["stage"] == "prepared"
        assert intent["physical_attempt_started_at"] is None
        started = follow_persistence_intent.update_intent_stage(
            run_id="run", action_id="action", stage="physical_attempt_started"
        )
        assert started["physical_attempt_started_at"]
        assert intent["candidate_username"] == "arnaud_blanchard74"
        assert Path(root, "run", "action.json").is_file()
        loaded = follow_persistence_intent.load_nonterminal_intents(
            account_id="account", run_id="run"
        )
        assert loaded == [started]


def test_unresolved_is_current_run_nonterminal_but_terminal_does_not_replay():
    with tempfile.TemporaryDirectory() as root, mock.patch.dict(
        "os.environ", {"FOLLOW_PERSISTENCE_INTENT_ROOT": root}
    ):
        follow_persistence_intent.create_mutation_intent(
            action_id="action",
            action_type="follow",
            account_id="account",
            run_id="run",
            request_id="request",
            candidate_username="candidate",
        )
        follow_persistence_intent.update_intent_stage(
            run_id="run", action_id="action", stage="unresolved"
        )
        assert len(follow_persistence_intent.load_nonterminal_intents(account_id="account", run_id="run")) == 1
        follow_persistence_intent.update_intent_stage(
            run_id="run", action_id="action", stage="terminal"
        )
        assert follow_persistence_intent.load_nonterminal_intents(account_id="account", run_id="run") == []


def test_scoped_loader_carries_only_same_business_session_across_auto_restart():
    with tempfile.TemporaryDirectory() as root, mock.patch.dict(
        "os.environ", {"FOLLOW_PERSISTENCE_INTENT_ROOT": root}
    ):
        for action_id, run_id, session_id in (
            ("same-session-prior-run", "run-1", "session-live"),
            ("different-session", "run-old", "session-old"),
            ("same-session-current-run", "run-2", "session-live"),
        ):
            follow_persistence_intent.create_mutation_intent(
                action_id=action_id,
                action_type="follow",
                account_id="account",
                run_id=run_id,
                request_id=f"request-{run_id}",
                business_session_id=session_id,
                candidate_username=f"candidate-{action_id}",
            )

        loaded = follow_persistence_intent.load_scoped_nonterminal_intents(
            account_id="account",
            current_run_id="run-2",
            business_session_id="session-live",
        )
        assert {item["action_id"] for item in loaded} == {
            "same-session-prior-run",
            "same-session-current-run",
        }


def test_scoped_loader_keeps_legacy_intent_current_run_only():
    with tempfile.TemporaryDirectory() as root, mock.patch.dict(
        "os.environ", {"FOLLOW_PERSISTENCE_INTENT_ROOT": root}
    ):
        for action_id, run_id in (("legacy-prior", "run-1"), ("legacy-current", "run-2")):
            follow_persistence_intent.create_prepared_intent(
                action_id=action_id,
                account_id="account",
                run_id=run_id,
                request_id=f"request-{run_id}",
                candidate_username=f"candidate-{action_id}",
                source_target_id="target",
                source_ct_username="ct",
                settings_revision="revision",
            )

        loaded = follow_persistence_intent.load_scoped_nonterminal_intents(
            account_id="account",
            current_run_id="run-2",
            business_session_id=None,
        )
        assert [item["action_id"] for item in loaded] == ["legacy-current"]
