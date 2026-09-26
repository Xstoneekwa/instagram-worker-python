"""Execute the real acquisition-abort closures before the Followers loop."""
from __future__ import annotations
import ast
from pathlib import Path
import time
import unittest
from terminal_summary_projection import terminal_reason

SOURCE = Path(__file__).resolve().parents[1] / 'runner.py'


def extracted_abort(remove_early_initialization=False):
    tree=ast.parse(SOURCE.read_text())
    owner=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)
               and any(isinstance(c,ast.FunctionDef) and c.name=='_abort_target_acquisition' for c in n.body))
    names=('_target_scan_stop_reason','_emit_target_scan_completed','_abort_target_acquisition')
    helpers=[n for n in owner.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assignments=[n for n in owner.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_followers_loop_finally_stop' for t in n.targets)]
    early=[n for n in assignments if n.lineno<min(h.lineno for h in helpers)]
    if len(early)!=1 or not isinstance(early[0].value,ast.Constant) or early[0].value.value!='':
        raise AssertionError('exact neutral initialization must precede abort closures')
    body=ast.parse('target_scan_completed_logged=False\n').body
    if not remove_early_initialization:body+=early
    body+=helpers
    body+=ast.parse('''try:
    return _abort_target_acquisition(64, first_causal_reason='global_search_surface_failed', target_outcome='target_recovery_failed', retryable=True, safe_to_skip=False)
finally:
    cleanup_reached.append(True)
_followers_loop_finally_stop = ""
''').body
    fn=ast.FunctionDef(name='execute',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=body,decorator_list=[])
    module=ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[]))
    events=[];summary={};cleanup=[]
    def publish(**kw): summary.update(kw);events.append(('publish',kw))
    def log(level,event,**kw):events.append((event,kw))
    env=dict(terminal_reason=terminal_reason,time=time,t0=time.perf_counter(),target_scan_tracker={},
             source_profile_username='les_bains_st_val',target_id='ct',processed=0,follows_completed_count=0,
             global_follow_goal_effective=60,_followers_session_summary=summary,
             get_followers_engine_stop_reason=lambda:'',_publish_followers_session_summary=publish,
             _target_rejection_summary_payload=lambda tracker,stop_reason:dict(candidates_seen_count=0,candidates_rejected_count=0,stop_reason=stop_reason),log=log,cleanup_reached=cleanup)
    exec(compile(module,str(SOURCE),'exec'),env)
    return env['execute'],events,summary,cleanup


class EarlyAbortTests(unittest.TestCase):
    def test_primary_reason_terminal_payload_and_cleanup_reachable(self):
        call,events,summary,cleanup=extracted_abort()
        self.assertEqual(call(),64)
        self.assertEqual(summary['first_causal_reason'],'global_search_surface_failed')
        self.assertEqual(summary['follow_stop_reason'],'global_search_surface_failed')
        self.assertFalse(summary['target_completed'])
        self.assertEqual(summary['safe_next_step'],'schedule_resume')
        completed=[p for event,p in events if event=='target_scan_completed']
        self.assertEqual(len(completed),1)
        self.assertEqual(completed[0]['stop_reason'],'global_search_surface_failed')
        self.assertEqual(completed[0]['follows_sent'],0)
        self.assertEqual(cleanup,[True])

    def test_removing_only_early_init_reproduces_original_failure(self):
        call,events,summary,cleanup=extracted_abort(True)
        with self.assertRaises(NameError):call()
        self.assertEqual(summary['first_causal_reason'],'global_search_surface_failed')
        self.assertFalse(any(event=='target_scan_completed' for event,_ in events))
        self.assertEqual(cleanup,[True])

    def test_existing_late_reset_remains(self):
        tree=ast.parse(SOURCE.read_text())
        neutral=[n for n in ast.walk(tree) if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant)
                 and n.value.value=='' and any(isinstance(t,ast.Name) and t.id=='_followers_loop_finally_stop' for t in n.targets)]
        self.assertEqual(len(neutral),2)
