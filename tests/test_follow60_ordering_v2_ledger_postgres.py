from __future__ import annotations

import json
import os
import subprocess
import threading
import unittest
from pathlib import Path


DATABASE_URL = os.environ.get("FOLLOW60_ORDERING_V2_TEST_DATABASE_URL", "")
FIXTURE = Path(__file__).with_name("fixtures") / "follow60_ordering_v2_ledger_v1.sql"


@unittest.skipUnless(DATABASE_URL, "set FOLLOW60_ORDERING_V2_TEST_DATABASE_URL for PostgreSQL replay")
class Follow60OrderingV2LedgerPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(
            ["psql", DATABASE_URL, "-v", "ON_ERROR_STOP=1", "-f", str(FIXTURE)],
            check=True,
            text=True,
            capture_output=True,
        )

    def setUp(self) -> None:
        self.sql("truncate follow60_ordering_v2_test.outbox, follow60_ordering_v2_test.receipts, follow60_ordering_v2_test.ledger restart identity cascade")

    def sql(self, statement: str) -> str:
        result = subprocess.run(
            ["psql", DATABASE_URL, "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", statement],
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout.strip()

    def apply(self, stage: str, payload: dict | None = None, *, action: str = "action-a") -> dict:
        value = json.dumps(payload or {"verified": True}, sort_keys=True).replace("'", "''")
        out = self.sql(
            "select follow60_ordering_v2_test.apply_receipt("
            f"'account-a','run-a','request-a','session-a','target-a','{action}',"
            f"'alice','FOLLOW60_ORDERING_V2','{stage}','{value}'::jsonb)::text"
        )
        return json.loads(out)

    def prepare_like(self, *, skipped: bool = False) -> None:
        self.apply("profile_certified")
        self.apply("post_opened")
        self.apply("like_skipped" if skipped else "like_verified")

    def prepare_follow(self, *, skipped: bool = False) -> None:
        self.prepare_like(skipped=skipped)
        self.apply("profile_reentry_verified")
        self.apply("follow_pending")

    def test_ten_required_replay_scenarios(self) -> None:
        # 1. Like verified -> Follow verified.
        self.prepare_follow()
        self.assertTrue(self.apply("follow_verified")["follow_verified"])
        self.apply("mute_posts_verified")
        self.apply("mute_stories_verified")
        returned = self.apply("return_ct_exact")
        self.assertFalse(returned["cycle_complete"])
        self.assertTrue(self.apply("cycle_complete")["cycle_complete"])

        # 2. Like verified -> Follow failed preserves Like and never completes.
        self.setUp()
        self.prepare_follow()
        failed = self.apply("follow_failed", {"reason": "verify_failed"})
        self.assertTrue(failed["like_verified"])
        self.assertFalse(failed["cycle_complete"])

        # 3. Like verified -> Stop before Follow; replay starts at profile reentry.
        self.setUp()
        self.prepare_like()
        stopped = self.apply("stop_recorded", {"source": "operator"})
        self.assertTrue(stopped["stop_recorded"])
        ledger_id = stopped["ledger_id"]
        self.assertEqual("profile_reentry_verified", self.sql(f"select follow60_ordering_v2_test.next_stage({ledger_id})"))

        # 4. Like skipped -> Follow verified.
        self.setUp()
        self.prepare_follow(skipped=True)
        skipped = self.apply("follow_verified")
        self.assertTrue(skipped["like_skipped"])

        # 5. V5 reject is an explicit Like skip, never a verified Like.
        self.setUp()
        self.apply("profile_certified")
        self.apply("post_opened")
        rejected = self.apply("like_skipped", {"reason": "v5_rejected"})
        self.assertTrue(rejected["like_skipped"])
        self.assertFalse(rejected["like_verified"])

        # 6. Replay Like receipt is a duplicate no-op.
        duplicate = self.apply("like_skipped", {"reason": "v5_rejected"})
        self.assertTrue(duplicate["duplicate"])

        # 7. Replay Follow is a duplicate no-op.
        self.setUp()
        self.prepare_follow()
        self.apply("follow_verified")
        self.assertTrue(self.apply("follow_verified")["duplicate"])

        # 8. Crash after journal before ACK: pending survives; replay does not insert.
        self.setUp()
        self.prepare_like()
        self.assertEqual("3", self.sql("select count(*) from follow60_ordering_v2_test.outbox where status='pending'"))
        self.assertTrue(self.apply("like_verified")["duplicate"])

        # 9. Outbox flush is idempotent.
        receipt_id = int(self.sql("select receipt_id from follow60_ordering_v2_test.receipts where action_type='like_verified'"))
        first_ack = json.loads(self.sql(f"select follow60_ordering_v2_test.ack_outbox({receipt_id})::text"))
        second_ack = json.loads(self.sql(f"select follow60_ordering_v2_test.ack_outbox({receipt_id})::text"))
        self.assertFalse(first_ack["duplicate"])
        self.assertTrue(second_ack["duplicate"])

        # 10. Concurrent duplicate ACK inserts exactly one receipt/outbox row.
        self.setUp()
        self.apply("profile_certified")
        self.apply("post_opened")
        outputs: list[dict] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                outputs.append(self.apply("like_verified"))
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual([False, True], sorted(result["duplicate"] for result in outputs))
        self.assertEqual("1", self.sql("select count(*) from follow60_ordering_v2_test.receipts where action_type='like_verified'"))
        self.assertEqual("1", self.sql("select count(*) from follow60_ordering_v2_test.outbox o join follow60_ordering_v2_test.receipts r using(receipt_id) where r.action_type='like_verified'"))

    def test_scope_isolation_and_fail_closed_transitions(self) -> None:
        self.apply("profile_certified", action="action-a")
        self.apply("profile_certified", action="action-b")
        self.assertEqual("2", self.sql("select count(*) from follow60_ordering_v2_test.ledger"))
        with self.assertRaises(subprocess.CalledProcessError):
            self.apply("follow_verified", action="action-c")

    def test_partial_mute_can_never_complete_cycle(self) -> None:
        self.prepare_follow()
        self.apply("follow_verified")
        self.apply("mute_posts_verified")
        with self.assertRaises(subprocess.CalledProcessError):
            self.apply("return_ct_exact")
        with self.assertRaises(subprocess.CalledProcessError):
            self.apply("cycle_complete")


if __name__ == "__main__":
    unittest.main()
