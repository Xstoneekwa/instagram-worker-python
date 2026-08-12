from __future__ import annotations

import ast
import copy
import itertools
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import account_session_orchestrator as session


PRODUCTION_BASE_SHA = "8f6d16c772236450b22d167239223ca3eae30e7d"
ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
REVIEWED_SUCCESSOR_RUNTIME_DELTAS = {
    "account_session_resume_engine.py",
    "instagram_navigation.py",
    "runner.py",
    "supabase_client.py",
    "unfollow_session_orchestrator.py",
}


class FakeFollowersEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.last_session_summary: dict = {}

    def __call__(self, _device, **kwargs):
        self.calls.append(dict(kwargs))
        self.last_session_summary = {
            "follows_completed_count": 1,
            "follow_session_outcome": "follows_completed",
            "follow_stop_reason": "",
            "candidates_seen_count": 1,
            "candidates_opened_count": 1,
            "candidates_rejected_count": 0,
        }
        return 0


def _function(source: str, name: str) -> ast.FunctionDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("missing function %s" % name)


def _without_availability_or_provenance_hooks(node: ast.FunctionDef) -> ast.FunctionDef:
    normalized = copy.deepcopy(node)
    for index, argument in list(enumerate(normalized.args.kwonlyargs))[::-1]:
        if argument.arg in {
            "tenant_id",
            "target_availability_scope_rejection_reason",
            "target_followers_resume_source_request_id",
            "auto_restart_resume_policy",
            "worker_runtime_identity",
        }:
            normalized.args.kwonlyargs.pop(index)
            normalized.args.kw_defaults.pop(index)

    class RemoveAvailability(ast.NodeTransformer):
        @staticmethod
        def _strip_provenance_keys(value):
            if not isinstance(value, ast.Dict):
                return
            retained = [
                (key, item_value)
                for key, item_value in zip(value.keys, value.values)
                if not (
                    isinstance(key, ast.Constant)
                    and key.value
                    in {
                        "target_followers_resume_source_request_id",
                        "auto_restart_resume_policy",
                        "worker_runtime_identity",
                    }
                )
            ]
            value.keys = [key for key, _item_value in retained]
            value.values = [item_value for _key, item_value in retained]

        def visit_Assign(self, item):
            if any(isinstance(target, ast.Name) and target.id == "stable_platform_user_id" for target in item.targets):
                return None
            if (
                any(
                    isinstance(target, ast.Name) and target.id == "call_kwargs"
                    for target in item.targets
                )
            ):
                self._strip_provenance_keys(item.value)
            return self.generic_visit(item)

        def visit_AnnAssign(self, item):
            if isinstance(item.target, ast.Name) and item.target.id == "call_kwargs":
                self._strip_provenance_keys(item.value)
            return self.generic_visit(item)

        def visit_Expr(self, item):
            call = item.value
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_observe_target_availability":
                return None
            return self.generic_visit(item)

    return ast.fix_missing_locations(RemoveAvailability().visit(normalized))


def _without_reviewed_resume_quota_bound(node: ast.FunctionDef) -> ast.FunctionDef:
    """Remove only the reviewed Auto Restart Follow hard-bound delta for parity."""
    normalized = copy.deepcopy(node)
    for index, argument in list(enumerate(normalized.args.kwonlyargs))[::-1]:
        if argument.arg == "authorized_follow_quota":
            normalized.args.kwonlyargs.pop(index)
            normalized.args.kw_defaults.pop(index)

    legacy_assignment = ast.parse(
        "global_follow_goal: int | None = None"
    ).body[0]

    class RemoveReviewedQuotaBound(ast.NodeTransformer):
        def visit_Assign(self, item):
            if any(
                isinstance(target, ast.Name) and target.id == "global_follow_goal"
                for target in item.targets
            ) and any(
                isinstance(child, ast.Name) and child.id == "authorized_follow_quota"
                for child in ast.walk(item.value)
            ):
                return copy.deepcopy(legacy_assignment)
            return self.generic_visit(item)

        def visit_If(self, item):
            if any(
                isinstance(child, ast.Constant)
                and child.value == "auto_restart_follow_quota_hard_bound_applied"
                for child in ast.walk(item)
            ):
                return None
            return self.generic_visit(item)

        def visit_Call(self, item):
            updated = self.generic_visit(item)
            updated.keywords = [
                keyword
                for keyword in updated.keywords
                if keyword.arg != "authorized_follow_quota"
            ]
            return updated

    return ast.fix_missing_locations(RemoveReviewedQuotaBound().visit(normalized))


def _without_reviewed_follow60_binding(node: ast.FunctionDef) -> ast.FunctionDef:
    """Remove only the reviewed Loriele Follow60 binding transport delta."""
    normalized = copy.deepcopy(node)
    reviewed_arguments = {
        "run_request_id",
        "follow60_canary_active",
        "follow60_canary_control",
        "follow60_attempt_id",
        "business_session_id",
        "follow60_mainline_active",
        "follow60_business_session_binding",
    }
    for index, argument in list(enumerate(normalized.args.kwonlyargs))[::-1]:
        if argument.arg in reviewed_arguments:
            normalized.args.kwonlyargs.pop(index)
            normalized.args.kw_defaults.pop(index)

    class RemoveReviewedFollow60Binding(ast.NodeTransformer):
        def visit_AnnAssign(self, item):
            if (
                isinstance(item.target, ast.Name)
                and item.target.id == "mainline_session_binding"
            ):
                return None
            return self.generic_visit(item)

        def visit_If(self, item):
            if (
                isinstance(item.test, ast.Name)
                and item.test.id in {"follow60_canary_active", "follow60_mainline_active"}
            ):
                return None
            return self.generic_visit(item)

        def visit_Dict(self, item):
            updated = self.generic_visit(item)
            pairs = [
                (key, value)
                for key, value in zip(updated.keys, updated.values)
                if not (
                    isinstance(key, ast.Constant)
                    and key.value in reviewed_arguments
                )
            ]
            updated.keys = [key for key, _value in pairs]
            updated.values = [value for _key, value in pairs]
            return updated

    return ast.fix_missing_locations(RemoveReviewedFollow60Binding().visit(normalized))


