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


if __name__ == "__main__":
    unittest.main()
