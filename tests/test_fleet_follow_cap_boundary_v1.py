import unittest
from fleet_runtime_barrier import FleetBlocked, FleetRuntimeBarrier, follow_allowance


class FollowCapTests(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.b = FleetRuntimeBarrier('tenant', 'account', self.saved.append)
        self.b.register_owner('runner')

    def cap(self, value=60):
        self.b.apply('cap', expected_revision=0, operation='cap', cap=value, package_max=120)

    def boundary(self, **kw):
        return self.b.safe_follow_boundary('runner', self.b.snapshot()['revision'],
            normal_cap=120, canonical_consumed=52, **kw)

    def test_normal_cap(self): self.assertEqual(follow_allowance(120, None, 52), (120, 68))
    def test_manual_preserved(self): self.assertEqual(follow_allowance(20, 60, 3), (20, 17))
    def test_lower_cap(self): self.assertEqual(follow_allowance(120, 60, 52), (60, 8))
    def test_below_consumed(self): self.assertEqual(follow_allowance(120, 60, 65), (60, 0))
    def test_zero_is_real(self): self.assertEqual(follow_allowance(120, 0, 0), (0, 0))
    def test_invalid_counter(self):
        with self.assertRaises(FleetBlocked): follow_allowance(120, 60, None)
    def test_boolean_cap_refused(self):
        with self.assertRaises(FleetBlocked): follow_allowance(120, True, 0)
    def test_above_package_refused(self):
        with self.assertRaises(FleetBlocked): self.cap(121)
    def test_composite_not_interrupted(self):
        self.cap()
        with self.assertRaises(FleetBlocked): self.boundary(composite_reconciled=False)
        self.assertEqual(self.b.snapshot()['cap_state'], 'CAP_REQUESTED')
    def test_safe_boundary_ack(self):
        self.cap(); self.assertEqual(self.boundary(composite_reconciled=True), (60, 8))
        self.assertEqual(self.b.snapshot()['cap_state'], 'CAP_ACTIVE')
    def test_wrong_boundary(self):
        self.cap()
        with self.assertRaises(FleetBlocked): self.boundary(composite_reconciled=True, boundary='mid_follow')
    def test_clear_restores_normal(self):
        self.cap(); self.b.apply('clear', expected_revision=1, operation='clear_cap')
        self.assertEqual(self.boundary(composite_reconciled=True), (120, 68))
    def test_all_owners_must_ack(self):
        self.b.register_owner('second'); self.cap(); self.boundary(composite_reconciled=True)
        self.assertEqual(self.b.snapshot()['cap_state'], 'CAP_REQUESTED')
    def test_no_policy_boundary_no_persistence(self):
        count = len(self.saved); self.boundary(composite_reconciled=True)
        self.assertEqual(len(self.saved), count)



class RunnerBoundaryTransportTests(unittest.TestCase):
    def setUp(self):
        from fleet_runtime_barrier import FleetRunnerBoundary
        self.frame = dict(schema='FLEET_RUNTIME_LOCAL_V1', account_id='account',
                          request_id='request', owner_boot_id='boot', business_day='2026-09-06',
                          revision=1, canonical_follows_before_run=0,
                          normal_effective_day_cap=120, emergency_cap=60)
        self.acks = []
        self.adapter = FleetRunnerBoundary(account_id='account', request_id='request',
            owner_boot_id='boot', read_local=lambda: dict(self.frame), write_ack=self.acks.append)

    def consume(self, count=52, **kwargs):
        return self.adapter.consume(session_follows=count, business_day='2026-09-06',
                                    composite_reconciled=kwargs.pop('reconciled', True), **kwargs)

    def test_52_of_60(self): self.assertEqual(self.consume()['remaining'], 8)
    def test_65_of_60(self): self.assertEqual(self.consume(65)['remaining'], 0)
    def test_manual_20(self):
        self.frame['normal_effective_day_cap'] = 20
        self.assertEqual(self.consume(10)['effective_day_cap'], 20)
    def test_warmup_20(self):
        self.frame['normal_effective_day_cap'] = 20
        self.assertEqual(self.consume(10)['remaining'], 10)
    def test_clear_restores_normal(self):
        self.consume(); self.frame.update(revision=2, emergency_cap=None)
        self.assertEqual(self.consume()['remaining'], 68)
    def test_composite_does_not_ack(self):
        with self.assertRaises(FleetBlocked): self.consume(reconciled=False)
        self.assertEqual(self.acks, [])
        self.frame.update(revision=2, emergency_cap=55)
        self.assertEqual(self.consume()['remaining'], 3)
        self.assertEqual(self.acks[-1]['revision'], 2)
    def test_stale_boot(self):
        self.frame['owner_boot_id'] = 'previous-boot'
        with self.assertRaises(FleetBlocked): self.consume()
        self.assertEqual(self.acks, [])
    def test_cross_account(self):
        self.frame['account_id'] = 'other'
        with self.assertRaises(FleetBlocked): self.consume()
    def test_stale_revision(self):
        self.consume(); self.frame['revision'] = 0
        with self.assertRaises(FleetBlocked): self.consume()
    def test_same_revision_conflict(self):
        self.consume(); self.frame['emergency_cap'] = 70
        with self.assertRaises(FleetBlocked): self.consume()
    def test_rollover_requires_canonical_refresh(self):
        self.frame['business_day'] = '2026-09-05'
        with self.assertRaises(FleetBlocked): self.consume()
    def test_partial_counters_fail_closed(self):
        del self.frame['canonical_follows_before_run']
        with self.assertRaises(FleetBlocked): self.consume()
    def test_ack_once_per_policy_revision(self):
        self.consume(52); self.consume(53)
        self.assertEqual(len(self.acks), 1)
    def test_failed_ack_is_not_certified(self):
        def fail(value): raise OSError('local ACK failure')
        self.adapter.write_ack = fail
        with self.assertRaises(OSError): self.consume()
        self.assertEqual(self.adapter.acked_revision, -1)
        self.adapter.write_ack = self.acks.append
        self.consume(); self.assertEqual(len(self.acks), 1)


if __name__ == '__main__': unittest.main()
