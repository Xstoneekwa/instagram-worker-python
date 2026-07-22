from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from follow_limit_provenance_shadow import (
    evaluate_follow_limit_shadow,
    shadow_enabled,
    shadow_log_fields,
)


ROOT = Path(__file__).resolve().parents[1]


def payload(package="Growth", day=80, session=80, *, account_id="account-1"):
    return {
        "account_id": account_id,
        "package": package,
        "package_limits": {"day": day, "session": session},
        "account_override": {"present": False, "day": None, "session": None, "source": None},
        "warmup": {"enabled": False, "day": None, "day_cap": None, "session_cap": None},
        "business_effective": {
            "day": day,
            "session": session,
            "limiting_source": "package_default",
            "limiting_reason": "limited_by_package",
        },
    }


def legacy(day=80, session=80, run=80, source="package_default"):
    return {
        "effective_follow_day_cap": day,
        "effective_follow_session_cap": session,
        "effective_follow_max": run,
        "follow_day_remaining_today": day,
        "limiting_source": source,
        "source": "legacy_reason",
    }


def evaluate(canonical=None, old=None, **kwargs):
    canonical = payload() if canonical is None else canonical
    default_package = canonical.get("package", "") if isinstance(canonical, dict) else ""
    return evaluate_follow_limit_shadow(
        enabled=kwargs.pop("enabled", True),
        account_id=kwargs.pop("account_id", "account-1"),
        account_username="fixture_user",
        package=kwargs.pop("package", default_package),
        legacy_resolved_limits=old or legacy(),
        canonical_payload=canonical,
        completed_today=kwargs.pop("completed_today", 0),
        **kwargs,
    )


