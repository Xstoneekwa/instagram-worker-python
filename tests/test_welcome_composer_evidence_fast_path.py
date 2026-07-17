from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

import dm_sender_engine


PKG = "com.instagram.android"


def hierarchy(*, username: str = "recipient", composer_text: str = "Message", popup: bool = False) -> str:
    popup_node = '<node text="Review account info carefully" />' if popup else ""
    return (
        "<hierarchy>"
        f'<node resource-id="{PKG}:id/header_title" text="{username}" />'
        f'<node resource-id="{PKG}:id/row_thread_composer_edittext" '
        f'text="{composer_text}" bounds="[1,2][3,4]" />'
        f"{popup_node}"
        "</hierarchy>"
    )


def device_for(xml: str, *, current_text: str = "Message") -> tuple[MagicMock, MagicMock]:
    device = MagicMock()
    device.app_current.return_value = {"package": PKG}
    device.dump_hierarchy.return_value = xml
    composer = MagicMock()
    composer.exists.return_value = True
    composer.info = {"bounds": {"left": 1, "top": 2, "right": 3, "bottom": 4}}
    composer.get_text.return_value = current_text
    device.return_value = composer
    return device, composer


class WelcomeComposerEvidenceFastPathTests(unittest.TestCase):
    def make_evidence(self, device: MagicMock, **overrides):
        values = {
            "pkg": PKG,
            "account_id": "account-1",
            "run_id": "run-1",
            "job_id": "job-1",
            "expected_username": "recipient",
            "navigation_generation": "nav-1",
        }
        values.update(overrides)
        return dm_sender_engine._fresh_welcome_composer_evidence(device, **values)

    def resolve(self, device: MagicMock, evidence: dict, **overrides):
        values = {
            "account_id": "account-1",
            "run_id": "run-1",
            "job_id": "job-1",
            "expected_username": "recipient",
            "navigation_generation": "nav-1",
        }
        values.update(overrides)
        return dm_sender_engine._resolve_welcome_composer_from_evidence(
            device, evidence, **values
        )

    def test_exact_fresh_empty_composer_uses_exact_selector(self) -> None:
        device, composer = device_for(hierarchy())
        evidence, reason, observed = self.make_evidence(device)
        self.assertEqual(reason, "ok")
        self.assertEqual(observed, "recipient")
        resolved, resolve_reason, _age_ms, _observed = self.resolve(device, evidence or {})
        self.assertIs(resolved, composer)
        self.assertEqual(resolve_reason, "ok")

    def test_wrong_header_rejects_evidence(self) -> None:
        device, _ = device_for(hierarchy(username="different"))
        evidence, reason, observed = self.make_evidence(device)
        self.assertIsNone(evidence)
        self.assertEqual(reason, "thread_recipient_identity_mismatch")
        self.assertEqual(observed, "different")

    def test_stale_snapshot_falls_back(self) -> None:
        device, _ = device_for(hierarchy())
        evidence, _, _ = self.make_evidence(device)
        assert evidence is not None
        evidence["created_monotonic"] = time.perf_counter() - 2.0
        resolved, reason, _age_ms, _observed = self.resolve(device, evidence)
        self.assertIsNone(resolved)
        self.assertEqual(reason, "snapshot_stale")

    def test_stale_snapshot_runs_the_existing_full_composer_path(self) -> None:
        device, composer = device_for(hierarchy())
        evidence, _, _ = self.make_evidence(device)
        assert evidence is not None
        evidence["created_monotonic"] = time.perf_counter() - 2.0
        logs: list[tuple[str, dict]] = []
        state: dict[str, object] = {}
        with (
            patch.object(dm_sender_engine, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(dm_sender_engine, "_thread_snapshot_supports_composer_fast_path", return_value=(False, "no_snapshot")),
            patch.object(dm_sender_engine, "_dm_audit_non_text_action_candidates"),
            patch.object(dm_sender_engine, "_dm_find_focus_composer", side_effect=[composer, composer]) as full_resolve,
            patch.object(dm_sender_engine.time, "sleep"),
            patch.object(
                dm_sender_engine,
                "log",
                side_effect=lambda _level, event, **fields: logs.append((event, fields)),
            ),
        ):
            resolved, error = dm_sender_engine._resolve_dm_text_composer(
                device,
                pkg=PKG,
                username="recipient",
                caller="welcome_list_sender",
                dm_type="welcome",
                welcome_composer_evidence=evidence,
                account_id="account-1",
                run_id="run-1",
                job_id="job-1",
                navigation_generation="nav-1",
                fast_path_state=state,
            )

        self.assertIs(resolved, composer)
        self.assertIsNone(error)
        self.assertFalse(state["used"])
        self.assertEqual(state["invalidation_reason"], "snapshot_stale")
        self.assertEqual(full_resolve.call_count, 2)
        self.assertIn(
            "welcome_composer_full_path_completed",
            [name for name, _fields in logs],
        )

    def test_review_popup_rejects_evidence(self) -> None:
        device, _ = device_for(hierarchy(popup=True))
        evidence, reason, _ = self.make_evidence(device)
        self.assertIsNone(evidence)
        self.assertEqual(reason, "account_review_popup")

    def test_non_empty_composer_rejects_evidence(self) -> None:
        device, _ = device_for(hierarchy(composer_text="old draft"), current_text="old draft")
        evidence, reason, _ = self.make_evidence(device)
        self.assertIsNone(evidence)
        self.assertEqual(reason, "composer_not_empty")

    def test_navigation_generation_change_invalidates(self) -> None:
        device, _ = device_for(hierarchy())
        evidence, _, _ = self.make_evidence(device)
        resolved, reason, _age_ms, _observed = self.resolve(
            device, evidence or {}, navigation_generation="nav-2"
        )
        self.assertIsNone(resolved)
        self.assertEqual(reason, "navigation_generation_changed")

    def test_exact_selector_absent_never_taps(self) -> None:
        device, composer = device_for(hierarchy())
        evidence, _, _ = self.make_evidence(device)
        composer.exists.return_value = False
        resolved, reason, _age_ms, _observed = self.resolve(device, evidence or {})
        self.assertIsNone(resolved)
        self.assertEqual(reason, "composer_exact_selector_absent")
        composer.click.assert_not_called()

    def test_fast_path_keeps_header_draft_and_outbound_proof(self) -> None:
        device, composer = device_for(hierarchy())
        logs: list[tuple[str, dict]] = []
        with (
            patch.object(
                dm_sender_engine,
                "verify_welcome_dm_thread_recipient_exact",
                side_effect=[
                    (True, "exact_thread_header", "recipient"),
                    (True, "exact_thread_header", "recipient"),
                ],
            ) as identity,
            patch.object(dm_sender_engine, "dm_thread_shows_outgoing_message", return_value=False),
            patch.object(dm_sender_engine, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(dm_sender_engine, "_dm_find_focus_composer") as exhaustive_focus,
            patch.object(dm_sender_engine, "verify_dm_composer_safe") as exhaustive_verify,
            patch.object(dm_sender_engine, "read_dm_composer_text", side_effect=["", "Hello"]),
            patch.object(
                dm_sender_engine,
                "type_dm_draft_only",
                return_value=(True, {"method": "set_text"}),
            ) as type_draft,
            patch.object(dm_sender_engine, "verify_dm_draft_text", return_value=True) as verify_draft,
            patch.object(
                dm_sender_engine,
                "send_dm_safe",
                return_value={"sent": True, "post_send_signal_reason": "new_outbound_bubble"},
            ) as send_safe,
            patch.object(
                dm_sender_engine,
                "finalize_after_real_send",
                return_value={"back_to_profile_ok": True},
            ),
            patch.object(
                dm_sender_engine,
                "log",
                side_effect=lambda _level, event, **fields: logs.append((event, fields)),
            ),
        ):
            ok, _out, reason = dm_sender_engine._perform_real_welcome_dm_send(
                device,
                username="recipient",
                message_body="Hello",
                thread_state="empty_new_thread",
                pkg=PKG,
                account_id="account-1",
                run_id="run-1",
                job_id="job-1",
                navigation_generation="nav-1",
            )

        self.assertTrue(ok)
        self.assertIsNone(reason)
        self.assertEqual(identity.call_count, 2)
        verify_draft.assert_called_once_with(device, "Hello")
        send_safe.assert_called_once()
        exhaustive_focus.assert_not_called()
        exhaustive_verify.assert_not_called()
        self.assertIs(type_draft.call_args.kwargs["composer"], composer)
        event = next(fields for name, fields in logs if name == "welcome_composer_fast_path_used")
        self.assertTrue(event["header_revalidated_before_send"])
        self.assertTrue(event["draft_revalidated_before_send"])

    def test_send_without_outbound_proof_is_not_success(self) -> None:
        device, _ = device_for(hierarchy())
        with (
            patch.object(
                dm_sender_engine,
                "verify_welcome_dm_thread_recipient_exact",
                side_effect=[
                    (True, "exact_thread_header", "recipient"),
                    (True, "exact_thread_header", "recipient"),
                ],
            ),
            patch.object(dm_sender_engine, "dm_thread_shows_outgoing_message", return_value=False),
            patch.object(dm_sender_engine, "_check_dm_sender_permission_blocker", return_value=False),
            patch.object(dm_sender_engine, "read_dm_composer_text", side_effect=["", "Hello"]),
            patch.object(dm_sender_engine, "type_dm_draft_only", return_value=(True, {})),
            patch.object(dm_sender_engine, "verify_dm_draft_text", return_value=True),
            patch.object(
                dm_sender_engine,
                "send_dm_safe",
                return_value={"sent": False, "reason": "outbound_bubble_not_verified"},
            ),
        ):
            ok, _out, reason = dm_sender_engine._perform_real_welcome_dm_send(
                device,
                username="recipient",
                message_body="Hello",
                thread_state="empty_new_thread",
                pkg=PKG,
                account_id="account-1",
                run_id="run-1",
                job_id="job-1",
                navigation_generation="nav-1",
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "outbound_bubble_not_verified")


if __name__ == "__main__":
    unittest.main()