def _without_reviewed_follow60_evaluation_barrier(node: ast.FunctionDef) -> ast.FunctionDef:
    """Remove only the reviewed Follow60 terminal evaluation barrier delta."""
    normalized = copy.deepcopy(node)

    class RemoveReviewedEvaluationBarrier(ast.NodeTransformer):
        def visit_If(self, item):
            if any(
                isinstance(child, ast.Constant)
                and child.value == "follow60_evaluation_barrier_rotation_blocked"
                for child in ast.walk(item)
            ):
                return None
            return self.generic_visit(item)

    return ast.fix_missing_locations(
        RemoveReviewedEvaluationBarrier().visit(normalized)
    )


class TargetAvailabilityDisabledParityTests(unittest.TestCase):
    def test_rotation_implementation_matches_production_after_reviewed_deltas(self):
        root = Path(__file__).resolve().parents[1]
        baseline = subprocess.run(
            ["git", "show", "%s:account_session_orchestrator.py" % PRODUCTION_BASE_SHA],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(baseline.returncode, 0, baseline.stderr)
        current_source = (root / "account_session_orchestrator.py").read_text(encoding="utf-8")
        expected = _function(baseline.stdout, "_run_follow_target_rotation")
        actual = _without_reviewed_follow60_evaluation_barrier(
            _without_reviewed_follow60_binding(
                _without_reviewed_resume_quota_bound(
                    _without_availability_or_provenance_hooks(
                        _function(current_source, "_run_follow_target_rotation")
                    )
                )
            )
        )
        self.assertEqual(ast.dump(actual, include_attributes=False), ast.dump(expected, include_attributes=False))

    def test_flags_off_outputs_calls_metrics_and_transitions_match_no_hook_path(self):
        def exercise(disable_hook: bool, *, isolation_root: Path):
            engine = FakeFollowersEngine()
            logs: list[tuple[str, str, dict]] = []
            timings: list[tuple[str, dict]] = []
            clock = (float(value) for value in itertools.count())
            patches = [
                patch.object(session, "log", side_effect=lambda level, event, **values: logs.append((level, event, values))),
                patch.object(session, "_startup_timing_log", side_effect=lambda event, _started, **values: timings.append((event, values))),
                patch.object(session.time, "perf_counter", side_effect=lambda: next(clock)),
                patch.dict(
                    os.environ,
                    {
                        "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "false",
                        "TARGET_AVAILABILITY_WRITER_ENABLED": "false",
                        "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": "",
                        "TARGET_AVAILABILITY_CONTROL_FILE": str(
                            isolation_root / "absent-control.json"
                        ),
                        "TARGET_AVAILABILITY_AUTO_KILL_FILE": str(
                            isolation_root / "absent-auto-kill.json"
                        ),
                        "TARGET_AVAILABILITY_KILL_SWITCH_FILE": str(
                            isolation_root / "absent-kill-switch"
                        ),
                    },
                ),
            ]
            if disable_hook:
                patches.append(patch.object(session, "_observe_target_availability", return_value=True))
            for active in patches:
                active.start()
            try:
                result = session._run_follow_target_rotation(
                    object(),
                    account_id=ACCOUNT_ID,
                    account_username="synthetic.account",
                    run_id="synthetic-run",
                    tenant_id="11111111-1111-4111-8111-111111111111",
                    follow_targets=[{
                        "target_id": "44444444-4444-4444-8444-444444444444",
                        "source_profile": "synthetic.target",
                        "target_index": 0,
                        "selection_source": "ig_targets",
                    }],
                    run_followers_list_engine_session=engine,
                    supabase_mode=False,
                    warm_session_used=False,
                    force_stop_used=False,
                    max_targets_per_run=1,
                )
                return result, engine.calls, logs, timings
            finally:
                for active in reversed(patches):
                    active.stop()

        with tempfile.TemporaryDirectory(prefix="target-availability-parity-") as value:
            isolation_root = Path(value)
            with_hooks = exercise(False, isolation_root=isolation_root)
            without_hooks = exercise(True, isolation_root=isolation_root)
        self.assertEqual(with_hooks, without_hooks)

    def test_sensitive_runtime_modules_only_have_reviewed_successor_deltas(self):
        root = Path(__file__).resolve().parents[1]
        sensitive = [
            "account_session_resume_engine.py",
            "instagram_followers_list_engine.py",
            "instagram_navigation.py",
            "instagram_recovery.py",
            "runner.py",
            "run_control_dispatcher.py",
            "supabase_client.py",
            "unfollow_session_orchestrator.py",
        ]
        compared = subprocess.run(
            ["git", "diff", "--name-only", PRODUCTION_BASE_SHA, "--", *sensitive],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(compared.returncode, 0, compared.stderr)
        actual_deltas = {
            value.strip()
            for value in compared.stdout.splitlines()
            if value.strip()
        }
        self.assertEqual(actual_deltas, REVIEWED_SUCCESSOR_RUNTIME_DELTAS)


if __name__ == "__main__":
    unittest.main()
