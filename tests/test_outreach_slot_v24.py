"""Offline fairness model exercises the emitted PostgREST predicates, not offsets."""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

import account_run_request_consumer as consumer
import reconciliation_scan as scan_module
import test_outreach_slot_contract as fixtures


def split_terms(text):
    result=[]; start=0; depth=0; quoted=False
    for i,c in enumerate(text):
        if c=='"': quoted=not quoted
        if not quoted:
            if c=='(': depth+=1
            elif c==')': depth-=1
            elif c==',' and depth==0: result.append(text[start:i]);start=i+1
    return result+[text[start:]]


def db_timestamp(value):
    # Independent PostgreSQL ordering model: compare aware instants, never text.
    value=value.replace('Z','+0000').replace(' ','T')
    fractional='.' in value[:value.rfind('+') if '+' in value else len(value)]
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S' + ('.%f' if fractional else '') + '%z')


def matches(row, expression):
    for op in ('and', 'or'):
        if expression.startswith(op+'('):
            values=[matches(row,part) for part in split_terms(expression[len(op)+1:-1])]
            return all(values) if op=='and' else any(values)
    field,op,value=expression.split('.',2)
    value=json.loads(value) if value.startswith('"') else value
    actual=row[field]
    if field=='created_at':actual,value=db_timestamp(actual),db_timestamp(value)
    return {'eq':actual==value,'gt':actual>value,'lt':actual<value,'lte':actual<=value}[op]


class Model:
    def __init__(self, kinds):
        f=fixtures.SlotTests();f.setUp()
        self.rows=[];self.receipts={};self.reads=[];self.completions=[];self.queries=[];self.metrics=[]
        for i,kind in enumerate(kinds,1):
            identifier=str(UUID(int=i));run=str(UUID(int=10000+i))
            ctx={**f.ctx,'request_id':identifier,'root_request_id':identifier,'attempt_id':1}
            self.rows.append(dict(id=identifier,created_at='2026-09-15T08:00:00+00:00',
                run_id=run,account_id=ctx['account_id'],requested_run_type='outreach_session',
                metadata_safe={'outreach_slot_v1':ctx},status='running',claimed_by='review'))
            receipt=fixtures.terminal_receipt(ctx,run_id=run)
            self.receipts[identifier]=None if kind=='missing' else ({**receipt,'attempt_id':99} if kind=='invalid' else receipt)

    def read(self, method, table, *, query, **kwargs):
        assert method=='GET';assert kwargs=={'request_timeout':5.0,'max_retries':0}
        self.queries.append((table,copy.deepcopy(query)))
        if table=='ig_runs':
            ids=query['id'][4:-1].split(',')
            return [{'id':i,'status':'completed'} for i in ids]
        assert table=='account_run_requests'
        assert 'offset' not in query
        owner=json.loads(query['claimed_by'][3:])
        rows=[r for r in self.rows if r['status']=='running' and r['claimed_by']==owner]
        if 'and' in query: rows=[r for r in rows if matches(r,'and'+query['and'])]
        assert query['order'] in ('created_at.asc,id.asc','created_at.desc,id.desc','id.asc','id.desc')
        sort_key=(lambda r:r['id']) if query['order'].startswith('id.') else (lambda r:(db_timestamp(r['created_at']),r['id']))
        rows=sorted(rows,key=sort_key,reverse=query['order'].endswith('desc'))
        return copy.deepcopy(rows[:int(query['limit'])])

    def rpc(self,name,payload,**kwargs):
        identifier=payload['p_request_id']
        if name=='read_outreach_terminal_v2':
            assert kwargs=={'timeout_seconds':5.0}
            self.reads.append(identifier)
            receipt=self.receipts[identifier]
            if isinstance(receipt,Exception):raise receipt
            return copy.deepcopy(receipt)
        if name=='complete_account_run_request':
            row=next(r for r in self.rows if r['id']==identifier)
            if row['status']!='running':return None
            row['status']=payload['p_status'];self.completions.append(identifier)
            return copy.deepcopy(row)
        raise AssertionError('NO BUSINESS RPC: '+name)

    def log(self,level,event,**fields):
        if event=='terminal_reconciliation_pass_v24':self.metrics.append(fields)

    def observe(self,worker='review',limit=20):
        with patch.object(consumer.supabase_client,'_request_json',side_effect=self.read), \
             patch.object(consumer.supabase_client,'call_rpc_once',side_effect=self.rpc), \
             patch.object(consumer,'log',side_effect=self.log):
            return consumer._reconcile_managed_requests_with_terminal_runs(SimpleNamespace(worker_id=worker),limit=limit)


class FairnessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        env=patch.dict(os.environ,{'RECONCILIATION_STATE_DIR':self.temp.name});env.start();self.addCleanup(env.stop)

    def test_twenty_deferred_tail_and_three_pass_progress(self):
        m=Model(['missing']*20+['valid'])
        self.assertEqual(m.observe()['terminalized'],0)
        self.assertEqual(m.observe()['terminalized'],1)
        self.assertEqual(m.observe()['terminalized'],0)
        self.assertEqual(m.reads[20],m.rows[20]['id'])
        self.assertEqual(len(set(m.reads)),21)
        self.assertEqual([x['rows_fetched'] for x in m.metrics],[20,1,20])

    def test_hundred_and_thousand_performance(self):
        for count in (20,100,999):
            with self.subTest(count=count),tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'RECONCILIATION_STATE_DIR':directory}):
                m=Model(['missing']*count+['valid']);start=time.perf_counter();passes=0
                while not m.completions and passes<51:m.observe();passes+=1
                self.assertEqual(passes,(count+20)//20)
                self.assertEqual(len(m.reads),count+1)
                self.assertLessEqual(max(x['rows_fetched'] for x in m.metrics),20)
                print('V24_PERFORMANCE '+json.dumps(dict(rows=count+1,passes_to_reach_valid=passes,
                    rows_fetched=sum(x['rows_fetched']+x['high_water_rows_fetched'] for x in m.metrics),rows_validated=len(m.reads),
                    rows_deferred=sum(x['rows_deferred'] for x in m.metrics),
                    db_roundtrips=len(m.queries)+len(m.reads)+len(m.completions),execution_ms=round((time.perf_counter()-start)*1000,3))))

    def test_all_deferred_one_page_no_hot_loop(self):
        m=Model(['missing']*20)
        for _ in range(3):self.assertEqual(m.observe(),{'ok':True,'observed':20,'terminalized':0})
        self.assertEqual(len(m.reads),60);self.assertEqual(len(m.queries),9)
        self.assertFalse(m.completions)

    def test_permanent_invalid_held_and_valid_tail_progresses(self):
        m=Model(['invalid']*20+['valid'])
        for _ in range(4):m.observe()
        self.assertEqual(len(m.completions),1)
        for r in m.rows[:20]:self.assertEqual(m.reads.count(r['id']),1)
        holds=list(Path(self.temp.name).rglob('invalid-*.json'))
        self.assertEqual(len(holds),20)
        self.assertTrue(all(json.loads(p.read_text())['state']=='OPERATOR_REQUIRED_INVALID_RECEIPT' for p in holds))

    def test_mixed_progress(self):
        m=Model(['missing']*5+['invalid']*5+['valid']*30)
        self.assertEqual(m.observe()['terminalized'],10)
        self.assertEqual(m.observe()['terminalized'],20)
        self.assertEqual(m.observe()['terminalized'],0)
        self.assertEqual(len(m.completions),30)

    def test_eventual_receipt_and_no_duplicate_or_business_writes(self):
        m=Model(['missing','valid']);original=copy.deepcopy(m.rows[0])
        m.observe();self.assertEqual(len(m.completions),1)
        m.receipts[original['id']]=fixtures.terminal_receipt(original['metadata_safe']['outreach_slot_v1'],run_id=original['run_id'])
        m.observe();m.observe()
        self.assertEqual(len(m.completions),2);self.assertEqual(len(set(m.completions)),2)
        self.assertEqual(m.rows[0]['metadata_safe'],original['metadata_safe'])

    def test_transport_failures_are_recheckable(self):
        m=Model(['missing','valid']);m.receipts[m.rows[0]['id']]=TimeoutError('transient')
        m.observe();m.observe()
        self.assertEqual(m.reads.count(m.rows[0]['id']),2)
        self.assertEqual(len(list(Path(self.temp.name).rglob('invalid-*.json'))),0)

    def test_persisted_cursor_survives_new_scanner_and_deletion(self):
        m=Model(['missing']*20+['valid']);m.observe()
        m.rows=m.rows[10:] # deleting earlier rows cannot shift the keyset window
        self.assertEqual(m.observe()['terminalized'],1)

    def test_upper_bound_prevents_arrivals_from_postponing_wrap(self):
        m=Model(['missing']*20+['valid']);m.observe()
        extra=Model(['valid']*22).rows[-1];m.rows.append(extra)
        m.receipts[extra['id']]=None
        m.observe()
        # #22 is beyond the initial high water; next call wraps and revisits #1.
        m.observe();self.assertEqual(m.reads[21],m.rows[0]['id'])

    def test_same_timestamp_tie_order_and_empty_page_wrap(self):
        m=Model(['missing']*20+['valid']);m.observe()
        m.rows.pop();self.assertEqual(m.observe()['observed'],0)
        self.assertEqual(m.observe()['observed'],20)

    def test_concurrent_same_owner_skips_without_db_and_cursor_damage(self):
        m=Model(['valid']);entered=threading.Event();release=threading.Event();errors=[]
        def holder():
            try:
                with scan_module.ReconciliationScan('review') as scan:
                    self.assertIsNotNone(scan);entered.set();release.wait(5)
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=holder);thread.start();self.assertTrue(entered.wait(5))
        try:
            self.assertEqual(m.observe()['observed'],0);self.assertEqual(m.queries,[])
        finally:release.set();thread.join()
        self.assertEqual(errors,[]);self.assertEqual(m.observe()['terminalized'],1)
        self.assertEqual(m.observe()['terminalized'],0)

    def test_distinct_owners_are_disjoint(self):
        m=Model(['valid','valid']);m.rows[1]['claimed_by']='other'
        self.assertEqual(m.observe()['terminalized'],1)
        self.assertEqual(m.observe('other')['terminalized'],1)
        self.assertEqual(len(m.completions),2)

    def test_corrupt_checkpoint_fails_closed_does_not_reset_to_head(self):
        m=Model(['valid'])
        with scan_module.ReconciliationScan('review') as scan:
            (scan.directory/'cursor.json').write_text('{broken')
        self.assertFalse(m.observe()['ok']);self.assertFalse(m.queries);self.assertFalse(m.completions)

    def test_no_arbitrary_deadline_and_invalid_requires_operator_rearm(self):
        self.assertEqual(scan_module.OPERATOR_ESCALATION_DELAY,'UNRESOLVED_NOT_ARBITRARILY_SET')
        m=Model(['invalid']);m.observe();row=m.rows[0]
        m.receipts[row['id']]=fixtures.terminal_receipt(row['metadata_safe']['outreach_slot_v1'],run_id=row['run_id'])
        self.assertEqual(m.observe()['terminalized'],0)
        with scan_module.ReconciliationScan('review') as scan:
            scan.hold_path(row['id'],row['run_id']).rename(scan.directory/'operator-reviewed.json')
        self.assertEqual(m.observe()['terminalized'],1)

    def test_failed_run_read_yields_page_and_rechecks_next_sweep(self):
        m=Model(['missing']*20+['valid']);original=m.read
        def fail_runs(method,table,**kwargs):
            if table=='ig_runs':raise ValueError('injected read failure')
            return original(method,table,**kwargs)
        m.read=fail_runs
        self.assertFalse(m.observe()['ok'])
        m.read=original
        self.assertEqual(m.observe()['terminalized'],1)
        self.assertEqual(m.observe()['observed'],20)

    def test_two_overlapping_reconcilers_single_completion(self):
        m=Model(['valid']);entered=threading.Event();release=threading.Event();results=[]
        original=m.rpc
        def pause_receipt(name,payload,**kwargs):
            if name=='read_outreach_terminal_v2':entered.set();release.wait(5)
            return original(name,payload,**kwargs)
        with patch.object(consumer.supabase_client,'_request_json',side_effect=m.read), \
             patch.object(consumer.supabase_client,'call_rpc_once',side_effect=pause_receipt), \
             patch.object(consumer,'log',side_effect=m.log):
            cfg=SimpleNamespace(worker_id='review')
            thread=threading.Thread(target=lambda:results.append(consumer._reconcile_managed_requests_with_terminal_runs(cfg)))
            thread.start();self.assertTrue(entered.wait(5))
            try:results.append(consumer._reconcile_managed_requests_with_terminal_runs(cfg))
            finally:release.set();thread.join()
        self.assertEqual(sorted(r['terminalized'] for r in results),[0,1])
        self.assertEqual(len(m.completions),1)
