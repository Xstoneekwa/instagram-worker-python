"""V25 timestamp and exact IO accounting regression tests; no real DB/device."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import UUID
import reconciliation_scan as scan
from test_outreach_slot_v24 import Model


class TimestampTests(unittest.TestCase):
    def test_all_fraction_widths_and_offsets(self):
        for fraction in ('','.1','.12','.123','.1234','.12345','.123456'):
            for offset in ('Z','+00:00','+0000','+02:00','-05:00','-0500'):
                raw='2026-09-15T08:00:00'+fraction+offset
                with self.subTest(raw=raw):
                    parsed=scan.parse_reconciliation_timestamp(raw)
                    expected=datetime.strptime(raw.replace('Z','+0000'),'%Y-%m-%dT%H:%M:%S'+('.%f' if fraction else '')+'%z').astimezone(timezone.utc)
                    self.assertEqual(parsed,expected);self.assertIs(parsed.tzinfo,timezone.utc)
                    self.assertEqual(scan.parse_reconciliation_timestamp(raw.replace('T',' ')),expected)

    def test_equivalent_instants_cross_midnight_and_microseconds(self):
        samples=['2026-09-15T00:00:00.12345Z','2026-09-15T02:00:00.123450+02:00','2026-09-14T19:00:00.12345-0500']
        keys=[scan.key({'created_at':raw,'id':str(UUID(int=1))}) for raw in samples]
        self.assertEqual(keys[0],keys[1]);self.assertEqual(keys[1],keys[2])
        self.assertLess(scan.parse_reconciliation_timestamp('2026-09-14T23:59:59.999999Z'),scan.parse_reconciliation_timestamp('2026-09-15T00:00:00Z'))
        self.assertLess(scan.parse_reconciliation_timestamp('2026-09-15T00:00:00.123449Z'),scan.parse_reconciliation_timestamp(samples[0]))

    def test_malformed_naive_precision_and_invalid_offsets_fail_explicitly(self):
        for raw in (None,5,'','garbage','2026-09-15T08:00:00','2026-02-30T08:00:00Z',
                    '2026-09-15T08:00:00.1234567Z','2026-09-15T08:00:00+25:00',
                    '2026-09-15T08:00:00+02:60','infinity','-infinity','2026-09-15T08:00:00Z extra'):
            with self.subTest(raw=raw),self.assertRaisesRegex(scan.InvalidReconciliationTimestamp,'reconciliation_timestamp_'):
                scan.parse_reconciliation_timestamp(raw)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict(os.environ,{'RECONCILIATION_STATE_DIR':self.tmp.name});env.start();self.addCleanup(env.stop)

    def test_previously_rejected_fraction_reaches_valid_tail(self):
        for width in (1,2,4,5):
            with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'RECONCILIATION_STATE_DIR':directory}):
                m=Model(['missing']*20+['valid'])
                for row in m.rows:row['created_at']='2026-09-15T08:00:00.'+'12345'[:width]+'+00:00'
                self.assertEqual(m.observe()['terminalized'],0)
                self.assertEqual(m.observe()['terminalized'],1)

    def test_equal_instant_offsets_keep_uuid_tie_break(self):
        m=Model(['missing']*20+['valid'])
        forms=['2026-09-15T00:00:00.12345Z','2026-09-15T02:00:00.123450+02:00','2026-09-14T19:00:00.12345-0500']
        for i,row in enumerate(m.rows):row['created_at']=forms[i%3]
        m.observe();self.assertEqual(m.observe()['terminalized'],1)
        self.assertEqual(m.reads,[r['id'] for r in m.rows])

    def test_microsecond_order_and_later_arrival_next_cycle(self):
        m=Model(['missing']*20+['valid']);m.rows[-1]['created_at']='2026-09-15T08:00:00.000001Z'
        m.observe();extra=Model(['valid']*22).rows[-1];extra['created_at']='2026-09-15T08:00:00.000002Z'
        m.rows.append(extra);m.receipts[extra['id']]=None
        self.assertEqual(m.observe()['terminalized'],1)
        self.assertNotIn(extra['id'],m.reads)
        m.observe();m.observe();self.assertIn(extra['id'],m.reads)

    def corrupt_response(self,m,identifier):
        original=m.read
        def read(method,table,**kwargs):
            rows=original(method,table,**kwargs)
            if table=='account_run_requests':
                for row in rows:
                    if row['id']==identifier:row['created_at']='not-a-timestamp'
            return rows
        m.read=read

    def test_malformed_page_row_yields_via_uuid_and_never_completes(self):
        m=Model(['valid']*21);bad=m.rows[0]['id'];self.corrupt_response(m,bad)
        self.assertEqual(m.observe()['terminalized'],0)
        state=json.loads(next(Path(self.tmp.name).rglob('cursor.json')).read_text())
        self.assertEqual(state['mode'],'id_recovery');self.assertEqual(state['request_id'],bad)
        self.assertEqual(m.observe()['terminalized'],19)
        self.assertEqual(m.observe()['terminalized'],1)
        self.assertNotIn(bad,m.completions);self.assertNotIn(bad,m.reads)
        for _ in range(3):m.observe()
        self.assertNotIn(bad,m.reads)
        self.assertEqual(len(m.completions),20)
        self.assertTrue(any(x['malformed_timestamp_rows'] for x in m.metrics))

    def test_malformed_high_water_does_not_starve_older_valid_requests(self):
        m=Model(['valid']*21);bad=m.rows[-1]['id'];self.corrupt_response(m,bad)
        m.observe();self.assertEqual(m.observe()['terminalized'],20)
        self.assertEqual(m.observe()['terminalized'],0)
        self.assertNotIn(bad,m.reads)

    def test_malformed_persisted_cursor_fails_without_reset_or_corruption(self):
        with scan.ReconciliationScan('review') as instance:
            path=instance.directory/'cursor.json'
            value={'version':1,'cursor':['bad',str(UUID(int=1))],'upper':['2026-09-15T08:00:00Z',str(UUID(int=2))]}
            path.write_text(json.dumps(value));before=path.read_bytes()
        m=Model(['valid'])
        self.assertFalse(m.observe()['ok']);self.assertEqual(path.read_bytes(),before);self.assertFalse(m.queries)

    def test_python_process_restart_reloads_fractional_cursor(self):
        m=Model(['missing']*20+['valid'])
        for row in m.rows:row['created_at']='2026-09-15T08:00:00.12345Z'
        m.observe();out=Path(self.tmp.name)/'child-result.json'
        pid=os.fork()
        if pid==0:
            try:
                out.write_text(json.dumps(m.observe()));os._exit(0)
            except BaseException:os._exit(1)
        _,status=os.waitpid(pid,0)
        self.assertEqual(status,0);self.assertEqual(json.loads(out.read_text())['terminalized'],1)

    def test_recovery_mode_survives_scanner_restart(self):
        m=Model(['valid']*21);bad=m.rows[0]['id'];self.corrupt_response(m,bad)
        m.observe();m.observe()
        state=json.loads(next(Path(self.tmp.name).rglob('cursor.json')).read_text())
        self.assertEqual(state['mode'],'id_recovery');self.assertEqual(state['cursor'],m.rows[19]['id'])
        self.assertEqual(m.observe()['terminalized'],1)

    def test_network_max_formula_counts_completion_fallback(self):
        m=Model(['valid']*20);read=m.read;rpc=m.rpc;fallback=[];rpc_names=[]
        def counted_read(method,table,**kwargs):
            query=kwargs['query']
            if query.get('select')=='*':
                fallback.append(query['id']);return [copy.deepcopy(next(r for r in m.rows if r['id']==query['id'][3:]))]
            return read(method,table,**kwargs)
        def lost_response(name,payload,**kwargs):
            rpc_names.append(name);result=rpc(name,payload,**kwargs)
            if name=='complete_account_run_request':raise TimeoutError('lost completion response')
            return result
        m.read=counted_read;m.rpc=lost_response
        self.assertEqual(m.observe()['terminalized'],20)
        self.assertEqual(len(m.queries),3);self.assertEqual(len(fallback),20)
        self.assertEqual(rpc_names.count('read_outreach_terminal_v2'),20)
        self.assertEqual(rpc_names.count('complete_account_run_request'),20)
        self.assertEqual(len(m.queries)+len(rpc_names)+len(fallback),3*20+3)
        print('V25_NETWORK_MAX '+json.dumps(dict(upper=1,page=1,run_batch=1,receipt_reads=20,completion_writes=20,fallback_gets=20,hold_network_reads=0,total=63)))

    def test_local_performance_and_bounded_calls(self):
        for n in (21,101,1000,1001):
            with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'RECONCILIATION_STATE_DIR':directory}):
                m=Model(['missing']*(n-1)+['valid'])
                for row in m.rows:row['created_at']='2026-09-15T08:00:00.12345Z'
                start=time.perf_counter();passes=0
                while not m.completions and passes<52:m.observe();passes+=1
                self.assertEqual(passes,(n+19)//20);self.assertEqual(len(m.reads),n)
                calls=len(m.queries)+len(m.reads)+len(m.completions)
                self.assertEqual(calls,2*passes+1+n+1)
                self.assertEqual(sum(x['cursor_writes'] for x in m.metrics),passes)
                print('V25_PERFORMANCE '+json.dumps(dict(rows=n,rows_fetched=sum(x['rows_fetched']+x['high_water_rows_fetched'] for x in m.metrics),
                    rows_validated=sum(x['rows_validated'] for x in m.metrics),passes=passes,network_calls=calls,
                    ms=round((time.perf_counter()-start)*1000,3),cursor_writes=passes,receipt_reads=len(m.reads),completion_writes=len(m.completions))))

    def test_legacy_v24_cursor_variable_fraction_is_loaded_without_reset(self):
        m=Model(['missing']*20+['valid'])
        for row in m.rows:row['created_at']='2026-09-15T08:00:00.12345+00:00'
        with scan.ReconciliationScan('review') as instance:
            (instance.directory/'cursor.json').write_text(json.dumps(dict(version=1,
                cursor=[m.rows[19]['created_at'],m.rows[19]['id']],
                upper=[m.rows[20]['created_at'],m.rows[20]['id']])))
        self.assertEqual(m.observe()['terminalized'],1)
        self.assertEqual(m.reads,[m.rows[20]['id']])

    def test_malformed_transport_value_repaired_later_is_revisited(self):
        m=Model(['valid']*21);bad=m.rows[0]['id'];original=m.read
        self.corrupt_response(m,bad)
        m.observe();m.observe();m.observe()
        self.assertNotIn(bad,m.reads)
        m.read=original
        self.assertEqual(m.observe()['terminalized'],1)
        self.assertEqual(m.reads.count(bad),1)
        self.assertEqual(len(set(m.completions)),21)