class FollowLimitProvenanceShadowTest(unittest.TestCase):
    def test_01_flag_absent_is_disabled(self):
        self.assertEqual(evaluate(enabled=None)["comparison"]["classification"], "shadow_disabled")

    def test_02_flag_false_is_disabled(self):
        self.assertEqual(evaluate(enabled=False)["shadow_status"], "disabled")

    def test_03_growth_without_override_or_warmup(self):
        self.assertEqual(evaluate()["shadow_runtime"]["final_run_cap"], 80)

    def test_04_pro_without_override(self):
        self.assertEqual(evaluate(payload("Pro", 120, 120), old=legacy(120, 120, 120))["shadow_status"], "evaluated")

    def test_05_premium_without_override(self):
        self.assertEqual(evaluate(payload("Premium", 120, 120), old=legacy(120, 120, 120))["shadow_runtime"]["day_cap"], 120)

    def _warmup(self, package_name, package_cap, warmup_day, warmup_cap):
        item = payload(package_name, package_cap, package_cap)
        item["warmup"] = {"enabled": True, "day": warmup_day, "day_cap": warmup_cap, "session_cap": warmup_cap}
        item["business_effective"] = {"day": warmup_cap, "session": warmup_cap, "limiting_source": "warmup", "limiting_reason": f"warmup_day_{warmup_day}"}
        return item

    def test_06_growth_warmup_day_1(self):
        self.assertEqual(evaluate(self._warmup("Growth", 80, 1, 10), old=legacy(10, 10, 10, "warmup"))["shadow_runtime"]["day_cap"], 10)

    def test_07_growth_warmup_day_2(self):
        self.assertEqual(evaluate(self._warmup("Growth", 80, 2, 20), old=legacy(20, 20, 20, "warmup"))["shadow_runtime"]["session_cap"], 20)

    def test_08_growth_warmup_day_3(self):
        self.assertEqual(evaluate(self._warmup("Growth", 80, 3, 40), old=legacy(40, 40, 40, "warmup"))["shadow_runtime"]["final_run_cap"], 40)

    def test_09_growth_day_4_plus(self):
        self.assertEqual(evaluate(self._warmup("Growth", 80, 4, 80))["shadow_status"], "evaluated")

    def test_10_pro_day_4_plus(self):
        self.assertEqual(evaluate(self._warmup("Pro", 120, 4, 120), old=legacy(120, 120, 120))["shadow_status"], "evaluated")

    def test_11_premium_day_4_plus(self):
        self.assertEqual(evaluate(self._warmup("Premium", 120, 5, 120), old=legacy(120, 120, 120))["shadow_status"], "evaluated")

    def test_12_explicit_override_lower(self):
        item = payload("Pro", 120, 120)
        item["account_override"] = {"present": True, "day": 60, "session": 20, "source": "admin"}
        item["business_effective"] = {"day": 60, "session": 20, "limiting_source": "account_override", "limiting_reason": "limited_by_account_override"}
        self.assertEqual(evaluate(item, old=legacy(60, 20, 20, "account_override"))["shadow_status"], "evaluated")

    def test_13_explicit_session_only_override(self):
        item = payload("Pro", 120, 120)
        item["account_override"] = {"present": True, "day": None, "session": 20, "source": "support"}
        item["business_effective"] = {"day": 120, "session": 20, "limiting_source": "mixed", "limiting_reason": "day_limited_by_package_default;session_limited_by_account_override"}
        self.assertEqual(evaluate(item, old=legacy(120, 20, 20, "mixed"))["shadow_runtime"]["session_cap"], 20)

    def test_14_override_above_package_is_bounded(self):
        item = payload()
        item["account_override"] = {"present": True, "day": 120, "session": 120, "source": "migration_confirmed"}
        item["business_effective"]["limiting_reason"] = "override_above_package_bounded"
        self.assertEqual(evaluate(item)["shadow_runtime"]["day_cap"], 80)

    def test_15_ops_day_hard_cap_lower(self):
        self.assertEqual(evaluate(ops_day_hard_cap=50)["shadow_runtime"]["day_cap"], 50)

    def test_16_ops_session_hard_cap_lower(self):
        self.assertEqual(evaluate(ops_session_hard_cap=25)["shadow_runtime"]["session_cap"], 25)

    def test_17_remaining_today_lower(self):
        self.assertEqual(evaluate(completed_today=75)["shadow_runtime"]["remaining_today"], 5)

    def test_18_run_specific_cap_lower(self):
        self.assertEqual(evaluate(run_specific_hard_cap=7)["shadow_runtime"]["final_run_cap"], 7)

    def test_19_exact_match(self):
        self.assertEqual(evaluate()["comparison"]["classification"], "exact_match")

    def test_20_numeric_match_source_difference(self):
        self.assertEqual(evaluate(old=legacy(source="account_follow_session_cap"))["comparison"]["classification"], "numeric_match_source_difference")

    def test_21_canonical_lower_than_legacy(self):
        self.assertEqual(evaluate(old=legacy(120, 120, 120))["comparison"]["classification"], "canonical_lower_than_legacy")

    def test_22_canonical_higher_than_legacy(self):
        self.assertEqual(evaluate(old=legacy(40, 40, 40))["comparison"]["classification"], "canonical_higher_than_legacy")

    def test_23_mixed_difference(self):
        self.assertEqual(evaluate(old=legacy(40, 120, 40))["comparison"]["classification"], "mixed_difference")

    def test_24_payload_absent(self):
        result = evaluate_follow_limit_shadow(enabled=True, account_id="account-1", account_username=None, package="Growth", legacy_resolved_limits=legacy(), canonical_payload=None, completed_today=0)
        self.assertEqual(result["shadow_status"], "not_evaluable")

    def test_25_payload_scalar_invalid(self):
        self.assertEqual(evaluate("invalid", package="Growth")["shadow_status"], "invalid_payload")

    def test_26_package_without_follow(self):
        self.assertEqual(evaluate(payload("Outreach", 30, 30), old=legacy(30, 30, 30))["shadow_status"], "invalid_payload")

    def test_27_counter_already_reached(self):
        self.assertEqual(evaluate(completed_today=80)["shadow_runtime"]["final_run_cap"], 0)

    def test_28_evaluation_does_not_mutate_legacy_authority(self):
        old = legacy()
        before = copy.deepcopy(old)
        evaluate(old=old, ops_session_hard_cap=1)
        self.assertEqual(old, before)

    def _runner_shadow_block(self):
        text = (ROOT / "runner.py").read_text(encoding="utf-8")
        return text.split('if bool(getattr(config, "FOLLOW_LIMIT_PROVENANCE_SHADOW_ENABLED", False)):', 1)[1].split("global_follow_goal_effective =", 1)[0]

    def test_29_shadow_block_does_not_change_exit_code(self):
        self.assertNotIn("exit_code", self._runner_shadow_block())

    def test_30_shadow_block_does_not_change_safe_stop(self):
        self.assertNotIn("safe_stop", self._runner_shadow_block())

    def test_31_shadow_block_does_not_change_resume_plan(self):
        self.assertNotIn("resume", self._runner_shadow_block())

    def test_32_shadow_block_catches_exceptions_fail_open(self):
        self.assertIn("except Exception:", self._runner_shadow_block())

    def test_33_log_fields_are_strictly_redacted(self):
        item = payload()
        item["token"] = "secret-value"
        result = evaluate(payload())
        fields = shadow_log_fields(result, account_id="account-1", account_username="user", run_id="run-1", package="Growth")
        serialized = json.dumps(fields)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("payload", serialized)

    def test_34_module_does_not_import_uiautomator2(self):
        self.assertNotIn("uiautomator2", (ROOT / "follow_limit_provenance_shadow.py").read_text())

    def test_35_module_has_no_network_import(self):
        text = (ROOT / "follow_limit_provenance_shadow.py").read_text()
        self.assertNotIn("requests", text)
        self.assertNotIn("supabase", text.lower())

    def test_36_account_mismatch_is_invalid(self):
        self.assertEqual(evaluate(account_id="other")["shadow_status"], "invalid_payload")

    def test_37_unknown_field_is_invalid(self):
        item = payload()
        item["unknown"] = 1
        self.assertEqual(evaluate(item)["shadow_status"], "invalid_payload")

    def test_38_nonpositive_cap_is_invalid(self):
        item = payload()
        item["package_limits"]["day"] = 0
        self.assertEqual(evaluate(item)["shadow_status"], "invalid_payload")

    def test_39_unbounded_override_source_is_invalid(self):
        item = payload()
        item["account_override"] = {"present": True, "day": 10, "session": None, "source": "legacy"}
        self.assertEqual(evaluate(item)["shadow_status"], "invalid_payload")

    def test_40_missing_warmup_snapshot_is_not_evaluable(self):
        fixtures = json.loads((ROOT / "tests/fixtures/follow_limit_shadow_v1.json").read_text())
        item = fixtures["i_m_your_traker"]
        self.assertEqual(evaluate(item, account_id=item["account_id"])["shadow_status"], "not_evaluable")

    def test_41_negative_completed_counter_is_invalid(self):
        self.assertEqual(evaluate(completed_today=-1)["shadow_status"], "invalid_payload")

    def test_42_flag_parser_is_explicit(self):
        self.assertTrue(shadow_enabled("true"))
        self.assertFalse(shadow_enabled("enforce"))


if __name__ == "__main__":
    unittest.main()
