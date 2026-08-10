import unittest
from unittest.mock import patch

import follow_source_rotation_settings as contract
import instagram_login_provisioner_orchestrator as provisioner


class FollowSourceRotationContractTest(unittest.TestCase):
    def test_audit_missing_row_flags_create_repair(self) -> None:
        with patch.object(
            contract.supabase_client,
            "load_account_follow_source_settings",
            return_value=None,
        ):
            audit = contract.audit_follow_source_rotation_contract(
                "acct-1",
                account_username="mythyl_fitness",
            )
        self.assertFalse(audit.contract_ok)
        self.assertTrue(audit.repair_required)
        self.assertEqual(audit.repair_action, "create_row_30_4")
        self.assertEqual(audit.settings_source, "default")

    def test_audit_contract_ok_for_30_4_row(self) -> None:
        with patch.object(
            contract.supabase_client,
            "load_account_follow_source_settings",
            return_value={
                "account_id": "acct-1",
                "max_follows_per_target_per_run": 30,
                "max_targets_per_run": 4,
            },
        ):
            audit = contract.audit_follow_source_rotation_contract("acct-1")
        self.assertTrue(audit.contract_ok)
        self.assertFalse(audit.repair_required)
        self.assertEqual(audit.repair_action, "none")

    def test_ensure_creates_missing_row_without_update(self) -> None:
        with patch.object(
            contract.supabase_client,
            "load_account_follow_source_settings",
            return_value=None,
        ), patch.object(
            contract.supabase_client,
            "ensure_account_follow_source_settings",
            return_value={
                "account_id": "acct-1",
                "max_follows_per_target_per_run": 30,
                "max_targets_per_run": 4,
            },
        ) as ensure_mock:
            result = contract.ensure_follow_source_rotation_contract_row(
                "acct-1",
                account_username="new_account",
                context="test",
                dry_run=False,
            )
        ensure_mock.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertTrue(result["db_mutation_performed"])
        self.assertEqual(result["action"], "created_row_30_4")

    def test_ensure_dry_run_never_mutates(self) -> None:
        with patch.object(
            contract.supabase_client,
            "load_account_follow_source_settings",
            return_value=None,
        ), patch.object(
            contract.supabase_client,
            "ensure_account_follow_source_settings",
        ) as ensure_mock:
            result = contract.ensure_follow_source_rotation_contract_row(
                "acct-1",
                dry_run=True,
            )
        ensure_mock.assert_not_called()
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["db_mutation_performed"])
        self.assertEqual(result["action"], "would_create_row_30_4")

    def test_ensure_skips_existing_non_contract_row(self) -> None:
        with patch.object(
            contract.supabase_client,
            "load_account_follow_source_settings",
            return_value={
                "account_id": "acct-1",
                "max_follows_per_target_per_run": 2,
                "max_targets_per_run": 3,
            },
        ), patch.object(
            contract.supabase_client,
            "ensure_account_follow_source_settings",
        ) as ensure_mock:
            result = contract.ensure_follow_source_rotation_contract_row("acct-1", dry_run=False)
        ensure_mock.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "existing_non_contract_row_requires_explicit_repair")

    def test_repair_requires_repair_go(self) -> None:
        with patch.object(
            contract.supabase_client,
            "load_account_follow_source_settings",
            return_value={
                "account_id": "acct-1",
                "max_follows_per_target_per_run": 2,
                "max_targets_per_run": 3,
            },
        ), patch.object(
            contract.supabase_client,
            "upsert_account_follow_source_settings",
        ) as upsert_mock:
            dry = contract.repair_follow_source_rotation_contract("acct-1", dry_run=True)
            blocked = contract.repair_follow_source_rotation_contract(
                "acct-1",
                dry_run=False,
                repair_go=False,
            )
        upsert_mock.assert_not_called()
        self.assertTrue(dry["dry_run"])
        self.assertFalse(dry["db_mutation_performed"])
        self.assertTrue(blocked["dry_run"])

    def test_repair_applies_only_with_repair_go(self) -> None:
        with patch.object(
            contract.supabase_client,
            "load_account_follow_source_settings",
            return_value={
                "account_id": "acct-1",
                "max_follows_per_target_per_run": 2,
                "max_targets_per_run": 3,
            },
        ), patch.object(
            contract.supabase_client,
            "upsert_account_follow_source_settings",
            return_value={
                "account_id": "acct-1",
                "max_follows_per_target_per_run": 30,
                "max_targets_per_run": 4,
            },
        ) as upsert_mock:
            result = contract.repair_follow_source_rotation_contract(
                "acct-1",
                dry_run=False,
                repair_go=True,
            )
        upsert_mock.assert_called_once()
        self.assertTrue(result["db_mutation_performed"])
        self.assertEqual(result["action"], "repaired_row_to_30_4")

    def test_provision_on_ready_skips_non_ready_status(self) -> None:
        with patch.object(
            contract,
            "ensure_follow_source_rotation_contract_row",
        ) as ensure_mock:
            result = contract.maybe_provision_follow_source_rotation_on_ready(
                account_id="acct-1",
                final_provisioning_status="login_pending",
            )
        ensure_mock.assert_not_called()
        self.assertTrue(result["skipped"])

    def test_finalize_ready_triggers_provision_hook(self) -> None:
        with patch.object(
            contract,
            "maybe_provision_follow_source_rotation_on_ready",
            return_value={"ok": True, "action": "created_row_30_4", "db_mutation_performed": True},
        ) as provision_mock, patch.object(
            provisioner,
            "clean_login_probe_metadata",
            side_effect=lambda payload: payload,
        ), patch.object(
            provisioner,
            "redact_credentials_payload",
            side_effect=lambda payload: payload,
        ):
            result = provisioner._finalize(
                ok=True,
                completed=True,
                final_outcome="connected",
                reason="connected",
                account_id="acct-1",
                expected_username="mythyl_fitness",
                actions_taken=[],
                timings={},
                warnings=[],
                total_start=0.0,
                timer=lambda: 0.0,
                publisher=None,
                publish_enabled=False,
                final_login_status="connected",
                final_provisioning_status="ready",
                final_onboarding_status="ready",
                extra_metadata={
                    "central_orchestrator_used": True,
                    "expected_username": "mythyl_fitness",
                    "actual_logged_in_username": "mythyl_fitness",
                    "expected_identity_verified": True,
                    "identity_verification_status": "verified",
                    "profile_opened": True,
                },
            )
        provision_mock.assert_called_once()
        self.assertIn("follow_source_rotation_provision", result.safe_metadata)

    def test_finalize_ready_without_identity_proof_does_not_provision_rotation(self) -> None:
        with patch.object(
            contract,
            "maybe_provision_follow_source_rotation_on_ready",
        ) as provision_mock:
            result = provisioner._finalize(
                ok=True,
                completed=True,
                final_outcome="connected",
                reason="connected",
                account_id="acct-1",
                expected_username="mythyl_fitness",
                actions_taken=[],
                timings={},
                warnings=[],
                total_start=0.0,
                timer=lambda: 0.0,
                publisher=None,
                publish_enabled=False,
                final_login_status="connected",
                final_provisioning_status="ready",
                final_onboarding_status="ready",
            )
        provision_mock.assert_not_called()
        self.assertTrue(result.safe_metadata["follow_source_rotation_provision"]["skipped"])


if __name__ == "__main__":
    unittest.main()
