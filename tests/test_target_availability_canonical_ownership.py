from __future__ import annotations

from datetime import datetime, timezone
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import account_session_orchestrator as orchestrator
import supabase_client
import target_availability_runtime as runtime
from target_availability_ownership import (
    OWNERSHIP_CONFLICT_REASON,
    OWNERSHIP_SOURCE,
    resolve_target_availability_tenant,
)
from target_availability_writer import SCOPE_MODE_EXPLICIT, TargetAvailabilityFeatureFlags


MYTHYL_ACCOUNT_ID = "0d299d1e-46ee-49d2-8a84-4f928f2bb182"
MYTHYL_CLIENT_ID = "4a9b1a8c-6eb0-46d0-a1fc-821f38e1e031"
LORIELE_ACCOUNT_ID = "dfe78a92-3a51-435e-8911-ed10c93a4d82"
LORIELE_CLIENT_ID = "aefbca70-fc91-4be8-bc44-c7b8ad776272"
TARGET_ONE = "11111111-1111-4111-8111-111111111111"
TARGET_TWO = "22222222-2222-4222-8222-222222222222"


def _row(account_id=MYTHYL_ACCOUNT_ID, client_id=MYTHYL_CLIENT_ID, active=True):
    return {"account_id": account_id, "client_id": client_id, "active": active, "updated_at": "2026-07-29T00:00:00Z"}


class TargetAvailabilityCanonicalOwnershipTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        isolation_directory = tempfile.TemporaryDirectory(
            prefix="target-availability-ownership-test-"
        )
        self.addCleanup(isolation_directory.cleanup)
        environment_patch = patch.dict(
            os.environ,
            {
                "TARGET_AVAILABILITY_CONTROL_FILE": str(
                    Path(isolation_directory.name) / "absent-control.json"
                ),
                "TARGET_AVAILABILITY_AUTO_KILL_FILE": str(
                    Path(isolation_directory.name) / "absent-auto-kill.json"
                ),
            },
            clear=False,
        )
        environment_patch.start()
        self.addCleanup(environment_patch.stop)

    def tearDown(self):
        probe = runtime._MEMORY_PROBE
        runtime._MEMORY_PROBE = None
        if probe is not None:
            probe.cleanup()

    def test_active_package_without_revision_resolves_canonical_tenant(self):
        result = resolve_target_availability_tenant(
            MYTHYL_ACCOUNT_ID,
            commercial_policy_revision={"package_code": "premium", "revision_token": "package:premium"},
            ownership_reader=lambda _aid: [_row()],
            resolved_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        )
        self.assertTrue(result.available)
        self.assertEqual(result.tenant_id, MYTHYL_CLIENT_ID)
        self.assertEqual(result.ownership.source, OWNERSHIP_SOURCE)
        self.assertEqual(result.lookup_count, 1)

    def test_legacy_account_without_revision_resolves(self):
        result = resolve_target_availability_tenant(
            MYTHYL_ACCOUNT_ID,
            commercial_policy_revision=None,
            ownership_reader=lambda _aid: [_row()],
        )
        self.assertTrue(result.available)

    def test_matching_revision_is_consistency_signal(self):
        result = resolve_target_availability_tenant(
            MYTHYL_ACCOUNT_ID,
            commercial_policy_revision={"client_id": MYTHYL_CLIENT_ID, "package_code": "premium"},
            ownership_reader=lambda _aid: [_row()],
        )
        self.assertTrue(result.available)

    def test_divergent_revision_fails_availability_closed(self):
        result = resolve_target_availability_tenant(
            MYTHYL_ACCOUNT_ID,
            commercial_policy_revision={"client_id": LORIELE_CLIENT_ID},
            ownership_reader=lambda _aid: [_row()],
        )
        self.assertFalse(result.available)
        self.assertEqual(result.reason_code, OWNERSHIP_CONFLICT_REASON)

    def test_missing_inactive_ambiguous_unknown_and_malformed_fail_closed(self):
        cases = [
            (lambda _aid: [], "target_availability_tenant_ownership_missing"),
            (lambda _aid: [_row(active=False)], "target_availability_tenant_ownership_inactive"),
            (lambda _aid: [_row(), _row(client_id=LORIELE_CLIENT_ID)], "target_availability_tenant_ownership_ambiguous"),
            (lambda _aid: {"unexpected": True}, "target_availability_tenant_ownership_response_malformed"),
            (lambda _aid: [{"account_id": MYTHYL_ACCOUNT_ID, "active": True}], "target_availability_tenant_ownership_response_malformed"),
        ]
        for reader, reason in cases:
            with self.subTest(reason=reason):
                result = resolve_target_availability_tenant(MYTHYL_ACCOUNT_ID, ownership_reader=reader)
                self.assertFalse(result.available)
                self.assertEqual(result.reason_code, reason)
        unknown = resolve_target_availability_tenant("unknown-account", ownership_reader=lambda _aid: [_row()])
        self.assertEqual(unknown.reason_code, "target_availability_tenant_account_id_invalid")
        self.assertEqual(unknown.lookup_count, 0)

    def test_timeout_is_contained_and_does_not_escape(self):
        def timeout(_aid):
            raise TimeoutError("bounded timeout")

        result = resolve_target_availability_tenant(MYTHYL_ACCOUNT_ID, ownership_reader=timeout)
        self.assertFalse(result.available)
        self.assertEqual(result.reason_code, "target_availability_tenant_ownership_lookup_failed")
        self.assertEqual(result.lookup_count, 1)

    def test_supabase_reader_is_exact_active_and_bounded_for_ambiguity(self):
        with patch.object(supabase_client, "_request_json", return_value=[_row()]) as request_json:
            rows = supabase_client.get_active_client_instagram_account_ownership_rows(MYTHYL_ACCOUNT_ID)
        self.assertEqual(rows, [_row()])
        request_json.assert_called_once_with(
            "GET",
            "client_instagram_accounts",
            query={
                "select": "account_id,client_id,active,updated_at",
                "account_id": "eq.%s" % MYTHYL_ACCOUNT_ID,
                "active": "eq.true",
                "limit": "2",
            },
            request_timeout=3.0,
            max_retries=0,
        )

    def test_mythyl_and_loriele_fixtures_resolve_independently(self):
        for account_id, client_id in (
            (MYTHYL_ACCOUNT_ID, MYTHYL_CLIENT_ID),
            (LORIELE_ACCOUNT_ID, LORIELE_CLIENT_ID),
        ):
            with self.subTest(account_id=account_id):
                result = resolve_target_availability_tenant(
                    account_id,
                    ownership_reader=lambda aid, cid=client_id: [_row(account_id=aid, client_id=cid)],
                )
                self.assertEqual(result.tenant_id, client_id)

    def test_flags_off_and_kill_switch_avoid_ownership_read(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "false",
                "TARGET_AVAILABILITY_CONTROL_FILE": str(
                    Path(directory) / "absent-control"
                ),
                "TARGET_AVAILABILITY_AUTO_KILL_FILE": str(
                    Path(directory) / "absent-auto-kill"
                ),
            }
            with patch.dict(os.environ, environment, clear=False), patch(
                "target_availability_ownership.resolve_target_availability_tenant",
                side_effect=AssertionError("ownership read forbidden"),
            ):
                self.assertEqual(
                    orchestrator._resolve_target_availability_tenant_once(
                        MYTHYL_ACCOUNT_ID,
                        run_id="run-off",
                        commercial_policy_revision={},
                    ),
                    (None, None),
                )
        with tempfile.TemporaryDirectory() as directory:
            kill_switch = Path(directory) / "kill-switch"
            kill_switch.write_text("ON\n", encoding="utf-8")
            environment = {
                "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
                "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": MYTHYL_ACCOUNT_ID,
                "TARGET_AVAILABILITY_KILL_SWITCH_FILE": str(kill_switch),
                "TARGET_AVAILABILITY_CONTROL_FILE": str(
                    Path(directory) / "absent-control"
                ),
                "TARGET_AVAILABILITY_AUTO_KILL_FILE": str(
                    Path(directory) / "absent-auto-kill"
                ),
            }
            with patch.dict(os.environ, environment, clear=False), patch(
                "target_availability_ownership.resolve_target_availability_tenant",
                side_effect=AssertionError("ownership read forbidden"),
            ):
                self.assertEqual(
                    orchestrator._resolve_target_availability_tenant_once(
                        MYTHYL_ACCOUNT_ID, run_id="run-killed", commercial_policy_revision={}
                    ),
                    (None, None),
                )

    def test_resolution_failure_never_raises_into_instagram_run(self):
        environment = {
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": MYTHYL_ACCOUNT_ID,
            "TARGET_AVAILABILITY_KILL_SWITCH_FILE": "/private/tmp/does-not-exist-target-availability-kill-switch",
        }
        with patch.dict(os.environ, environment, clear=False), patch(
            "target_availability_ownership.resolve_target_availability_tenant",
            side_effect=TimeoutError("secret-service-role-value"),
        ), patch.object(orchestrator, "log") as safe_log:
            tenant_id, reason = orchestrator._resolve_target_availability_tenant_once(
                MYTHYL_ACCOUNT_ID, run_id="run-one", commercial_policy_revision={}
            )
        self.assertIsNone(tenant_id)
        self.assertEqual(reason, "target_availability_tenant_ownership_lookup_failed")
        self.assertNotIn("secret-service-role-value", repr(safe_log.call_args_list))

    def test_one_resolution_per_run_and_no_lookup_per_target_hook(self):
        environment = {
            "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
            "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": MYTHYL_ACCOUNT_ID,
            "TARGET_AVAILABILITY_KILL_SWITCH_FILE": "/private/tmp/does-not-exist-target-availability-kill-switch",
        }
        reader_calls = []

        def reader(aid):
            reader_calls.append(aid)
            return [_row(account_id=aid)]

        with patch.dict(os.environ, environment, clear=False), patch(
            "supabase_client.get_active_client_instagram_account_ownership_rows",
            side_effect=reader,
        ):
            tenant_id, reason = orchestrator._resolve_target_availability_tenant_once(
                MYTHYL_ACCOUNT_ID, run_id="run-one", commercial_policy_revision={"package_code": "premium"}
            )

        summaries = [
            {"follows_completed_count": 0, "follow_stop_reason": "bounded_exploration_exhausted"},
            {"follows_completed_count": 0, "follow_stop_reason": "bounded_exploration_exhausted"},
        ]

        class Engine:
            last_session_summary = {}

            def __call__(self, _device, **_kwargs):
                self.last_session_summary = summaries.pop(0)
                return 0

        observed = []
        with patch.object(orchestrator, "_observe_target_availability", side_effect=lambda hook, **kwargs: observed.append((hook, kwargs)) or True):
            orchestrator._run_follow_target_rotation(
                object(),
                account_id=MYTHYL_ACCOUNT_ID,
                account_username="mythyl_fitness",
                run_id="run-one",
                tenant_id=tenant_id,
                follow_targets=[
                    {"target_id": TARGET_ONE, "source_profile": "target.one", "target_index": 0},
                    {"target_id": TARGET_TWO, "source_profile": "target.two", "target_index": 1},
                ],
                run_followers_list_engine_session=Engine(),
                supabase_mode=False,
                warm_session_used=False,
                force_stop_used=False,
                max_targets_per_run=2,
            )
        self.assertIsNone(reason)
        self.assertEqual(reader_calls, [MYTHYL_ACCOUNT_ID])
        self.assertEqual(len(observed), 4)
        self.assertEqual({item[1]["tenant_id"] for item in observed}, {MYTHYL_CLIENT_ID})

    def test_mythyl_fixture_reaches_strict_scope_and_memory_probe_writer_off(self):
        with tempfile.TemporaryDirectory() as directory:
            status = Path(directory) / "status.json"
            environment = {
                "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
                "TARGET_AVAILABILITY_WRITER_ENABLED": "false",
                "TARGET_AVAILABILITY_SHADOW_ENABLED": "false",
                "TARGET_AVAILABILITY_POLICY_SHADOW_ENABLED": "false",
                "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": MYTHYL_ACCOUNT_ID,
                "TARGET_AVAILABILITY_MEMORY_PROBE_ENABLED": "true",
                "TARGET_AVAILABILITY_MEMORY_PROBE_STATUS_FILE": str(status),
                "TARGET_AVAILABILITY_KILL_SWITCH_FILE": str(Path(directory) / "absent-kill-switch"),
            }
            resolution = resolve_target_availability_tenant(
                MYTHYL_ACCOUNT_ID,
                ownership_reader=lambda _aid: [_row()],
            )
            with patch.dict(os.environ, environment, clear=False):
                flags = TargetAvailabilityFeatureFlags.from_mapping()
                self.assertTrue(runtime.observe_rotation_target_loaded(
                    tenant_id=resolution.tenant_id,
                    account_id=MYTHYL_ACCOUNT_ID,
                    target_id=TARGET_ONE,
                    username="target.one",
                    run_id="natural-fixture",
                    target_index=0,
                    flags=flags,
                ))
            payload = json.loads(status.read_text(encoding="utf-8"))["current_run"]
            self.assertEqual(payload["observation_valid_count"], 1)
            self.assertEqual(payload["observation_rejected_count"], 0)
            self.assertEqual(payload["payload_retained_count"], 0)
            self.assertIsNotNone(runtime._scope(
                tenant_id=resolution.tenant_id,
                account_id=MYTHYL_ACCOUNT_ID,
                target_id=TARGET_ONE,
                username="target.one",
            ))
            self.assertIsNone(runtime._WRITER)

    def test_nonpilot_never_resolves_or_executes_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
                "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": MYTHYL_ACCOUNT_ID,
                "TARGET_AVAILABILITY_CONTROL_FILE": str(
                    Path(directory) / "absent-control"
                ),
                "TARGET_AVAILABILITY_AUTO_KILL_FILE": str(
                    Path(directory) / "absent-auto-kill"
                ),
            }
            with patch.dict(os.environ, environment, clear=False), patch(
                "target_availability_ownership.resolve_target_availability_tenant",
                side_effect=AssertionError("nonpilot lookup"),
            ):
                self.assertEqual(
                    orchestrator._resolve_target_availability_tenant_once(
                        LORIELE_ACCOUNT_ID,
                        run_id="control",
                        commercial_policy_revision={},
                    ),
                    (None, None),
                )
                with patch(
                    "target_availability_runtime.observe_rotation_target_loaded",
                    side_effect=AssertionError("nonpilot hook"),
                ):
                    self.assertTrue(
                        orchestrator._observe_target_availability(
                            "loaded",
                            tenant_id=LORIELE_CLIENT_ID,
                            account_id=LORIELE_ACCOUNT_ID,
                            target_id=TARGET_ONE,
                            username="target.one",
                            run_id="control",
                            target_index=0,
                        )
                    )

    def test_scope_remains_strict_and_explicit_conflict_reason_reaches_probe(self):
        self.assertIsNone(runtime._scope(
            tenant_id="",
            account_id=MYTHYL_ACCOUNT_ID,
            target_id=TARGET_ONE,
            username="target.one",
        ))
        with tempfile.TemporaryDirectory() as directory:
            status = Path(directory) / "status.json"
            environment = {
                "TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED": "true",
                "TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST": MYTHYL_ACCOUNT_ID,
                "TARGET_AVAILABILITY_MEMORY_PROBE_ENABLED": "true",
                "TARGET_AVAILABILITY_MEMORY_PROBE_STATUS_FILE": str(status),
            }
            with patch.dict(os.environ, environment, clear=False):
                flags = TargetAvailabilityFeatureFlags(
                    target_availability_observation_capture_enabled=True,
                    scope_mode=SCOPE_MODE_EXPLICIT,
                    account_allowlist=frozenset({MYTHYL_ACCOUNT_ID}),
                )
                self.assertTrue(runtime.observe_rotation_target_loaded(
                    tenant_id="",
                    account_id=MYTHYL_ACCOUNT_ID,
                    target_id=TARGET_ONE,
                    username="target.one",
                    run_id="conflict",
                    target_index=0,
                    scope_rejection_reason=OWNERSHIP_CONFLICT_REASON,
                    flags=flags,
                ))
            current = json.loads(status.read_text(encoding="utf-8"))["current_run"]
            self.assertEqual(current["observation_rejected_count"], 1)
            self.assertEqual(current["last_error_code"], OWNERSHIP_CONFLICT_REASON)

    def test_runtime_source_has_no_ownership_network_call_inside_hooks(self):
        root = Path(__file__).resolve().parents[1]
        runtime_source = (root / "target_availability_runtime.py").read_text(encoding="utf-8")
        rotation_source = inspect.getsource(orchestrator._run_follow_target_rotation)
        self.assertNotIn("client_instagram_accounts", runtime_source)
        self.assertNotIn("get_active_client_instagram_account_ownership_rows", runtime_source)
        self.assertNotIn("resolve_target_availability_tenant", rotation_source)


if __name__ == "__main__":
    unittest.main()
