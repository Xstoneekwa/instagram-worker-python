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
BACKEND_SHA = 'e8419c560f3623a50cbdefdb9c3a9b56235725ea'
MIGRATION = '20260914021526_restriction_resume_natural_tick_v1.sql'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--backend-root', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--lineage-only', action='store_true',
                    help='Run the existing terminal-lineage SQL tests with this migration installed.')
    args = ap.parse_args()
    backend = args.backend_root.resolve()
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
    migrations = []
    port = '55483' if args.lineage_only else '55484'
    prefix = 'auto-restart-lineage-pg17-' if args.lineage_only else 'restriction-resume-pg17-'
    with tempfile.TemporaryDirectory(prefix=prefix, dir='/private/tmp') as tmp:
        task = Path(tmp)
        env['PHONEFARM_RUNTIME_ROOT'] = str(task / 'runtime')
        command = lambda cmd, **kw: subprocess.run([str(x) for x in cmd], env=env, check=True, **kw)
        try:
            command([PG / 'initdb', '-D', task / 'db', '-A', 'trust', '-U', 'postgres', '--no-locale'], capture_output=True, text=True)
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
            def replay(path):
                raw = path.read_bytes()
                try:
                    sql(raw.decode())
                except subprocess.CalledProcessError as exc:
                    raise RuntimeError(f'migration_failed:{path.name}:{exc.stderr[-3000:]}') from exc
                migrations.append({'name': path.name, 'sha256': hashlib.sha256(raw).hexdigest()})
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
                replay(backend / 'tests/auto-restart-real-pg17-v2' / name)
            if args.lineage_only:
                replay(ROOT / 'tests/auto_restart_terminal_lineage_v1.sql')
                replay(ROOT / 'supabase/migrations' / MIGRATION)
            else:
                replay(ROOT / 'tests/restriction_resume_natural_tick_v1.sql')
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
            adapter = ThreadingHTTPServer(('127.0.0.1', 0), Adapter)
            threading.Thread(target=adapter.serve_forever, daemon=True).start()
            testenv = {**env, 'SUPABASE_URL': f'http://127.0.0.1:{adapter.server_port}',
                       'SUPABASE_SERVICE_ROLE_KEY': token, 'RESTRICTION_TEST_SOCKET': tmp,
                       'RESTRICTION_TEST_BACKEND_ROOT': str(backend), 'RESTRICTION_TEST_OUTPUT': str(output)}
            test_file = 'test_restriction_resume_natural_tick_v1.py'
            if args.lineage_only:
                testenv.update(LINEAGE_TEST_SOCKET=tmp, LINEAGE_TEST_OUTPUT=str(output))
                test_file = 'test_auto_restart_terminal_lineage_v1.py'
            result = subprocess.run(['/usr/bin/python3', '-B', '-m', 'unittest', 'discover', '-s', 'tests',
                                     '-p', test_file, '-v'], cwd=ROOT,
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
