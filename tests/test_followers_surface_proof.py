from __future__ import annotations

import unittest

import followers_surface_proof as proof


def _scope(
    *,
    device: str = "device-a",
    app: str = "app-a",
    account: str = "account-a",
    target: str = "target-a",
) -> proof.FollowersSurfaceScope:
    return proof.FollowersSurfaceScope(
        device_serial=device,
        app_instance_id=app,
        account_id=account,
        target_id=target,
        surface_id="instagram_followers_list",
    )


class FollowersSurfaceProofTests(unittest.TestCase):
    def setUp(self) -> None:
        proof.reset_for_tests()

    def test_same_surface_read_accepts_fresh_exact_scope_and_generation(self) -> None:
        scope = _scope()
        captured = proof.capture(scope, "<hierarchy/>", captured_at_monotonic=10.0)
        observed, reason = proof.get(
            scope,
            mode="same_surface_read",
            now_monotonic=10.5,
        )
        self.assertEqual(captured, observed)
        self.assertEqual("valid_same_surface_proof", reason)

    def test_certify_after_navigation_never_accepts_cache(self) -> None:
        scope = _scope()
        proof.capture(scope, "<hierarchy/>", captured_at_monotonic=10.0)
        observed, reason = proof.get(
            scope,
            mode="certify_after_navigation",
            now_monotonic=10.1,
        )
        self.assertIsNone(observed)
        self.assertEqual("cache_forbidden_after_navigation", reason)

    def test_navigation_generation_invalidates_old_proof(self) -> None:
        scope = _scope()
        captured = proof.capture(scope, "<old/>", captured_at_monotonic=10.0)
        proof.invalidate(scope, reason="back", navigation_changed=True)
        ok, reason = proof.validate(
            captured,
            scope,
            mode="same_surface_read",
            now_monotonic=10.1,
        )
        self.assertFalse(ok)
        self.assertEqual("generation_mismatch", reason)

    def test_scroll_generation_invalidates_old_viewport(self) -> None:
        scope = _scope()
        captured = proof.capture(scope, "<old/>", captured_at_monotonic=10.0)
        proof.invalidate(scope, reason="scroll", scroll_changed=True)
        ok, reason = proof.validate(
            captured,
            scope,
            mode="same_surface_read",
            now_monotonic=10.1,
        )
        self.assertFalse(ok)
        self.assertEqual("generation_mismatch", reason)

    def test_account_target_device_and_app_instance_are_isolated(self) -> None:
        base = _scope()
        proof.capture(base, "<base/>", captured_at_monotonic=10.0)
        variants = [
            _scope(device="device-b"),
            _scope(app="app-b"),
            _scope(account="account-b"),
            _scope(target="target-b"),
        ]
        for variant in variants:
            observed, reason = proof.get(
                variant,
                mode="same_surface_read",
                now_monotonic=10.1,
            )
            self.assertIsNone(observed)
            self.assertEqual("proof_missing", reason)

    def test_stale_proof_is_rejected(self) -> None:
        scope = _scope()
        proof.capture(scope, "<old/>", captured_at_monotonic=10.0)
        observed, reason = proof.get(
            scope,
            mode="same_surface_read",
            max_age_ms=1250.0,
            now_monotonic=11.251,
        )
        self.assertIsNone(observed)
        self.assertEqual("proof_stale", reason)

    def test_clear_current_does_not_clear_another_scoped_proof(self) -> None:
        first = _scope(target="target-a")
        second = _scope(target="target-b")
        proof.capture(first, "<first/>", captured_at_monotonic=10.0)
        proof.capture(second, "<second/>", captured_at_monotonic=10.0)

        proof.clear_current()

        first_observed, first_reason = proof.get(
            first,
            mode="same_surface_read",
            now_monotonic=10.1,
        )
        second_observed, second_reason = proof.get(
            second,
            mode="same_surface_read",
            now_monotonic=10.1,
        )
        self.assertIsNotNone(first_observed)
        self.assertEqual("valid_same_surface_proof", first_reason)
        self.assertIsNone(second_observed)
        self.assertEqual("proof_missing", second_reason)

    def test_default_freshness_covers_bounded_same_surface_processing(self) -> None:
        scope = _scope()
        proof.capture(scope, "<fresh/>", captured_at_monotonic=10.0)

        observed, reason = proof.get(
            scope,
            mode="same_surface_read",
            now_monotonic=24.9,
        )

        self.assertIsNotNone(observed)
        self.assertEqual("valid_same_surface_proof", reason)

    def _capture_viewport(self, scope: proof.FollowersSurfaceScope, *, now: float = 10.0):
        return proof.capture_viewport(
            scope,
            "<hierarchy generation='1'/>",
            [
                {
                    "username": "faelhpp09",
                    "bounds": {"left": 100, "top": 700, "right": 500, "bottom": 820},
                    "row_center": [300, 760],
                    "resource_id": "follow_list_username",
                }
            ],
            package_name="com.instagram.android",
            activity_name="com.instagram.mainactivity.InstagramMainActivity",
            captured_at_monotonic=now,
        )

    def test_exact_row_token_is_single_use(self) -> None:
        scope = _scope()
        viewport = self._capture_viewport(scope)
        token, reason = proof.issue_row_action_token(
            scope, "@FAELHPP09", now_monotonic=10.1
        )
        self.assertIsNotNone(viewport)
        self.assertEqual("row_action_token_issued", reason)
        self.assertEqual((300, 760), token.row_center if token else None)
        self.assertEqual((True, "row_action_token_consumed"), proof.consume_row_action_token(token))
        self.assertEqual(
            (False, "row_action_token_missing_or_consumed"),
            proof.consume_row_action_token(token),
        )

    def test_scroll_invalidation_revokes_row_token_and_geometry(self) -> None:
        scope = _scope()
        self._capture_viewport(scope)
        token, _ = proof.issue_row_action_token(scope, "faelhpp09", now_monotonic=10.1)
        proof.invalidate(scope, reason="physical_scroll", scroll_changed=True)
        self.assertEqual(
            (False, "row_action_token_missing_or_consumed"),
            proof.consume_row_action_token(token),
        )
        new_token, reason = proof.issue_row_action_token(
            scope, "faelhpp09", now_monotonic=10.2
        )
        self.assertIsNone(new_token)
        self.assertEqual("viewport_proof_missing", reason)

    def test_navigation_invalidation_revokes_row_token(self) -> None:
        scope = _scope()
        self._capture_viewport(scope)
        token, _ = proof.issue_row_action_token(scope, "faelhpp09", now_monotonic=10.1)
        proof.invalidate(scope, reason="back", navigation_changed=True)
        self.assertFalse(proof.consume_row_action_token(token)[0])

    def test_missing_wrong_or_ambiguous_expected_identity_fails_closed(self) -> None:
        scope = _scope()
        proof.capture_viewport(
            scope,
            "<hierarchy/>",
            [
                {"username": "one", "bounds": {"left": 1, "top": 1, "right": 2, "bottom": 2}},
                {"username": "one", "bounds": {"left": 3, "top": 3, "right": 4, "bottom": 4}},
            ],
            captured_at_monotonic=10.0,
        )
        missing, missing_reason = proof.issue_row_action_token(
            scope, "two", now_monotonic=10.1
        )
        ambiguous, ambiguous_reason = proof.issue_row_action_token(
            scope, "one", now_monotonic=10.1
        )
        self.assertIsNone(missing)
        self.assertEqual("expected_row_missing", missing_reason)
        self.assertIsNone(ambiguous)
        self.assertEqual("expected_row_ambiguous", ambiguous_reason)

    def test_stale_viewport_cannot_issue_action_token(self) -> None:
        scope = _scope()
        self._capture_viewport(scope, now=10.0)
        token, reason = proof.issue_row_action_token(
            scope, "faelhpp09", max_age_ms=1250.0, now_monotonic=11.251
        )
        self.assertIsNone(token)
        self.assertEqual("viewport_proof_stale", reason)


if __name__ == "__main__":
    unittest.main()
