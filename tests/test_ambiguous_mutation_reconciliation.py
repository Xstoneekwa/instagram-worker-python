from ambiguous_mutation_reconciliation import (
    AMBIGUOUS,
    PERSISTED,
    RECONCILED,
    UNRESOLVED,
    decide_reconciliation,
    deterministic_mutation_action_id,
)


def test_action_id_is_deterministic_and_action_scoped():
    follow = deterministic_mutation_action_id(
        account_id="a", run_id="r", candidate_username="@Arnaud_Blanchard74", action_type="follow"
    )
    assert follow == deterministic_mutation_action_id(
        account_id="a", run_id="r", candidate_username="arnaud_blanchard74", action_type="follow"
    )
    assert follow != deterministic_mutation_action_id(
        account_id="a", run_id="r", candidate_username="arnaud_blanchard74", action_type="unfollow"
    )


def test_receipt_first_short_circuits_without_ui_state():
    out = decide_reconciliation(
        action_type="follow", canonical_receipt_exists=True, exact_identity=False, fresh_states=[]
    )
    assert (out.decision, out.terminal) == (PERSISTED, True)


def test_follow_requires_two_fresh_concordant_states():
    assert decide_reconciliation(
        action_type="follow", canonical_receipt_exists=False, exact_identity=True, fresh_states=["following"]
    ).decision == UNRESOLVED
    out = decide_reconciliation(
        action_type="follow",
        canonical_receipt_exists=False,
        exact_identity=True,
        fresh_states=["following", "following"],
    )
    assert (out.decision, out.terminal) == (RECONCILED, True)


def test_follow_pre_state_allows_only_one_bounded_retry():
    out = decide_reconciliation(
        action_type="follow",
        canonical_receipt_exists=False,
        exact_identity=True,
        fresh_states=["follow", "follow"],
    )
    assert (out.decision, out.safe_to_retry) == (AMBIGUOUS, True)
    assert not decide_reconciliation(
        action_type="follow",
        canonical_receipt_exists=False,
        exact_identity=True,
        fresh_states=["follow", "follow"],
        physical_retry_count=1,
    ).safe_to_retry


def test_unfollow_reconciles_only_exact_not_following_twice():
    out = decide_reconciliation(
        action_type="unfollow",
        canonical_receipt_exists=False,
        exact_identity=True,
        fresh_states=["not_following", "not_following"],
    )
    assert out.decision == RECONCILED
    assert decide_reconciliation(
        action_type="unfollow",
        canonical_receipt_exists=False,
        exact_identity=False,
        fresh_states=["not_following", "not_following"],
    ).decision == UNRESOLVED
