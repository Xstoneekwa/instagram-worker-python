#!/usr/bin/env python3
"""Replay real migrations in a disposable PG17 + PostgREST; no production DSN.

The Backend is a read-only schema dependency, pinned below. The HTTP adapter
only strips Supabase's /rest/v1 prefix. No application RPC is mocked.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
PG = Path('/opt/homebrew/opt/postgresql@17/bin')
BACKEND_SHA = '20718305def88ae2ceb9a0c2fa3bc2b016151cf1'
CANDIDATE_BACKEND_SHA = '90fd0ef932f73d353d3d4b51c68ac2047c9c9864'
MIGRATION = '20260914122213_operator_stop_natural_tick_lineage_v1.sql'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--backend-root', type=Path, required=True)
    ap.add_argument('--candidate-backend-sha', default=CANDIDATE_BACKEND_SHA,
                    help='Exact Backend candidate commit; pass the final V6 commit after governance closure.')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--test-name', help='Run one named isolated contract after full migration replay.')
    ap.add_argument('--lineage-only', action='store_true',
                    help='Run the existing terminal-lineage SQL tests with this migration installed.')
    ap.add_argument('--restriction-only', action='store_true', help='Run inherited restriction/readiness real SQL tests with the new trigger.')
    ap.add_argument('--operator-only', action='store_true')
    args = ap.parse_args()
    human = not (args.operator_only or args.lineage_only or args.restriction_only)
    if human: args.restriction_only = True
    if args.lineage_only and args.restriction_only: ap.error('select one contract group')
    backend = args.backend_root.resolve()
    candidate_backend = ROOT.parent / 'backend-incident-system-global-resolution-v2'
    if subprocess.check_output(['git', '-C', str(candidate_backend), 'rev-parse', 'HEAD'], text=True).strip() != args.candidate_backend_sha:
        raise RuntimeError('candidate_backend_base_mismatch')
    if subprocess.check_output(['git', '-C', str(backend), 'rev-parse', 'HEAD'], text=True).strip() != BACKEND_SHA:
        raise RuntimeError('schema_dependency_sha_mismatch')
    if subprocess.check_output(['git', '-C', str(backend), 'status', '--porcelain'], text=True).strip():
        raise RuntimeError('schema_dependency_dirty')
    output = args.output.resolve()
    output.relative_to(ROOT.parent)  # Task-owned evidence, never a runtime path.
    if output.is_relative_to(ROOT) or output.exists():
        raise RuntimeError('new_external_evidence_path_required')
    output.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PG', 'SUPABASE', 'PGRST', 'PHONEFARM_', 'WORKER_', 'AUTO_RESTART_'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['RUNTIME_INCIDENTS_ENABLED'] = 'true'
    migrations = []
    port = '55483' if args.lineage_only else '55484'
    prefix = 'auto-restart-lineage-pg17-' if args.lineage_only else 'restriction-resume-pg17-'
    with tempfile.TemporaryDirectory(prefix=prefix, dir='/private/tmp') as tmp:
        task = Path(tmp)
        env['PHONEFARM_RUNTIME_ROOT'] = str(task / 'runtime')
        command = lambda cmd, **kw: subprocess.run([str(x) for x in cmd], env=env, check=True, **kw)
        try:
            command([PG / 'initdb', '-D', task / 'db', '-A', 'trust', '-U', 'postgres',
                     '--no-locale', '--encoding=UTF8'], capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError('isolated_initdb_failed:' + exc.stderr) from exc
        pglog = (output / 'postgres.log').open('w')
        server = subprocess.Popen([str(PG / 'postgres'), '-D', str(task / 'db'), '-k', tmp,
                                   '-p', port, '-c', 'listen_addresses='], env=env, stdout=pglog, stderr=subprocess.STDOUT)
        rest = None
        adapter = None
        try:
            for _ in range(100):
                if (task / ('.s.PGSQL.' + port)).exists():
                    break
                if server.poll() is not None:
                    raise RuntimeError('isolated_postgres_start_failed')
                time.sleep(.05)
            psql = [PG / 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-h', tmp, '-p', port, '-U', 'postgres', '-d', 'postgres']
            def sql(query):
                r = command(psql, input=query, text=True, capture_output=True)
                return r.stdout.strip()
            def replay(path, *, raw_override=None, evidence_name=None):
                raw = path.read_bytes() if raw_override is None else raw_override
                try:
                    sql(raw.decode())
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError(f'migration_failed:{path.name}:{exc.stderr[-3000:]}') from exc
                migrations.append({'name': evidence_name or path.name,
                                   'sha256': hashlib.sha256(raw).hexdigest()})
            for name in ('0000_local_platform_compatibility.sql', '20260728001632_public_schema.sql'):
                replay(backend / 'supabase/baseline' / name)
            sql("INSERT INTO auth.users(id,email) VALUES ('580d7856-d60f-4838-a5f9-3b405d6ae79b','synthetic-owner@example.invalid'); INSERT INTO public.tenant_users(user_id,role) VALUES ('580d7856-d60f-4838-a5f9-3b405d6ae79b','superadmin');")
            files = [p for p in (backend / 'supabase/migrations').glob('*.sql')
                     if p.name[0].isdigit() and p.name > '20260728001632' and 'rollback' not in p.name.lower()]
            files += [ROOT / 'supabase/migrations' / n for n in (
                '20260826021814_control_plane_reliability_v1.sql',
                '20260826134332_fast_recovery_active_state_v1.sql',
                '20260826222059_resume_plan_contract_v1.sql',
                '20260911140247_worker_promotion_admission_fence_v1.sql')]
            for path in sorted(files, key=lambda p: p.name):
                if path.name == '20260815024500_pre_run_incident_resume_not_null_v1.sql':
                    definition = sql("select pg_get_functiondef(oid) from pg_proc where proname='reconcile_resolved_pre_run_incident_authorizations_v1'")
                    import re
                    if not re.search(r"restart_block_reason\s*=\s*''", definition):
                        raise RuntimeError('historical_preapplied_fix_not_proven')
                    continue
                replay(path)
            replay(ROOT / 'supabase/migrations/20260913100519_auto_restart_terminal_lineage_v1.sql')
            for name in ('seed.sql', 'ready.sql', 'settings.sql', 'package.sql'):
                path = backend / 'tests/auto-restart-real-pg17-v2' / name
                if human and name == 'seed.sql':
                    # Bind the source to the fixture assignment's actual first
                    # occurrence. V5 advances only the scheduler's injected
                    # clock; no historical request/key/timestamp is rewritten.
                    raw = path.read_text()
                    needle = "'failed','account_session','auto_restart','system','local-source','{}'"
                    slot = "(date_trunc('day',now())+floor(extract(hour from now())/6)*interval '6 hours')"
                    assignment_window = "date_trunc('day',now()),date_trunc('day',now())+interval '6 hour'"
                    # The assignment and its seed resume plan describe the
                    # same occurrence and therefore move together.
                    if raw.count(assignment_window) != 2:
                        raise RuntimeError('v5_clock_fixture_assignment_contract_changed')
                    raw = raw.replace(assignment_window, f"{slot},{slot}+interval '6 hours'")
                    slot_key = f"to_char({slot},'YYYY-MM-DD\"T\"HH24:MI:SSTZH:TZM')"
                    base_meta = (
                        "jsonb_build_object('source','schedule_session_cron','trigger','scheduler',"
                        "'assignment_id','00000000-0000-0000-0000-000000009008',"
                        "'worker_id','schedule_session_cron',"
                        f"'scheduled_session_at',{slot},'scheduled_session_ends_at',{slot}+interval '6 hours',"
                        f"'business_action_deadline',to_char(({slot}+interval '5 hours 50 minutes') at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"'),"
                        "'business_action_deadline_source','scheduler_canonical_device_window_v1',"
                        "'device_timezone','Africa/Johannesburg')"
                    )
                    binding = (
                        "jsonb_build_object('lease_id','00000000-0000-4000-8000-000000009099',"
                        "'generation','00000000-0000-4000-8000-000000009099',"
                        "'device_id','00000000-0000-0000-0000-000000009006',"
                        "'account_id','00000000-0000-0000-0000-000000009002',"
                        "'app_instance_id','00000000-0000-0000-0000-000000009007',"
                        "'request_id','00000000-0000-0000-0000-000000009010',"
                        "'worker_id','schedule_session_cron','lease_seconds',900,"
                        "'lease_expires_at',now()-interval '1 minute')"
                    )
                    payload = (
                        "jsonb_build_object('account_id','00000000-0000-0000-0000-000000009002',"
                        "'requested_by',null,'actor_type','system',"
                        "'source_surface','instagram_schedule_session_cron',"
                        "'requested_run_type','account_session','priority',0,"
                        f"'metadata',{base_meta},'device_id','00000000-0000-0000-0000-000000009006',"
                        "'app_instance_id','00000000-0000-0000-0000-000000009007')"
                    )
                    replacement = (
                        "'failed','account_session','instagram_schedule_session_cron','system',"
                        "'schedule-session:00000000-0000-0000-0000-000000009008:'||"
                        f"{slot_key},{base_meta}||jsonb_build_object("
                        f"'device_lock_binding',{binding},'fleet_atomic_enqueue_payload',{payload})"
                    )
                    if raw.count(needle) != 1:
                        raise RuntimeError('v4_clock_fixture_seed_contract_changed')
                    replay(path, raw_override=raw.replace(needle, replacement).encode(),
                           evidence_name='seed.sql#v5-canonical-first-occurrence')
                else:
                    replay(path)
            if args.restriction_only:
                replay(ROOT / 'tests/restriction_resume_natural_tick_v1.sql')
                replay(ROOT / 'supabase/migrations/20260914021526_restriction_resume_natural_tick_v1.sql')
                overlays = ROOT.parent / 'worker-restriction-resume-natural-tick-v1-activation/pg17-rollout-final/production-definition-overlays.json'
                expected = json.loads(overlays.read_text())
                catalog = json.loads((overlays.parents[1] / 'production-catalog-before.json').read_text())
                for name, digest in expected.items():
                    value = next(v for v in catalog if v['proname'] == name)
                    if hashlib.sha256(value['definition'].encode()).hexdigest() != digest:
                        raise RuntimeError('production_overlay_evidence_changed')
                    sql(value['definition'] + ';')
                replay(ROOT / 'supabase/migrations/20260914104008_resolved_incident_growth_ready_v1.sql')
                replay(ROOT / 'supabase/migrations' / MIGRATION)
                replay(ROOT / 'tests/resolved_incident_growth_ready_v1.sql')
            else:
                replay(ROOT / 'tests/auto_restart_terminal_lineage_v1.sql')
                replay(ROOT / 'supabase/migrations/20260914021526_restriction_resume_natural_tick_v1.sql')
                replay(ROOT / 'supabase/migrations/20260914104008_resolved_incident_growth_ready_v1.sql')
                # Preserve exact predecessor for the required fail-before/pass-after
                # test and isolated rollback. Production is never contacted.
                (output / 'predecessor.sql').write_text(sql(
                    "select pg_get_functiondef(to_regprocedure('public.apply_ordinary_auto_restart_lineage_v1()'))") + ';\n')
                if args.lineage_only:
                    replay(ROOT / 'supabase/migrations' / MIGRATION)
                else:
                    replay(ROOT / 'tests/operator_stop_natural_tick_lineage_v1.sql')
            if human:
                (output / 'predecessor.sql').write_text(sql("select pg_get_functiondef('public.reconcile_stale_account_session_resume_plans_v1(integer)'::regprocedure)")+';\n')
                replay(ROOT / 'tests/human_resume_terminal_plan_v1.sql')
            if True:
                replay(ROOT / 'supabase/migrations/20260914223102_human_resume_terminal_plan_v1.sql')
            replay(ROOT / 'supabase/migrations/20260915102538_incident_system_global_resolution_v2.sql')
            if human:
                # The production RPC and cron remain byte-for-byte identical
                # except for this isolated clock authority. Both consumers now
                # observe the same controlled instant, so the proof advances
                # time rather than rewriting a historical occurrence.
                sql("create schema if not exists restriction_test; create table restriction_test.deterministic_clock(observed_at timestamptz not null); insert into restriction_test.deterministic_clock values (now());")
                schedule_gate = sql("select pg_get_functiondef('public.evaluate_account_schedule_gate(uuid,text,text)'::regprocedure)")
                clock_needles = (
                    'v_now timestamp with time zone := now();',
                    'v_now timestamptz := now();',
                )
                matches = [needle for needle in clock_needles if needle in schedule_gate]
                if len(matches) != 1 or schedule_gate.count(matches[0]) != 1:
                    raise RuntimeError('isolated_schedule_clock_seam_mismatch')
                schedule_gate = schedule_gate.replace(
                    matches[0],
                    'v_now timestamp with time zone := (select observed_at from restriction_test.deterministic_clock);',
                )
                sql(schedule_gate + ';')
                (output / 'isolated-clock-authority.json').write_text(json.dumps({
                    'scope': 'disposable_pg17_only',
                    'production_function_changed': False,
                    'function': 'public.evaluate_account_schedule_gate(uuid,text,text)',
                    'only_replacement': matches[0],
                    'clock_authority': 'restriction_test.deterministic_clock.observed_at',
                }, indent=2) + '\n')
            (output / 'migration-replay.json').write_text(json.dumps(migrations, indent=2) + '\n')
            def free_port():
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    return sock.getsockname()[1]
            rest_port = free_port()
            secret = 'local-only-terminal-lineage-fixture-not-a-production-key'
            encode = lambda x: base64.urlsafe_b64encode(json.dumps(x).encode()).rstrip(b'=').decode()
            content = encode({'alg': 'HS256', 'typ': 'JWT'}) + '.' + encode({'role': 'service_role', 'exp': int(time.time()) + 3600})
            token = content + '.' + base64.urlsafe_b64encode(hmac.new(secret.encode(), content.encode(), hashlib.sha256).digest()).rstrip(b'=').decode()
            restenv = {**env, 'PGRST_DB_URI': f'postgresql://postgres@/postgres?host={tmp}&port={port}',
                       'PGRST_DB_SCHEMAS': 'public', 'PGRST_DB_ANON_ROLE': 'anon',
                       'PGRST_SERVER_HOST': '127.0.0.1', 'PGRST_SERVER_PORT': str(rest_port),
                       'PGRST_JWT_SECRET': secret}
            restlog = (output / 'postgrest.log').open('w')
            rest = subprocess.Popen(['/opt/homebrew/bin/postgrest'], env=restenv, stdout=restlog, stderr=subprocess.STDOUT)
            for _ in range(100):
                try:
                    with socket.create_connection(('127.0.0.1', rest_port), .1):
                        break
                except OSError:
                    if rest.poll() is not None:
                        raise RuntimeError('isolated_postgrest_start_failed')
                    time.sleep(.05)
            class Adapter(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass
                def handle_rpc(self):
                    if not self.path.startswith('/rest/v1/'):
                        self.send_error(404)
                        return
                    body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'connection')}
                    connection = http.client.HTTPConnection('127.0.0.1', rest_port, timeout=10)
                    connection.request(self.command, self.path[len('/rest/v1'):], body, headers)
                    response = connection.getresponse()
                    data = response.read()
                    self.send_response(response.status)
                    for k, v in response.getheaders():
                        if k.lower() not in ('transfer-encoding', 'connection', 'content-length'):
                            self.send_header(k, v)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    connection.close()
                do_GET = do_POST = do_PATCH = do_HEAD = handle_rpc
            class AdapterHTTPServer(ThreadingHTTPServer):
                request_queue_size = socket.SOMAXCONN

            adapter = AdapterHTTPServer(('127.0.0.1', 0), Adapter)
            threading.Thread(target=adapter.serve_forever, daemon=True).start()
            testenv = {**env, 'SUPABASE_URL': f'http://127.0.0.1:{adapter.server_port}',
                       'SUPABASE_SERVICE_ROLE_KEY': token, 'RESTRICTION_TEST_SOCKET': tmp,
                       'RESTRICTION_TEST_BACKEND_ROOT': str(candidate_backend), 'RESTRICTION_TEST_OUTPUT': str(output), 'OPERATOR_STOP_TEST_SOCKET': tmp,
                       'INCIDENT_SYSTEM_BASELINE_BACKEND_ROOT': str(backend),
                       'OPERATOR_STOP_TEST_PORT': port, 'OPERATOR_STOP_TEST_OUTPUT': str(output),
                       'OPERATOR_STOP_BACKEND_ROOT': str(backend)}
            test_file = 'test_operator_stop_natural_tick_lineage_v1.py'
            if args.restriction_only:
                test_file = 'test_resolved_incident_growth_ready_v1.py'
            if args.lineage_only:
                testenv.update(LINEAGE_TEST_SOCKET=tmp, LINEAGE_TEST_OUTPUT=str(output))
                test_file = 'test_auto_restart_terminal_lineage_v1.py'
            if human: test_file = 'test_incident_system_global_resolution_v2.py'
            selection = ['-k', args.test_name] if args.test_name else []
            entry = ['-m', 'unittest', 'discover', '-s', 'tests', '-p', test_file, '-v', *selection]
            if args.restriction_only and not human:
                # The inherited two rollback tests locate an external receipt
                # via ROOT.name. Bind only that evidence location to its actual
                # owner; code, MIGRATION, RPCs and assertions remain unchanged.
                historical = ROOT.parent / 'worker-resolved-incident-growth-ready-v1'
                rollback = historical.parent / (historical.name + '-approval1') / 'rollback.sql'
                if (not rollback.is_file() or hashlib.sha256(rollback.read_bytes()).hexdigest()
                        != 'ad95bb4a57c9147295111d7b2a6a1de31910484371857d49504ba86006eaf8a5'):
                    raise RuntimeError('historical_restriction_rollback_evidence_missing')
                (output / 'historical-rollback-evidence.json').write_text(json.dumps({
                    'path': str(rollback), 'sha256': hashlib.sha256(rollback.read_bytes()).hexdigest()}))
                entry = ['-c', "import sys,unittest;from pathlib import Path;sys.path.insert(0,'tests');"
                    "import test_resolved_incident_growth_ready_v1 as cases;"
                    "cases.ROOT=Path(sys.argv[1]);unittest.main(module=cases,argv=['qualified','-v']+sys.argv[2:])",
                    str(historical), *selection]
            result = subprocess.run(['/usr/bin/python3', '-B', *entry], cwd=ROOT,
                                    env=testenv, capture_output=True, text=True)
            (output / 'tests.log').write_text(result.stdout + result.stderr)
            print(result.stderr[-12000:])
            print(json.dumps({'integration_exit_code': result.returncode, 'migration_count': len(migrations), 'evidence': str(output)}))
            return result.returncode
        finally:
            if adapter:
                adapter.shutdown()
                adapter.server_close()
            if rest:
                rest.terminate()
                rest.wait(timeout=15)
                restlog.close()
            server.terminate()
            server.wait(timeout=15)
            pglog.close()


if __name__ == '__main__':
    raise SystemExit(main())
