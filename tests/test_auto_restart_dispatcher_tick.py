import io
import json
import os
import unittest
import urllib.error
from unittest import mock

import auto_restart_dispatcher_tick as tick


class AutoRestartDispatcherTickTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_builds_canonical_tick_route(self) -> None:
        url = tick.build_auto_restart_tick_url("https://dashboard.example.com")
        self.assertEqual(url, "https://dashboard.example.com/api/instagram-dashboard/auto-restart/tick")

    def test_should_run_at_most_once_per_minute(self) -> None:
        self.assertFalse(
            tick.should_run_auto_restart_tick(
                last_tick_monotonic=100.0,
                now_monotonic=159.0,
            )
        )
        self.assertTrue(
            tick.should_run_auto_restart_tick(
                last_tick_monotonic=100.0,
                now_monotonic=160.0,
            )
        )

    def test_missing_token_skips_without_http(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "INSTAGRAM_DASHBOARD_API_BASE_URL": "https://dashboard.example.com",
            },
            clear=False,
        ):
            with mock.patch("auto_restart_dispatcher_tick.urllib.request.urlopen") as urlopen:
                result = tick.run_auto_restart_dispatcher_tick(worker_id="run-dispatcher:host-a")
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "tick_token_not_configured")
        urlopen.assert_not_called()

    def test_missing_worker_id_skips_without_http(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "INSTAGRAM_DASHBOARD_API_BASE_URL": "https://dashboard.example.com",
                "INSTAGRAM_AUTO_RESTART_TICK_TOKEN": "secret-token-value",
            },
            clear=False,
        ):
            with mock.patch("auto_restart_dispatcher_tick.urllib.request.urlopen") as urlopen:
                result = tick.run_auto_restart_dispatcher_tick(worker_id="")
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "worker_id_not_configured")
        urlopen.assert_not_called()

    def test_missing_backend_url_skips_without_http(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "INSTAGRAM_AUTO_RESTART_TICK_TOKEN": "secret-token-value",
            },
            clear=False,
        ):
            with mock.patch("auto_restart_dispatcher_tick.urllib.request.urlopen") as urlopen:
                result = tick.run_auto_restart_dispatcher_tick(worker_id="run-dispatcher:host-a")
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "tick_url_not_configured")
        urlopen.assert_not_called()

    def test_unreliable_dispatcher_skips_without_http(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "INSTAGRAM_DASHBOARD_API_BASE_URL": "https://dashboard.example.com",
                "INSTAGRAM_AUTO_RESTART_TICK_TOKEN": "secret-token-value",
            },
            clear=False,
        ):
            with mock.patch("auto_restart_dispatcher_tick.urllib.request.urlopen") as urlopen:
                result = tick.run_auto_restart_dispatcher_tick(
                    worker_id="run-dispatcher:host-a",
                    dispatcher_reliable=False,
                )
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("reason"), "dispatcher_unreliable")
        urlopen.assert_not_called()

    def test_request_uses_required_headers_and_minimal_body(self) -> None:
        request = tick.build_auto_restart_tick_request(
            worker_id="run-dispatcher:host-a",
            token="secret-token-value",
            url="https://dashboard.example.com/api/instagram-dashboard/auto-restart/tick",
            dry_run=False,
        )
        self.assertEqual(request.get_method(), "POST")
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["x-instagram-auto-restart-tick-token"], "secret-token-value")
        self.assertEqual(headers["x-run-control-worker-id"], "run-dispatcher:host-a")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body, {"dry_run": False})

    def test_extract_tick_result_payload_reads_nested_data(self) -> None:
        nested = {"ok": True, "data": {"enqueued_count": 2, "scanned_candidates": 5}}
        payload = tick.extract_tick_result_payload(nested)
        self.assertEqual(payload.get("enqueued_count"), 2)
        self.assertEqual(payload.get("scanned_candidates"), 5)

    def test_summarize_tick_metrics_maps_backend_fields(self) -> None:
        metrics = tick.summarize_tick_metrics(
            {
                "skipped": False,
                "reason": None,
                "deduplicated_count": 1,
                "scanned_candidates": 4,
                "eligible_candidates": 2,
                "blocked_count": 1,
                "enqueued_count": 1,
            }
        )
        self.assertEqual(metrics["evaluated_count"], 4)
        self.assertEqual(metrics["eligible_count"], 2)
        self.assertEqual(metrics["enqueued_count"], 1)

    def test_nested_success_response_logs_business_metrics(self) -> None:
        response = mock.Mock()
        response.status = 200
        response.read.return_value = json.dumps(
            {
                "ok": True,
                "data": {
                    "skipped": False,
                    "deduplicated_count": 0,
                    "scanned_candidates": 3,
                    "eligible_candidates": 1,
                    "blocked_count": 2,
                    "enqueued_count": 1,
                },
            }
        ).encode("utf-8")
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)

        with mock.patch.dict(
            os.environ,
            {
                "INSTAGRAM_DASHBOARD_API_BASE_URL": "https://dashboard.example.com",
                "INSTAGRAM_AUTO_RESTART_TICK_TOKEN": "secret-token-value",
            },
            clear=False,
        ):
            with mock.patch("auto_restart_dispatcher_tick.urllib.request.urlopen", return_value=response):
                with mock.patch("auto_restart_dispatcher_tick.log") as log_mock:
                    tick.run_auto_restart_dispatcher_tick(worker_id="run-dispatcher:host-a")

        completed = [call for call in log_mock.call_args_list if call.kwargs.get("enqueued_count") is not None]
        self.assertTrue(completed)
        last = completed[-1].kwargs
        self.assertEqual(last.get("enqueued_count"), 1)
        self.assertEqual(last.get("evaluated_count"), 3)
        self.assertNotIn("secret-token-value", str(last))

    def test_response_without_data_remains_safe(self) -> None:
        payload = tick.extract_tick_result_payload({"ok": True})
        metrics = tick.summarize_tick_metrics(payload)
        self.assertIsNone(metrics.get("enqueued_count"))

    def test_successful_tick_posts_to_backend(self) -> None:
        response = mock.Mock()
        response.status = 200
        response.read.return_value = b'{"ok":true,"enqueued_count":0}'
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=False)

        with mock.patch.dict(
            os.environ,
            {
                "INSTAGRAM_DASHBOARD_API_BASE_URL": "https://dashboard.example.com",
                "INSTAGRAM_AUTO_RESTART_TICK_TOKEN": "secret-token-value",
            },
            clear=False,
        ):
            with mock.patch("auto_restart_dispatcher_tick.urllib.request.urlopen", return_value=response) as urlopen:
                result = tick.run_auto_restart_dispatcher_tick(worker_id="run-dispatcher:host-a")

        self.assertTrue(result.get("ok"))
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://dashboard.example.com/api/instagram-dashboard/auto-restart/tick",
        )

    def test_http_errors_do_not_raise(self) -> None:
        error = urllib.error.HTTPError(
            url="https://dashboard.example.com/api/instagram-dashboard/auto-restart/tick",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"reason":"forbidden","token":"secret-token-value"}'),
        )

        with mock.patch.dict(
            os.environ,
            {
                "INSTAGRAM_DASHBOARD_API_BASE_URL": "https://dashboard.example.com",
                "INSTAGRAM_AUTO_RESTART_TICK_TOKEN": "secret-token-value",
            },
            clear=False,
        ):
            with mock.patch("auto_restart_dispatcher_tick.urllib.request.urlopen", side_effect=error):
                result = tick.run_auto_restart_dispatcher_tick(worker_id="run-dispatcher:host-a")

        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("status"), 403)
        self.assertNotIn("secret-token-value", str(result.get("error") or ""))

    def test_token_is_redacted_from_logs(self) -> None:
        with mock.patch("auto_restart_dispatcher_tick.log") as log_mock:
            tick.run_auto_restart_dispatcher_tick(
                worker_id="run-dispatcher:host-a",
                token="secret-token-value",
                base_url="https://dashboard.example.com",
                urlopen=mock.Mock(side_effect=RuntimeError("secret-token-value leaked")),
            )
        joined = " ".join(str(call.kwargs.get("error") or "") for call in log_mock.call_args_list)
        self.assertNotIn("secret-token-value", joined)

    def test_no_local_enqueue_or_adb_side_effects(self) -> None:
        source_path = tick.__file__
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read().lower()
        self.assertNotIn("account_run_requests", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("adb", source)
        self.assertNotIn("runner.py", source)


if __name__ == "__main__":
    unittest.main()
