from pathlib import Path
import plistlib
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from promotion_service_control_v2 import control, SERVICES


class LaunchdAdapter:
    def __init__(self):
        self.loaded = True
        self.disabled = False
        self.commands = []
        self.fail = None

    def __call__(self, argv, **kwargs):
        op = argv[1]; self.commands.append(argv)
        result = 0
        if op == self.fail:
            result = 5
        elif op == 'disable':
            self.disabled = True
        elif op == 'enable':
            self.disabled = False
        elif op == 'bootout':
            self.loaded = False if self.disabled else True  # KeepAlive
        elif op == 'bootstrap':
            self.loaded = not self.disabled
        elif op == 'print' and argv[2].count('/') > 1:
            result = 0 if self.loaded else 113
        return SimpleNamespace(returncode=result, stdout='', stderr='')


class ServiceContractTests(unittest.TestCase):
    def fixture(self, service):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        home = Path(tmp.name); runtime = home / 'runtime'
        label, run = SERVICES[service]
        plists = home / 'Library/LaunchAgents'; plists.mkdir(parents=True)
        (plists / (label + '.plist')).write_bytes(plistlib.dumps({'Label': label, 'KeepAlive': True}))
        (runtime / 'run' / run).mkdir(parents=True)
        return dict(home=home, runtime=runtime, uid=501)

    def test_all_services_disable_before_bootout_and_resume(self):
        for name in SERVICES:
            with self.subTest(service=name):
                args = self.fixture(name); launchd = LaunchdAdapter()
                self.assertTrue(control(name, 'pause', runner=launchd, **args)['ok'])
                self.assertFalse(launchd.loaded); self.assertTrue(launchd.disabled)
                self.assertTrue(control(name, 'resume', runner=launchd, **args)['ok'])
                self.assertTrue(launchd.loaded); self.assertFalse(launchd.disabled)
                self.assertFalse(any('gui/0' in arg for call in launchd.commands for arg in call))

    def test_failed_disable_cannot_claim_quiescence(self):
        args = self.fixture('notifier'); launchd = LaunchdAdapter(); launchd.fail = 'disable'
        with self.assertRaisesRegex(ValueError, 'disable_failed'):
            control('notifier', 'pause', runner=launchd, **args)
        self.assertTrue(launchd.loaded)
        self.assertFalse(any(x[1] == 'bootout' for x in launchd.commands))

    def test_real_wrappers_delegate_before_old_runtime_initialization(self):
        root = Path(__file__).resolve().parents[1]
        for name, wrapper in [('dispatcher', 'run_control_dispatcher_service.sh'),
                              ('heartbeat', 'device_heartbeat_service.sh'),
                              ('notifier', 'incident_notifier_service.sh')]:
            for verb in ('pause', 'resume'):
                # Execute the actual shell wrapper. Intercept its exec boundary
                # only; lifecycle behavior is exercised with LaunchdAdapter above.
                command = 'exec() { printf "%s\\n" "$@"; exit 0; }; export -f exec; /bin/bash "$1" "$2"'
                result = subprocess.run(['/bin/bash', '-c', command, 'test',
                                         str(root / 'scripts' / wrapper), verb],
                                        capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout.splitlines()[-3:],
                                 [str(root / 'promotion_service_control_v2.py'), name, verb])

    def test_root_domain_is_rejected(self):
        args = self.fixture('notifier'); args['uid'] = 0
        with self.assertRaises(ValueError):
            control('notifier', 'pause', runner=LaunchdAdapter(), **args)
