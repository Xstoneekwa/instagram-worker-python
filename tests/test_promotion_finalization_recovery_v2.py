import tempfile
from pathlib import Path
import unittest

from test_promotion_transaction_v1 import FakeAdapter, plan, NEW_SHA
from promotion_transaction_v1 import DurablePromotion, PromotionFailure, SimulatedHardCrash


class PhysicalAdapter(FakeAdapter):
    def finish_transaction(self, value, *, promoted, evidence):
        if self.finished is None and not self.fence:
            raise AssertionError("admission opened before finish")
        return super().finish_transaction(value, promoted=promoted, evidence=evidence)

    def quiesce(self, service, value):
        if not self.fence:
            raise AssertionError("quiesce without admission fence")
        return super().quiesce(service, value)


class FinalizationV2Tests(unittest.TestCase):
    def machine(self, adapter):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return DurablePromotion(Path(directory.name) / 'journal.json', plan(), adapter)

    def test_finish_precedes_release(self):
        a = PhysicalAdapter()
        result = self.machine(a).run()
        self.assertEqual(result['status'], 'PROMOTED')
        self.assertLess(a.hits.index('finish:after'), a.hits.index('release_fence'))

    def test_persistent_finish_failure_keeps_fence_and_new(self):
        class BrokenFinish(PhysicalAdapter):
            def finish_transaction(self, *args, **kwargs):
                raise PromotionFailure('finish_unavailable')
        a = BrokenFinish(); m = self.machine(a)
        result = m.run()
        self.assertEqual(result['status'], 'COMMITTED_FINALIZATION_REQUIRED')
        self.assertTrue(a.fence)
        self.assertTrue(result['admission_fence_active'])
        self.assertEqual(a.production, NEW_SHA)

    def test_protected_recovery_is_not_noop(self):
        a = PhysicalAdapter(failures={'start:new:heartbeat', 'start:old:notifier'})
        m = self.machine(a)
        self.assertEqual(m.run()['status'], 'PROTECTED_RECOVERY_REQUIRED')
        self.assertTrue(a.fence)
        self.assertEqual(m.recover()['status'], 'ROLLED_BACK')
        self.assertFalse(a.fence)

    def test_hard_crashes_at_finish_and_unfence_recover_both_directions(self):
        for promoted in (True, False):
            for crash in ('finish', 'finish:after', 'release_fence', 'release_fence:after', 'final_receipt'):
                with self.subTest(promoted=promoted, crash=crash):
                    a = PhysicalAdapter(failures=set() if promoted else {'start:new:heartbeat'}, crashes={crash})
                    m = self.machine(a)
                    with self.assertRaises(SimulatedHardCrash):
                        m.run()
                    result = m.recover()
                    self.assertEqual(result['status'], 'PROMOTED' if promoted else 'ROLLED_BACK')
                    self.assertFalse(a.fence)

    def test_release_failure_preserves_committed_authority_then_recovers(self):
        class BrokenRelease(PhysicalAdapter):
            broken = True
            def release_fence(self, value):
                if self.broken:
                    raise PromotionFailure('release_unavailable')
                return super().release_fence(value)
        a = BrokenRelease(); m = self.machine(a)
        self.assertEqual(m.run()['status'], 'COMMITTED_FINALIZATION_REQUIRED')
        self.assertEqual(a.production, NEW_SHA)
        self.assertTrue(a.fence)
        a.broken = False
        self.assertEqual(m.recover()['status'], 'PROMOTED')

    def test_drain_timeout_does_not_quiesce_running_work(self):
        class Busy(PhysicalAdapter):
            def drain_naturally(self, value):
                raise PromotionFailure('natural_drain_timeout')
            def zero_gate(self, value):
                return {"ok": False, "reason": "active_runtime_work_present"}
        a = Busy(); m = self.machine(a)
        self.assertEqual(m.run()['status'], 'PROTECTED_RECOVERY_REQUIRED')
        self.assertTrue(a.fence)
        self.assertFalse(any(x.startswith('quiesce:') for x in a.hits))
        self.assertEqual(set(a.services.values()), {'/releases/old'})
