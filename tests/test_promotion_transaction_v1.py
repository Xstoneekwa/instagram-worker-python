from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from promotion_transaction_v1 import (
    DurablePromotion,
    PromotionFailure,
    PromotionPlan,
    SERVICES,
    SimulatedHardCrash,
    read_journal,
)


OLD_SHA = "a" * 40
NEW_SHA = "b" * 40


def plan() -> PromotionPlan:
    return PromotionPlan(
        transaction_id="worker-promotion:test-transaction-0001",
        candidate_sha=NEW_SHA,
        candidate_tree="c" * 40,
        candidate_release="/releases/new",
        old_active_sha=OLD_SHA,
        old_active_release="/releases/old",
        previous_rollback_sha="d" * 40,
        previous_rollback_release="/releases/previous-rollback",
        controller_identity={"path": "/releases/controller/phonefarm_runtime_control.py", "release_root": "/releases/controller", "sha256": "e" * 64},
        launchd_identity={"uid": 501, "gid": 20, "user": "operator", "home": "/Users/operator", "launchd_domain": "gui/501", "labels": {service: f"test.{service}" for service in SERVICES}},
        registry_pre_state={
            "registry_path": "/registry.json",
            "registry_sha256": "f" * 64,
            "worker_component": {"production": {"sha": OLD_SHA, "runtime_root": "/releases/old"}, "rollback": {"sha": "d" * 40}},
            "worker_production": {"sha": OLD_SHA, "runtime_root": "/releases/old"},
        },
        runtime_pointer_pre_state="/releases/old",
    )


class FakeAdapter:
    def __init__(self, failures=None, crashes=None):
        self.failures = set(failures or ())
        self.crashes = set(crashes or ())
        self.hits = []
        self.fence = False
        self.pointer = "/releases/old"
        self.services = {service: "/releases/old" for service in SERVICES}
        self.production = OLD_SHA
        self.rollback = "d" * 40
        self.token_counter = 0
        self.finished = None
        self.projection = None
        self.new_created = 0
        self.new_claimed = 0

    def _hit(self, name):
        self.hits.append(name)
        if name in self.crashes:
            self.crashes.remove(name)
            raise SimulatedHardCrash(name)
        if name in self.failures:
            self.failures.remove(name)
            raise PromotionFailure(name)

    def acquire_transaction(self, value):
        self._hit("acquire_transaction")
        return {"ok": True}

    def acquire_fence(self, value):
        self._hit("acquire_fence")
        self.fence = True
        return {"ok": True, "active": True, "persistent": True}

    def assert_fence(self, value):
        self._hit("assert_fence")
        return {"ok": self.fence, "active": self.fence, "persistent": self.fence, "reason": None if self.fence else "missing"}

    def fence_state(self, value):
        terminal = None if self.fence else ("PROMOTED" if self.production == NEW_SHA else "ROLLED_BACK")
        return {"ok": True, "active": self.fence, "persistent": self.fence, "terminal_state": terminal}

    def drain_naturally(self, value):
        self._hit("drain")
        return {"ok": True, "counts": {"requests": 0, "runs": 0, "devices": 0, "ticks": 0}}

    def zero_gate(self, value):
        self._hit("zero_gate")
        return {"ok": True, "counts": {"requests": 0, "runs": 0, "devices": 0, "ticks": 0}}

    def verify_and_pin(self, value):
        self._hit("verify_and_pin")
        return {"ok": True}

    def quiesce(self, service, value):
        self._hit(f"quiesce:{service}")
        self.services[service] = None
        return {"ok": True, "service": service}

    def switch_pointer(self, release, value):
        self._hit(f"switch:{'new' if release.endswith('new') else 'old'}")
        self.pointer = release
        return {"ok": True, "runtime_root": release}

    def prepare_token(self, *, phase, release_sha, plan):
        self._hit(f"token:{phase.lower()}")
        self.token_counter += 1
        return {
            "ok": True,
            "token_id": f"token-{self.token_counter}",
            "binding": {
                "transaction_id": plan.transaction_id,
                "phase": phase,
                "candidate_sha": release_sha,
                "dispatcher_module_root": plan.candidate_release if phase == "PROMOTE" else plan.old_active_release,
            },
        }

    def start(self, service, release, value):
        side = "new" if release.endswith("new") else "old"
        self._hit(f"start:{side}:{service}")
        self.services[service] = release
        return {"ok": True, "service": service}

    def verify_services_and_token(self, release, release_sha, token, value):
        side = "new" if release.endswith("new") else "old"
        self._hit(f"verify:{side}")
        ok = self.pointer == release and all(root == release for root in self.services.values())
        phase = "PROMOTE" if side == "new" else "ROLLBACK"
        return {
            "ok": ok,
            "active_root": release,
            "active_sha": release_sha,
            "services": {
                service: {
                    "ok": self.services[service] == release,
                    "process_count": 1,
                    "processes": [{"module_root": release, "runtime_sha": release_sha, "pid": index + 100}],
                }
                for index, service in enumerate(SERVICES)
            },
            "startupTokenGate": {
                "ok": token["binding"]["phase"] == phase,
                "token_id": token["token_id"],
                "binding": token["binding"],
            },
        }

    def archive_token_evidence(self, token, value):
        self._hit(f"archive:{token['binding']['phase'].lower()}")
        return {"ok": True, "token_id": token["token_id"], "binding": token["binding"]}

    def publish_generation(self, value):
        self._hit("commit:before")
        self.production = NEW_SHA
        self.rollback = OLD_SHA
        self._hit("commit:after")
        return {"ok": True, "active": NEW_SHA, "rollback": OLD_SHA}

    def committed_generation_state(self, value):
        committed = self.production == NEW_SHA and self.rollback == OLD_SHA
        old = self.production == OLD_SHA and self.rollback == "d" * 40
        return {"ok": committed or old, "committed": committed, "old": old}

    def reconcile_projections(self, value, *, promoted):
        self._hit("reconcile")
        self.projection = "new" if promoted else "old"
        return {"ok": True}

    def restore_precommit_generation(self, value):
        self._hit("restore_registry")
        return {"ok": self.production == OLD_SHA and self.rollback == "d" * 40}

    def verify_terminal(self, value, *, promoted, token):
        side = "new" if promoted else "old"
        self._hit(f"terminal:{side}")
        release = value.candidate_release if promoted else value.old_active_release
        sha = value.candidate_sha if promoted else value.old_active_sha
        services = self.verify_services_and_token(release, sha, token, value)
        return {"ok": services["ok"], "status": "NEW_RELEASE_FULLY_ACTIVE" if promoted else "OLD_RELEASE_FULLY_ACTIVE", "runtime_root": release, "release_sha": sha, "services": services}

    def release_fence(self, value):
        self._hit("release_fence")
        self.fence = False
        terminal = "PROMOTED" if self.production == NEW_SHA else "ROLLED_BACK"
        self._hit("release_fence:after")
        return {"ok": True, "active": False, "terminal_state": terminal}

    def finish_transaction(self, value, *, promoted, evidence):
        self._hit("finish")
        self.finished = "PROMOTED" if promoted else "ROLLED_BACK"
        self._hit("finish:after")
        return {"ok": True}

    def complete_finalization(self, value, *, promoted, evidence):
        self._hit("final_receipt")
        return {"ok": self.finished is not None and not self.fence}

    def attempt_new_work(self):
        if self.fence:
            return False
        self.new_created += 1
        self.new_claimed += 1
        return True


class DurablePromotionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "transaction.json"

    def machine(self, adapter):
        return DurablePromotion(self.path, plan(), adapter)

    def assert_old_terminal(self, adapter):
        self.assertEqual(adapter.pointer, "/releases/old")
        self.assertEqual(set(adapter.services.values()), {"/releases/old"})
        self.assertEqual(adapter.production, OLD_SHA)
        self.assertEqual(adapter.rollback, "d" * 40)
        self.assertFalse(adapter.fence)
        self.assertEqual(read_journal(self.path)["phase"], "ROLLED_BACK")

    def assert_new_terminal(self, adapter):
        self.assertEqual(adapter.pointer, "/releases/new")
        self.assertEqual(set(adapter.services.values()), {"/releases/new"})
        self.assertEqual(adapter.production, NEW_SHA)
        self.assertEqual(adapter.rollback, OLD_SHA)
        self.assertFalse(adapter.fence)
        self.assertEqual(read_journal(self.path)["phase"], "PROMOTED")

    def test_success_has_one_commit_point_and_new_terminal_state(self):
        adapter = FakeAdapter()
        result = self.machine(adapter).run()
        self.assertEqual(result["status"], "PROMOTED")
        self.assert_new_terminal(adapter)

    def test_all_recoverable_precommit_failures_restore_old(self):
        points = [
            "verify_and_pin",
            "quiesce:heartbeat",
            "switch:new",
            "token:promote",
            "start:new:heartbeat",
            "start:new:notifier",
            "verify:new",
            "archive:promote",
            "commit:before",
        ]
        for point in points:
            with self.subTest(point=point):
                path = Path(self.tmp.name) / f"{point.replace(':', '-')}.json"
                adapter = FakeAdapter(failures={point})
                result = DurablePromotion(path, plan(), adapter).run()
                self.assertEqual(result["status"], "ROLLED_BACK")
                self.path = path
                self.assert_old_terminal(adapter)

    def test_failure_after_atomic_commit_finalizes_new(self):
        adapter = FakeAdapter(failures={"commit:after"})
        result = self.machine(adapter).run()
        self.assertEqual(result["status"], "PROMOTED")
        self.assert_new_terminal(adapter)

    def test_postcommit_projection_failure_stays_protected_then_recovers_new(self):
        adapter = FakeAdapter(failures={"reconcile"})
        result = self.machine(adapter).run()
        self.assertEqual(result["status"], "PROMOTED")
        self.assert_new_terminal(adapter)

    def test_postcommit_finish_failure_recovers_idempotently(self):
        adapter = FakeAdapter(failures={"finish"})
        result = self.machine(adapter).run()
        self.assertEqual(result["status"], "PROMOTED")
        self.assert_new_terminal(adapter)

    def test_rollback_failures_leave_fence_and_open_recovery(self):
        rollback_points = [
            "start:old:dispatcher",
            "start:old:heartbeat",
            "start:old:notifier",
            "verify:old",
        ]
        for point in rollback_points:
            with self.subTest(point=point):
                path = Path(self.tmp.name) / f"double-{point.replace(':', '-')}.json"
                adapter = FakeAdapter(failures={"start:new:dispatcher", point})
                result = DurablePromotion(path, plan(), adapter).run()
                self.assertEqual(result["status"], "PROTECTED_RECOVERY_REQUIRED")
                self.assertTrue(adapter.fence)
                self.assertEqual(read_journal(path)["phase"], "PROTECTED_RECOVERY_REQUIRED")

    def test_rollback_uses_distinct_token(self):
        adapter = FakeAdapter(failures={"verify:new"})
        self.machine(adapter).run()
        state = read_journal(self.path)
        self.assertNotEqual(state["startup_tokens"]["PROMOTE"]["token_id"], state["startup_tokens"]["ROLLBACK"]["token_id"])
        self.assertEqual(state["startup_tokens"]["ROLLBACK"]["binding"]["phase"], "ROLLBACK")

    def test_fence_blocks_concurrent_creation_and_claim_for_long_transaction(self):
        adapter = FakeAdapter(crashes={"drain"})
        with self.assertRaises(SimulatedHardCrash):
            self.machine(adapter).run()
        for _ in range(5000):
            self.assertFalse(adapter.attempt_new_work())
        self.assertEqual(adapter.new_created, 0)
        self.assertEqual(adapter.new_claimed, 0)
        self.assertTrue(adapter.fence)

    def test_hard_crash_recovery_at_every_operation(self):
        precommit = [
            "drain", "zero_gate", "verify_and_pin", "quiesce:dispatcher",
            "quiesce:heartbeat", "quiesce:notifier", "switch:new", "token:promote",
            "start:new:dispatcher", "start:new:heartbeat", "start:new:notifier",
            "verify:new", "archive:promote", "commit:before",
        ]
        postcommit = ["commit:after", "reconcile", "terminal:new", "release_fence", "release_fence:after", "finish", "finish:after"]
        for point in precommit + postcommit:
            with self.subTest(point=point):
                path = Path(self.tmp.name) / f"crash-{point.replace(':', '-')}.json"
                adapter = FakeAdapter(crashes={point})
                with self.assertRaises(SimulatedHardCrash):
                    DurablePromotion(path, plan(), adapter).run()
                result = DurablePromotion(path, plan(), adapter).recover()
                if point in postcommit:
                    self.assertEqual(result["status"], "PROMOTED")
                    self.path = path
                    self.assert_new_terminal(adapter)
                else:
                    self.assertEqual(result["status"], "ROLLED_BACK")
                    self.path = path
                    self.assert_old_terminal(adapter)

    def test_crash_after_rollback_finish_recovers_old_idempotently(self):
        adapter = FakeAdapter(failures={"verify:new"}, crashes={"finish:after"})
        with self.assertRaises(SimulatedHardCrash):
            self.machine(adapter).run()
        result = self.machine(adapter).recover()
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assert_old_terminal(adapter)

    def test_hash_chain_detects_tampering(self):
        adapter = FakeAdapter()
        self.machine(adapter).run()
        value = json.loads(self.path.read_text())
        value["history"][2]["phase"] = "PROMOTED"
        self.path.write_text(json.dumps(value))
        with self.assertRaises(PromotionFailure):
            read_journal(self.path)


if __name__ == "__main__":
    unittest.main()
