"""Offline child loading, native bootstrap diagnostics and canonical finalization."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import ast
import io
import os
import subprocess
import sys
import tempfile
import unittest

import account_run_request_consumer as consumer

ROOT = Path(__file__).resolve().parents[1]
PARENT = "5ce2261a6a705680f4dc317da6fb628e624ce11d"
ID = lambda n: "00000000-0000-4000-8000-%012d" % n
ACCOUNT, REQUEST, RUN, DEVICE, INSTANCE, ASSIGNMENT, LEASE, NEXT_LEASE = (ID(n) for n in range(1,9))
PACKAGE = "com.instagram.testclone"


def command(run_type="login_provisioning", package=PACKAGE):
    with patch.object(consumer, "_load_expected_username", return_value="synthetic_owner"):
        return consumer._build_runner_command(ACCOUNT, run_type, RUN, run_request_id=REQUEST,
            device_serial="SYNTHETIC_SERIAL", package_name=package, app_instance_id=INSTANCE,
            metadata_safe={"device_id": DEVICE})


class CanonicalChildLoadingTests(unittest.TestCase):
    def child(self, argv):
        with tempfile.TemporaryDirectory(prefix="login-entrypoint-offline-") as directory:
            path = Path(directory)
            (path/"sitecustomize.py").write_text(
                "import sys\n"
                "def guard(event,args):\n"
                " if event in {'subprocess.Popen','os.system','os.exec','os.posix_spawn','socket.connect','socket.connect_ex','socket.getaddrinfo'}:\n"
                "  raise RuntimeError('offline_device_or_network_operation_forbidden')\n"
                "sys.addaudithook(guard)\n")
            dependencies = [p for p in sys.path if p and p != str(ROOT)
                            and not Path(p).resolve().is_relative_to(ROOT)]
            env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR") if k in os.environ}
            env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                PYTHON_DOTENV_DISABLED="1", SUPABASE_ENABLED="false", SUPABASE_URL="",
                SUPABASE_SERVICE_ROLE_KEY="", ADB_PATH="/__offline_no_adb__",
                PHONEFARM_RUNTIME_HOME=str(path/"runtime"),
                PYTHONPATH=os.pathsep.join([str(path), *dependencies]))
            return subprocess.run(argv, cwd=path, env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=20)

    def test_previous_module_command_fails_from_external_cwd(self):
        result = self.child([sys.executable, "-B", "-m", "instagram_login_provisioner_cli", "--help"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("No module named instagram_login_provisioner_cli", result.stderr)

    def test_actual_canonical_entrypoint_loads_from_external_cwd(self):
        result = self.child([*command()[:2], "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--expected-app-instance-id", result.stdout)
        self.assertIn("--package-name", result.stdout)

    def test_uiautomator_dependency_imports_without_connecting(self):
        result = self.child([sys.executable, "-B", "-c",
            "import sys,uiautomator2; assert 'sitecustomize' in sys.modules; "
            "assert callable(uiautomator2.connect); print('dependency_imported_no_connect')"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dependency_imported_no_connect", result.stdout)

    def test_offline_probe_forbids_child_network_and_adb_processes(self):
        result = self.child([sys.executable, "-B", "-c",
            "import socket,subprocess\n"
            "for action in (lambda:socket.getaddrinfo('offline.invalid',443),lambda:subprocess.run(['no-adb'])):\n"
            " try: action()\n"
            " except RuntimeError as e: assert str(e)=='offline_device_or_network_operation_forbidden'\n"
            " else: raise AssertionError('offline guard absent')\n"])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_primary_and_clones_keep_exact_binding_and_single_cli(self):
        for run_type in ("login_provisioning", "login_email_code_resume"):
            for package in ("com.instagram.android", "com.instagram.androie", "com.instagram.androif", PACKAGE):
                with self.subTest(run_type=run_type, package=package):
                    cmd = command(run_type, package)
                    self.assertEqual(cmd[:2], [sys.executable, str(ROOT/"instagram_login_provisioner_cli.py")])
                    for key, value in (("--account-id", ACCOUNT), ("--request-id", REQUEST), ("--run-id", RUN),
                                       ("--package-name", package), ("--expected-app-instance-id", INSTANCE),
                                       ("--device-id", DEVICE), ("--device-serial", "SYNTHETIC_SERIAL")):
                        self.assertEqual(cmd[cmd.index(key)+1], value)
                    self.assertNotIn("-m", cmd)
                    self.assertNotIn("historical_auto_login_07ee_adapter", " ".join(cmd))


class BootstrapDiagnosticTests(unittest.TestCase):
    def test_interpreter_module_refusal_is_known_before_android(self):
        summary = consumer._known_login_bootstrap_failure(
            f"{sys.executable}: No module named instagram_login_provisioner_cli\n")
        self.assertEqual(summary["reason"], "worker_start_failed")
        self.assertEqual(summary["phase"], "worker_start")
        self.assertIs(summary["app_start_attempted"], False)
        self.assertNotIn("stderr", summary)

    def test_missing_or_unreadable_exact_cli_is_pre_cli_failure(self):
        path = ROOT/"instagram_login_provisioner_cli.py"
        for number, message in ((2, "No such file or directory"), (13, "Permission denied")):
            summary = consumer._known_login_bootstrap_failure(
                f"{sys.executable}: can't open file '{path}': [Errno {number}] {message}\n")
            self.assertEqual(summary["reason"], "worker_start_failed")

    def test_unknown_import_traceback_foreign_path_and_oversized_errors_are_not_inferred(self):
        for value in (None, "", "ModuleNotFoundError: No module named 'dependency'",
                      "Traceback: synthetic failure after navigation",
                      f"{sys.executable}: No module named another_engine",
                      f"{sys.executable}: can't open file '/foreign.py': [Errno 2] No such file or directory",
                      f"{sys.executable}: No module named instagram_login_provisioner_cli\nextra value",
                      "x"*4097):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(consumer._known_login_bootstrap_failure(value), {})

    def test_capture_is_bounded_and_uses_the_existing_stderr_sink(self):
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as handle:
            handle.write("x"*12000)
            sink = io.StringIO()
            with patch.object(consumer.sys, "stderr", sink):
                diagnostic = consumer._read_login_bootstrap_stderr(handle)
            self.assertEqual(len(diagnostic), 4097)
            self.assertEqual(sink.getvalue(), "x"*12000)
            self.assertEqual(consumer._known_login_bootstrap_failure(diagnostic), {})
        self.assertTrue(handle.closed)

    def test_closed_log_sink_does_not_interrupt_terminalization(self):
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as handle:
            handle.write(f"{sys.executable}: No module named instagram_login_provisioner_cli\n")
            sink = Mock();sink.write.side_effect = OSError("synthetic sink unavailable")
            with patch.object(consumer.sys,"stderr",sink), patch.object(consumer,"log"):
                diagnostic = consumer._read_login_bootstrap_stderr(handle)
        self.assertEqual(consumer._known_login_bootstrap_failure(diagnostic)["reason"],"worker_start_failed")


class DispatcherTerminalizationTests(unittest.TestCase):
    def fixture(self, native_summary=None, stderr=None, package=PACKAGE):
        stack=ExitStack();self.addCleanup(stack.close)
        directory=stack.enter_context(tempfile.TemporaryDirectory(prefix="login-handoff-test-"))
        worker="run-dispatcher:synthetic"
        cfg=consumer.DispatcherConfig(enabled=True,health_only=False,launch_enabled=True,
            worker_id=worker,poll_seconds=5,lease_seconds=120,heartbeat_seconds=20,
            allowed_run_types=["login_provisioning"],test_account_ids=set(),
            subprocess_timeout_seconds=120,require_assignment=True,enforce_assignment_window=False)
        binding=dict(device_id=DEVICE,account_id=ACCOUNT,request_id=REQUEST,
                     worker_id="pending-request:"+REQUEST,lease_id=LEASE,generation=LEASE,lease_seconds=900)
        successor=dict(binding,worker_id=worker,lease_id=NEXT_LEASE,generation=NEXT_LEASE,ok=True,transferred=True)
        metadata=dict(binding_version="auto_login_app_instance_v1",assignment_id=ASSIGNMENT,
            device_id=DEVICE,app_instance_id=INSTANCE,package_name=package,clone_index=1,
            device_lock_binding=binding)
        request=dict(id=REQUEST,account_id=ACCOUNT,requested_run_type="login_provisioning",
            status="claimed",metadata_safe=metadata)
        context=dict(assignment_found=True,assignment_id=ASSIGNMENT,device_id=DEVICE,
            app_instance_id=INSTANCE,package_name=package,app_instance_index=1,adb_serial="SYNTHETIC_SERIAL")
        row=dict(request,run_id=RUN,status="running",metadata_safe={**metadata,"device_lock_binding":successor})
        order=[];mocks={}
        values={"_account_is_launch_allowed":(True,None),"_load_package_runtime_contract":(True,"ready",{}),
            "mark_account_run_request_starting":True,"_validate_assignment":(True,None,context),
            "evaluate_queued_run_commercial_policy":(True,None,{}),"transfer_device_lock":successor,
            "_create_and_link_login_run":RUN,"_load_expected_username":"synthetic_owner",
            "_heartbeat":None,"_runner_runtime_identity_env":{},"_login_provisioner_env":{},
            "runtime_home":Path(directory),"_integration_noop_runner_enabled":False,
            "_safe_login_provisioner_summary_for_audit":native_summary or {},
            "_fleet_finish_execution":None,"_audit":None,"log":None,
            "get_account_run_request":row,"_safe_complete_account_run_request":None,
            "_publish_run_failure_incident":None}
        for name,value in values.items():mocks[name]=stack.enter_context(patch.object(consumer,name,return_value=value))
        def spawn(*args,**kw):
            order.append("spawn")
            self.assertEqual(args[2],Path(directory))
            self.assertNotEqual(args[2],ROOT)
            capture=kw['popen_options']['stderr'];self.assertIsNotNone(capture)
            capture.write(stderr or "")
            return SimpleNamespace(returncode=1)
        mocks['spawn']=stack.enter_context(patch.object(consumer,'_fleet_spawn_or_legacy',side_effect=spawn))
        def wait(*args,**kw):order.append("wait");return 1,False
        mocks['wait']=stack.enter_context(patch.object(consumer,'_wait_for_subprocess',side_effect=wait))
        def release(**kw):order.append("release");return dict(successor, released=True)
        mocks['release']=stack.enter_context(patch.object(consumer,'release_device_lock',side_effect=release))
        def terminal(**kw):
            order.append("terminalize");row.update(status="failed",lease_expires_at=None)
            return dict(request_status="failed",run_status="failed",persisted_error_code=kw['persisted_error_code'],reason="finalized")
        mocks['terminal']=stack.enter_context(patch.object(consumer,'finalize_auto_login_failure',side_effect=terminal))
        mocks['Popen']=stack.enter_context(patch.object(consumer.subprocess,'Popen',side_effect=AssertionError("no real process")))
        stack.enter_context(patch.object(consumer.sys,'stderr',io.StringIO()))
        return cfg,request,context,row,mocks,order

    def test_one_child_then_exact_lease_release_and_known_native_incident(self):
        cfg,request,_,row,m,order=self.fixture(stderr=f"{sys.executable}: No module named instagram_login_provisioner_cli\n")
        consumer._handle_claimed_request(cfg,request)
        self.assertEqual(order,["spawn","wait","release","terminalize"])
        m['spawn'].assert_called_once();m['Popen'].assert_not_called();m['terminal'].assert_called_once()
        self.assertEqual(row['status'],'failed');self.assertIsNone(row['lease_expires_at'])
        m['release'].assert_called_once_with(device_id=DEVICE,worker_id=cfg.worker_id,request_id=REQUEST,
            expected_lease_id=NEXT_LEASE,account_id=ACCOUNT)
        call=m['terminal'].call_args.kwargs
        self.assertEqual(call['run_id'],RUN);self.assertEqual(call['persisted_error_code'],'worker_start_failed')
        self.assertEqual(call['phase'],'worker_start')
        incident=m['_publish_run_failure_incident'].call_args.kwargs
        self.assertEqual(incident['request_id'],REQUEST);self.assertEqual(incident['run_id'],RUN)
        summary=incident['worker_summary'];self.assertEqual(summary['reason_code'],'worker_start_failed')
        self.assertIs(summary['app_start_attempted'],False);self.assertNotIn('stderr',summary)
        decision=consumer.classify_terminal_run_failure(exit_code=1,run_type='login_provisioning',performance_summary=summary)
        self.assertEqual(decision.reason_code,'worker_start_failed')
        self.assertNotEqual(decision.incident_type,'auto_login_identity_mismatch')
        m['_fleet_finish_execution'].assert_called_once_with(REQUEST)

    def test_unknown_nonzero_exit_does_not_invent_pre_android_failure(self):
        cfg,request,_,_,m,_=self.fixture(stderr="synthetic post-navigation exception\n")
        consumer._handle_claimed_request(cfg,request)
        self.assertEqual(m['terminal'].call_args.kwargs['persisted_error_code'],'unclassified_auto_login_failure')
        self.assertNotIn('app_start_attempted',m['_publish_run_failure_incident'].call_args.kwargs['worker_summary'])
        m['spawn'].assert_called_once();m['release'].assert_called_once()

    def test_existing_native_adb_failure_summary_remains_authoritative(self):
        cfg,request,_,_,m,_=self.fixture(native_summary={'reason':'app_start_failed_after_retry','app_start_attempted':True},
            stderr=f"{sys.executable}: No module named instagram_login_provisioner_cli\n")
        consumer._handle_claimed_request(cfg,request)
        self.assertEqual(m['terminal'].call_args.kwargs['persisted_error_code'],'app_start_failed_after_retry')
        self.assertEqual(m['terminal'].call_args.kwargs['phase'],'open_instagram')

    def test_existing_native_identity_mismatch_remains_a_distinct_refusal(self):
        cfg,request,_,_,m,_=self.fixture(native_summary={'reason':'active_instagram_account_mismatch'})
        consumer._handle_claimed_request(cfg,request)
        summary=m['_publish_run_failure_incident'].call_args.kwargs['worker_summary']
        self.assertEqual(summary['phase'],'identity_verification')
        decision=consumer.classify_terminal_run_failure(exit_code=1,run_type='login_provisioning',performance_summary=summary)
        self.assertEqual(decision.incident_type,'auto_login_identity_mismatch')

    def test_foreign_instance_or_primary_package_never_spawns(self):
        for field,value in [('app_instance_id',ID(99)),('package_name','com.instagram.android')]:
            cfg,request,context,_,m,_=self.fixture()
            context[field]=value
            consumer._handle_claimed_request(cfg,request)
            m['spawn'].assert_not_called();m['release'].assert_not_called();m['terminal'].assert_not_called()
            m['_safe_complete_account_run_request'].assert_called_once()


class SignedParentPreservationTests(unittest.TestCase):
    def parent(self,path):
        return subprocess.check_output(['git','-C',str(ROOT),'show',PARENT+':'+path],stderr=subprocess.PIPE).decode()

    def test_unrelated_functions_and_all_engine_sources_are_unchanged(self):
        old=ast.parse(self.parent('account_run_request_consumer.py'))
        new=ast.parse((ROOT/'account_run_request_consumer.py').read_text())
        changed={'_build_login_provisioner_command','_handle_claimed_request','_finalize_manual_run_after_subprocess'}
        added={'_known_login_bootstrap_failure','_read_login_bootstrap_stderr'}
        def retained(tree):
            return [ast.dump(n) for n in tree.body if not (isinstance(n,ast.FunctionDef) and n.name in changed|added)
                    and not (isinstance(n,ast.Import) and len(n.names)==1 and n.names[0].name=='tempfile')]
        self.assertEqual(retained(old),retained(new))
        for path in ['instagram_login_provisioner_cli.py','instagram_login_provisioner_orchestrator.py',
            'instagram_login_password_form_executor.py','instagram_login_email_code_executor.py',
            'instagram_credentials_runtime_access.py','instagram_supabase_vault_reader.py',
            'historical_auto_login_07ee_adapter.py','auto_login_failure_contract.py',
            'assignment_dispatch_resolver.py','auto_restart_device_lock.py','fleet_runtime_barrier.py']:
            with self.subTest(path=path):self.assertEqual((ROOT/path).read_text(),self.parent(path))

    def test_wait_and_exact_lease_calls_keep_their_parent_arguments(self):
        def calls(source,name):
            tree=ast.parse(source)
            function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_handle_claimed_request')
            return [ast.dump(n) for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id==name]
        old=self.parent('account_run_request_consumer.py');new=(ROOT/'account_run_request_consumer.py').read_text()
        for name in ['_wait_for_subprocess','release_device_lock','renew_device_lock','transfer_device_lock',
                     '_fleet_finish_execution','_validate_login_request_binding','_runner_runtime_identity_env']:
            with self.subTest(name=name):self.assertEqual(calls(old,name),calls(new,name))


if __name__ == '__main__':unittest.main()
