import ast
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib import error

from ct_target_evaluation_evidence import EvaluationEvidenceWriter, TargetEvaluationCapture
import supabase_client


ACCOUNT = "10000000-0000-4000-8000-000000000001"
TARGET = "10000000-0000-4000-8000-000000000002"
RUN = "10000000-0000-4000-8000-000000000003"
WORKER = "a" * 40


def capture(submit, **scope):
    return TargetEvaluationCapture(**{
        "account_id": ACCOUNT,
        "target_id": TARGET,
        "run_id": RUN,
        "worker_sha": WORKER,
        "source_username": "target",
        "submit": submit,
        **scope,
    })


def observe(collector, *, outcome="eligible", candidate="person", observed="person"):
    return collector.observe(candidate_username=candidate, observed_username=observed, outcome=outcome)


def runner_capture_block(marker, outcome):
    """Exercise the actual runner branch body with previously observed screen evidence."""
    path = Path(__file__).resolve().parents[1] / "runner.py"
    source = path.read_text()
    target = next(n for n in ast.parse(source).body
                  if isinstance(n, ast.FunctionDef) and n.name == "_run_followers_list_engine_session")
    block = next(n for n in ast.walk(target) if isinstance(n, ast.If)
                 and ast.unparse(n.test) == marker
                 and any(f"outcome={outcome!r}" in ast.unparse(c) for c in n.body))
    return compile(ast.Module(body=[block], type_ignores=[]), str(path), "exec")


class TargetEvaluationEvidenceTest(unittest.TestCase):
    def test_372_and_446_share_the_confirmed_profile_hooks(self):
        eligible = runner_capture_block("_ct_evaluation_capture is not None", "eligible")
        private = runner_capture_block("_ct_evaluation_capture is not None and _pre_follow_private_detected", "private")
        for version, observations in (
            (372, {"_g_final": {}, "_ct_open_observed_username": "person"}),
            (446, {"_g_final": {"action_bar_title": "person"}, "_ct_open_observed_username": ""}),
        ):
            for outcome, code in (("eligible", eligible), ("private", private)):
                with self.subTest(version=version, outcome=outcome):
                    sent = []
                    local = {
                        "_ct_evaluation_capture": capture(lambda row: sent.append(row) or True),
                        "_pre_follow_private_detected": outcome == "private",
                        "follower_un": "@Person",
                        **observations,
                    }
                    exec(code, local)
                    self.assertEqual(len(sent), 1)
                    self.assertEqual(sent[0]["p_outcome"], outcome)
                    self.assertEqual(sent[0]["p_normalized_username"], "person")
                    self.assertEqual(sent[0]["p_source_run_id"], RUN)

    def test_missing_mismatched_and_synthetic_identities_do_not_count(self):
        sent = []
        collector = capture(lambda row: sent.append(row) or True)
        for candidate, observed in (
            ("person", ""), ("person", "someone_else"), ("target", "target"),
            ("__visual_candidate:42", "__visual_candidate:42"),
        ):
            with self.subTest(candidate=candidate, observed=observed):
                self.assertFalse(observe(collector, candidate=candidate, observed=observed))
        self.assertFalse(observe(collector, outcome="provider_error"))
        self.assertEqual(sent, [])

    def test_invalid_scope_and_unattested_worker_are_refused(self):
        for field in ("account_id", "target_id", "run_id", "worker_sha"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                capture(lambda row: True, **{field: ""})

    def test_replay_key_binds_account_target_run_and_username(self):
        sent = []
        sink = lambda row: sent.append(row) or True
        first = capture(sink)
        self.assertTrue(observe(first, candidate="@Person"))
        self.assertFalse(observe(first, candidate="person", outcome="private"))
        self.assertTrue(observe(capture(sink)))
        self.assertTrue(observe(capture(sink, run_id=TARGET)))
        self.assertTrue(observe(capture(sink, target_id=RUN)))
        self.assertTrue(observe(capture(sink, account_id=RUN)))
        keys = [row["p_idempotency_key"] for row in sent]
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(len(set(keys)), 4)

    def test_queue_refusal_can_be_retried_without_counting_the_same_profile_twice(self):
        sent = []

        def submit(row):
            sent.append(row)
            return len(sent) > 1

        collector = capture(submit)
        self.assertFalse(observe(collector))
        self.assertTrue(observe(collector))
        self.assertFalse(observe(collector))
        self.assertEqual(sent[0]["p_idempotency_key"], sent[1]["p_idempotency_key"])

    def test_writer_is_bounded_and_a_failed_write_does_not_block_future_evidence(self):
        attempted = []
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()

        def send(row):
            attempted.append(row)
            if row == "first":
                entered.set()
                release.wait(2)
                raise TimeoutError()
            completed.set()

        writer = EvaluationEvidenceWriter(send, capacity=1)
        self.assertIsNone(writer._thread)
        try:
            with self.assertLogs("ct_target_evaluation_evidence", level="WARNING"):
                self.assertTrue(writer.submit("first"))
                self.assertTrue(entered.wait(1))
                self.assertTrue(writer.submit("second"))
                self.assertFalse(writer.submit("rejected"))
                release.set()
                self.assertTrue(completed.wait(1))
        finally:
            release.set()
        self.assertEqual(attempted, ["first", "second"])

    def test_rpc_retries_the_exact_payload_on_lost_response(self):
        sent = []
        observe(capture(lambda row: sent.append(row) or True))
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"eventId":"synthetic-replayed-event","eventCreated":false}'

        def urlopen(req, *, timeout):
            requests.append((req, timeout))
            if len(requests) == 1:
                raise error.URLError(TimeoutError())
            return Response()

        with (
            patch.object(supabase_client, "_base_url", return_value="https://unused.invalid"),
            patch.object(supabase_client, "_service_key", return_value="fixture"),
            patch.object(supabase_client.request, "urlopen", side_effect=urlopen),
            patch.object(supabase_client.time, "sleep"),
        ):
            supabase_client._write_target_evaluation_evidence(sent[0])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0][0].data, requests[1][0].data)
        self.assertEqual(json.loads(requests[1][0].data), sent[0])
        self.assertEqual(requests[1][1], 2.0)

    def test_rpc_rejects_confirmation_without_an_event_receipt(self):
        with patch.object(supabase_client, "_call_rpc", return_value={"ok": True}):
            with self.assertRaisesRegex(ValueError, "receipt_invalid"):
                supabase_client._write_target_evaluation_evidence({})


if __name__ == "__main__":
    unittest.main()
