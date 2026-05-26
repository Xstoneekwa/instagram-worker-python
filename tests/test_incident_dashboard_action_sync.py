from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from urllib import error

import incident_dashboard_action_sync as sync


TOKEN = "service-role-not-real"
BASE_URL = "https://example.supabase.co"


class FakeResponse:
    def __init__(self, body: object, status: int = 200) -> None:
        self.body = json.dumps(body).encode("utf-8")
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _incident(incident_id: str, incident_type: str = "active_instagram_account_mismatch") -> dict:
    return {
        "id": incident_id,
        "incident_type": incident_type,
        "status": "open",
        "last_seen_at": "2026-05-26T00:00:00+00:00",
        "created_at": "2026-05-26T00:00:00+00:00",
    }


class IncidentDashboardActionSyncTest(unittest.TestCase):
    def _env(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch.dict("os.environ", {"SUPABASE_URL": BASE_URL, "SUPABASE_SERVICE_ROLE_KEY": TOKEN}))
        stack.enter_context(patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_FAIL_OPEN", True, create=True))
        return stack

    def test_flag_off_disabled_without_http(self) -> None:
        with (
            self._env(),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", False, create=True),
            patch.object(sync.request, "urlopen") as urlopen,
        ):
            out = sync.dispatch_incident_dashboard_action_sync(limit=50)

        self.assertFalse(out["enabled"])
        self.assertEqual(out["reason"], "disabled")
        self.assertEqual(out["selected_count"], 0)
        urlopen.assert_not_called()

    def test_dry_run_lists_incidents_without_rpc(self) -> None:
        calls = []

        def fake_urlopen(req, timeout=0):
            calls.append(req)
            return FakeResponse([_incident("incident-1"), _incident("incident-2", "device_offline")])

        with (
            self._env(),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", True, create=True),
            patch.object(sync.request, "urlopen", side_effect=fake_urlopen),
        ):
            out = sync.dispatch_incident_dashboard_action_sync(dry_run=True, run_id="run-1")

        self.assertEqual(out["reason"], "dry_run")
        self.assertEqual(out["selected_count"], 2)
        self.assertEqual(len(out["results"]), 2)
        self.assertEqual(out["results"][0]["action"], "dry_run")
        self.assertEqual(len(calls), 1)
        self.assertIn("/rest/v1/account_incidents", calls[0].full_url)

    def test_limit_clamped_and_selection_filters_open_acknowledged(self) -> None:
        seen_urls: list[str] = []

        def fake_urlopen(req, timeout=0):
            seen_urls.append(req.full_url)
            return FakeResponse([])

        with (
            self._env(),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", True, create=True),
            patch.object(sync.request, "urlopen", side_effect=fake_urlopen),
        ):
            out = sync.dispatch_incident_dashboard_action_sync(limit=999, dry_run=True)

        self.assertEqual(out["limit"], 200)
        self.assertEqual(out["selected_count"], 0)
        self.assertIn("status=in.%28open%2Cacknowledged%29", seen_urls[0])
        self.assertIn("limit=200", seen_urls[0])
        self.assertIn("order=last_seen_at.desc.nullslast%2Ccreated_at.desc", seen_urls[0])

    def test_sync_calls_rpc_for_each_incident_and_counts(self) -> None:
        bodies: list[dict] = []

        def fake_urlopen(req, timeout=0):
            if "/rest/v1/account_incidents" in req.full_url:
                return FakeResponse([_incident("incident-1"), _incident("incident-2")])
            bodies.append(json.loads(req.data.decode("utf-8")))
            action_id = "action-1" if len(bodies) == 1 else "action-2"
            return FakeResponse(
                {
                    "ok": True,
                    "action": "upserted",
                    "action_type": "review_account_mismatch",
                    "dashboard_action_id": action_id,
                }
            )

        with (
            self._env(),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", True, create=True),
            patch.object(sync.request, "urlopen", side_effect=fake_urlopen),
        ):
            out = sync.dispatch_incident_dashboard_action_sync(run_id="run-123")

        self.assertEqual(out["selected_count"], 2)
        self.assertEqual(out["synced_count"], 2)
        self.assertEqual(out["skipped_count"], 0)
        self.assertEqual(bodies[0]["p_incident_id"], "incident-1")
        self.assertEqual(bodies[0]["p_reason"], "incident_dashboard_reconciliation")
        self.assertEqual(bodies[0]["p_metadata"], {"source": "incident_dashboard_action_sync", "run_id": "run-123"})
        self.assertNotIn(TOKEN, json.dumps(out))

    def test_unsupported_incident_skipped_count(self) -> None:
        def fake_urlopen(req, timeout=0):
            if "/rest/v1/account_incidents" in req.full_url:
                return FakeResponse([_incident("incident-1", "device_offline")])
            return FakeResponse({"ok": True, "action": "skipped", "reason": "unsupported_incident_type"})

        with (
            self._env(),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", True, create=True),
            patch.object(sync.request, "urlopen", side_effect=fake_urlopen),
        ):
            out = sync.dispatch_incident_dashboard_action_sync()

        self.assertEqual(out["selected_count"], 1)
        self.assertEqual(out["skipped_count"], 1)
        self.assertEqual(out["results"][0]["reason"], "unsupported_incident_type")

    def test_rpc_error_fail_open_continues(self) -> None:
        def fake_urlopen(req, timeout=0):
            if "/rest/v1/account_incidents" in req.full_url:
                return FakeResponse([_incident("incident-1"), _incident("incident-2")])
            if b"incident-1" in req.data:
                raise error.URLError("network down")
            return FakeResponse({"ok": True, "action": "upserted", "dashboard_action_id": "action-2"})

        with (
            self._env(),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", True, create=True),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_FAIL_OPEN", True, create=True),
            patch.object(sync.request, "urlopen", side_effect=fake_urlopen),
        ):
            out = sync.dispatch_incident_dashboard_action_sync()

        self.assertEqual(out["selected_count"], 2)
        self.assertEqual(out["error_count"], 1)
        self.assertEqual(out["synced_count"], 1)
        self.assertEqual(out["results"][0]["action"], "error")

    def test_missing_config_fail_open_summary_safe(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", True, create=True),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_FAIL_OPEN", True, create=True),
            patch.object(sync.request, "urlopen") as urlopen,
        ):
            out = sync.dispatch_incident_dashboard_action_sync()

        self.assertEqual(out["reason"], "load_failed")
        self.assertEqual(out["error_count"], 1)
        self.assertNotIn("service-role", json.dumps(out).lower())
        urlopen.assert_not_called()

    def test_missing_config_fail_closed_raises(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", True, create=True),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_FAIL_OPEN", False, create=True),
        ):
            with self.assertRaises(sync.IncidentDashboardActionSyncError):
                sync.dispatch_incident_dashboard_action_sync()

    def test_force_runs_when_flag_off(self) -> None:
        with (
            self._env(),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", False, create=True),
            patch.object(sync.request, "urlopen", return_value=FakeResponse([])) as urlopen,
        ):
            out = sync.dispatch_incident_dashboard_action_sync(force=True, dry_run=True)

        self.assertTrue(out["enabled"])
        self.assertEqual(out["selected_count"], 0)
        urlopen.assert_called_once()

    def test_no_incident_summary_counts(self) -> None:
        with (
            self._env(),
            patch.object(sync.config, "INCIDENT_DASHBOARD_SYNC_ENABLED", True, create=True),
            patch.object(sync.request, "urlopen", return_value=FakeResponse([])),
        ):
            out = sync.dispatch_incident_dashboard_action_sync()

        self.assertEqual(out["selected_count"], 0)
        self.assertEqual(out["synced_count"], 0)
        self.assertEqual(out["skipped_count"], 0)
        self.assertEqual(out["error_count"], 0)
        self.assertEqual(out["results"], [])

    def test_cli_force_and_dry_run_prints_summary(self) -> None:
        with patch.object(
            sync,
            "dispatch_incident_dashboard_action_sync",
            return_value={"error_count": 0, "enabled": True, "dry_run": True},
        ) as dispatch:
            code = sync.main(["--force", "--dry-run", "--limit", "7"])

        self.assertEqual(code, 0)
        dispatch.assert_called_once_with(limit=7, dry_run=True, force=True)


if __name__ == "__main__":
    unittest.main()
