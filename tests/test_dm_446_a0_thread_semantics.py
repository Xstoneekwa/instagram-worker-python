"""A0 phase semantics with exact retained physical XML. Offline only."""
import base64,hashlib,unittest,zlib
from unittest.mock import patch
import instagram_navigation as nav
from test_dm_446_readers import Clock,Device,fixture,P
XML=zlib.decompress(base64.b64decode('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')).decode('utf-8')
class A0ThreadSemanticsTests(unittest.TestCase):
    def read(self,xmls,*,cost=0.,probe=0.,target='sunetbassonmua'):
        clock=Clock();clock.now=0.;d=Device(xmls,clock=clock);d.cost=cost
        with patch.object(nav,'time',clock),patch.object(nav,'log',lambda *a,**k:None),patch.object(nav.config,'DM_EXISTING_THREAD_PROBE_S',probe):
            result=nav.detect_dm_thread_state(d,target,pkg=P)
        return result,d,clock
    def test_exact_two_physical_xmls(self):
        self.assertEqual(hashlib.sha256(XML.encode()).hexdigest(),'597836a0b0c9c71d104266ce20b8261efc8eed14bafa2bde6288471584adad52')
        for xml in (XML,XML):
            result,d,c=self.read(xml,probe=0.8)
            self.assertEqual(result[0],'empty_new_thread')
            self.assertEqual(result[1]['reason'],'a0_history_probe_completed')
            self.assertGreaterEqual(c.now,0.8)
    def test_no_two_positive_acceptance_requirement(self):
        result,d,c=self.read(XML)
        self.assertEqual(result[0],'empty_new_thread')
        self.assertEqual((d.reads,result[1]['positive_empty_observations']),(1,1))
    def test_history_probe_inflight_read_over_total_2_5s_is_evaluated(self):
        result,d,c=self.read([XML,XML],cost=1.2535761875,probe=0.8)
        self.assertAlmostEqual(c.now,2.607152375)
        self.assertEqual(result[0],'empty_new_thread')
        self.assertEqual(d.reads,2)
    def test_inflight_existing_history_over_total_budget_is_not_empty(self):
        existing=fixture('B_30_existing_thread').replace('vincentfavrephotographe','sunetbassonmua')
        result,d,c=self.read([XML,existing],cost=1.3,probe=0.8)
        self.assertEqual(result[0],'existing_thread')
        self.assertTrue(result[1]['has_history'])
    def test_probe_unknown_must_not_be_no_history(self):
        for bad in ['<broken','<hierarchy/>',XML.replace('sunetbassonmua','wrong_recipient')]:
            result,d,c=self.read([XML,bad],probe=0.8)
            self.assertEqual(result[0],'unknown')
    def test_initial_composer_deadline_remains_2_5(self):
        result,d,c=self.read(XML,cost=2.6,probe=0.8)
        self.assertEqual(result[0],'unknown')
        self.assertEqual(result[1]['reason'],'dm_composer_budget_exhausted')
        self.assertEqual(d.reads,1)
if __name__=='__main__':unittest.main()
