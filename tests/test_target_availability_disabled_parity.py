from __future__ import annotations

import ast
import copy
import itertools
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import account_session_orchestrator as session


PRODUCTION_BASE_SHA = "8f6d16c772236450b22d167239223ca3eae30e7d"
ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
REVIEWED_SUCCESSOR_RUNTIME_DELTAS = {
    "instagram_navigation.py",
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


def _without_availability_hooks(node: ast.FunctionDef) -> ast.FunctionDef:
    normalized = copy.deepcopy(node)
    for index, argument in list(enumerate(normalized.args.kwonlyargs))[::-1]:
        if argument.arg == "tenant_id":
            normalized.args.kwonlyargs.pop(index)
            normalized.args.kw_defaults.pop(index)

    class RemoveAvailability(ast.NodeTransformer):
        def visit_Assign(self, item):
            if any(isinstance(target, ast.Name) and target.id == "stable_platform_user_id" for target in item.targets):
                return None
            return self.generic_visit(item)

        def visit_Expr(self, item):
            call = item.value
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_observe_target_availability":
                return None
            return self.generic_visit(item)

    return ast.fix_missing_locations(RemoveAvailability().visit(normalized))


class TargetAvailabilityDisabledParityTests(unittest.TestCase):
    def test_rotation_implementation_matches_production_after_removing_two_hooks(self):
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
        actual = _without_availability_hooks(_function(current_source, "_run_follow_target_rotation"))
        self.assertEqual(ast.dump(actual, include_attributes=False), ast.dump(expected, include_attributes=False))

    def test_flags_off_outputs_calls_metrics_and_transitions_match_no_hook_path(self):
        def exercise(disable_hook: bool):
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

        with_hooks = exercise(False)
        without_hooks = exercise(True)
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
