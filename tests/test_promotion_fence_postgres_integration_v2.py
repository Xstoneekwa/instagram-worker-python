"""Execute the exact candidate migration on an isolated, socket-only PostgreSQL."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


class FencePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='worker-fence-v2-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.bin = Path('/opt/homebrew/bin')
        for name in ('initdb', 'postgres', 'psql'):
            if not (cls.bin / name).exists():
                raise RuntimeError('local_postgres_required:' + name)
        initialized = subprocess.run([str(cls.bin / 'initdb'), '-D', str(cls.root / 'db'), '-A', 'trust', '--no-locale'], capture_output=True, text=True)
        if initialized.returncode:
            raise RuntimeError(initialized.stderr)
        cls.log = (cls.root / 'postgres.log').open('w')
        cls.addClassCleanup(cls.log.close)
        cls.server = subprocess.Popen([str(cls.bin / 'postgres'), '-D', str(cls.root / 'db'),
                                      '-k', str(cls.root), '-p', '55479', '-c', 'listen_addresses='],
                                     stdout=cls.log, stderr=subprocess.STDOUT)
        cls.addClassCleanup(cls.stop)
        for _ in range(100):
            if (cls.root / '.s.PGSQL.55479').exists():
                break
            if cls.server.poll() is not None:
                raise RuntimeError('isolated_postgres_failed')
            time.sleep(.05)
        cls.sql('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role; '
                'CREATE TABLE account_run_requests (id text primary key, status text); '
                'CREATE TABLE ig_runs (id text primary key, status text); '
                'CREATE TABLE auto_restart_device_locks (id text primary key, lease_expires_at timestamptz); '
                'CREATE TABLE auto_restart_tick_locks (id text primary key, status text);')
        migration = Path(__file__).resolve().parents[1] / 'supabase/migrations/20260911140247_worker_promotion_admission_fence_v1.sql'
        cls.sql(migration.read_text())

    @classmethod
    def stop(cls):
        if cls.server.poll() is None:
            cls.server.terminate()
            cls.server.wait(timeout=15)

    @classmethod
    def sql(cls, query, *, success=True):
        p = subprocess.run([str(cls.bin / 'psql'), '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
                            '-h', str(cls.root), '-p', '55479', '-d', 'postgres'],
                           input=query, text=True, capture_output=True)
        if success and p.returncode:
            raise AssertionError(p.stderr)
        return p

    def setUp(self):
        self.sql('TRUNCATE phonefarm_runtime_barriers, account_run_requests, ig_runs, auto_restart_device_locks, auto_restart_tick_locks;')

    def acquire(self):
        return json.loads(self.sql("SET ROLE service_role; SELECT acquire_phonefarm_worker_promotion_fence_v1('test-transaction', repeat('a',40));").stdout.splitlines()[-1])

    def test_exact_rpc_argument_discovery_and_privileges(self):
        value = self.sql("SELECT proname, pg_get_function_identity_arguments(oid) FROM pg_proc WHERE proname LIKE '%worker_promotion_fence_v1' ORDER BY proname").stdout
        self.assertIn('acquire_phonefarm_worker_promotion_fence_v1|p_transaction_id text, p_candidate_sha text', value)
        self.assertIn('inspect_phonefarm_worker_promotion_fence_v1|p_transaction_id text', value)
        self.assertIn('release_phonefarm_worker_promotion_fence_v1|p_transaction_id text, p_terminal_state text', value)
        self.assertNotEqual(self.sql("SET ROLE anon; SELECT inspect_phonefarm_worker_promotion_fence_v1('test-transaction');", success=False).returncode, 0)

    def test_acquire_status_release_and_wrong_owner(self):
        self.assertTrue(self.acquire()['active'])
        status = json.loads(self.sql("SELECT inspect_phonefarm_worker_promotion_fence_v1('test-transaction');").stdout)
        self.assertTrue(status['persistent'])
        wrong = json.loads(self.sql("SELECT release_phonefarm_worker_promotion_fence_v1('wrong-owner','PROMOTED');").stdout)
        self.assertFalse(wrong['ok']); self.assertTrue(wrong['active'])
        good = json.loads(self.sql("SELECT release_phonefarm_worker_promotion_fence_v1('test-transaction','PROMOTED');").stdout)
        self.assertTrue(good['ok']); self.assertFalse(good['active'])

    def test_database_enforces_all_table_admission_paths(self):
        self.sql("INSERT INTO account_run_requests VALUES ('prior','queued');")
        self.acquire()
        for query in ["INSERT INTO account_run_requests VALUES ('manual-or-scheduled','queued')",
                      "UPDATE account_run_requests SET status='claimed' WHERE id='prior'",
                      "INSERT INTO ig_runs VALUES ('run','running')",
                      "INSERT INTO auto_restart_device_locks VALUES ('device',now()+interval '1 minute')",
                      "INSERT INTO auto_restart_tick_locks VALUES ('tick','started')"]:
            with self.subTest(query=query):
                result = self.sql(query, success=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('phonefarm_runtime_admission_fenced', result.stderr)
        self.sql("UPDATE account_run_requests SET status='completed' WHERE id='prior';")

    def test_existing_device_can_renew_but_expired_lease_cannot_reenter(self):
        self.sql("INSERT INTO auto_restart_device_locks VALUES ('running',now()+interval '1 minute'), ('expired',now()-interval '1 minute');")
        self.acquire()
        self.sql("UPDATE auto_restart_device_locks SET lease_expires_at=now()+interval '2 minutes' WHERE id='running';")
        denied = self.sql("UPDATE auto_restart_device_locks SET lease_expires_at=now()+interval '2 minutes' WHERE id='expired';", success=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn('phonefarm_runtime_admission_fenced', denied.stderr)

    def test_fence_survives_client_exit(self):
        self.acquire()  # psql exits; the fence must still be present in a new session.
        self.assertEqual(self.sql("SELECT active AND expires_at='infinity'::timestamptz FROM phonefarm_runtime_barriers WHERE barrier_name='worker_promotion_active'").stdout.strip(), 't')
