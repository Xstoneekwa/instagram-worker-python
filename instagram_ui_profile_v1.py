"""Exact Instagram build selection and immutable A0 run binding.

This is a version fence, not a navigation/action authority. Existing A0 surface,
identity, lease, Stop, Send and persistence contracts remain authoritative.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, fields
import re
from threading import RLock

CERTIFIED = {('372.0.0.48.60', 377611059): 'UI_PROFILE_372',
             ('446.0.0.49.77', 385211304): 'UI_PROFILE_446'}
PACKAGE = re.compile(r'com\.instagram\.[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*\Z')
PLAN_KEY = 'instagram_ui_binding'
_METADATA_READ = ContextVar('instagram_ui_metadata_read', default=False)
_DEPTH = ContextVar('instagram_ui_dispatch_depth', default=0)
_SESSION = None
_CURRENT = object()
_LOCK = RLock()


class UIProfileStopped(BaseException):
    """A run stop must not become a legacy Exception retry or fallback."""
    def __init__(self, reason):
        super().__init__(reason)
        self.summary = {'root_failure_code': reason, 'first_failure_reason': reason,
                        'safe_to_continue_ui': False, 'completed_actions_preserved': True}


# Only preparation diagnostics: these labels confer no UI or lease authority.
_PREPARATION_STAGES = {
    'assignment': ('instagram_ui_assignment_read_failed', 'assignment'),
    'request_read': ('instagram_ui_request_read_failed', 'request'),
    'request_identity': ('instagram_ui_request_conversion_failed', 'request.identity'),
    'request_lineage': ('instagram_ui_request_lineage_invalid', 'request.lineage'),
    'lease_binding': ('instagram_ui_lease_binding_invalid', 'device_lock_binding'),
    'lease_projection': ('instagram_ui_lease_projection_invalid', 'device_lock_binding'),
    'live_lease_read': ('instagram_ui_lease_read_failed', 'device_lock'),
    'live_lease_identity': ('instagram_ui_lease_conversion_failed', 'device_lock.identity'),
    'live_lease_expiry': ('instagram_ui_lease_timestamp_invalid', 'lease_expires_at'),
    'run_read': ('instagram_ui_run_read_failed', 'run'),
    'run_identity': ('instagram_ui_run_conversion_failed', 'run.identity'),
    'package_metadata': ('instagram_ui_package_metadata_read_failed', 'installed_package'),
    'binding_construct': ('instagram_ui_binding_conversion_failed', 'instagram_ui_binding'),
    'parent_binding': ('instagram_ui_parent_binding_read_failed', 'parent.instagram_ui_binding'),
    'run_snapshot': ('instagram_ui_run_snapshot_invalid', 'run.updated_at'),
    'binding_persist': ('instagram_ui_binding_persistence_unproved', 'performance_summary'),
    'binding_readback': ('instagram_ui_binding_readback_failed', 'performance_summary'),
    'resume_binding_persist': ('instagram_ui_resume_binding_persistence_unproved', 'resume_plan'),
    'binding_arm': ('instagram_ui_binding_arm_failed', 'instagram_ui_binding'),
}


def preparation_failure(error, stage):
    """Keep a strict stop and its native chain without retaining sensitive values.

    The existing terminal logger receives stack locations/types separately from
    the public summary. Never format exception messages, source lines or locals:
    transport exceptions can contain credentials or entire response payloads.
    """
    stage = stage if stage in _PREPARATION_STAGES else 'binding_construct'
    reason, field = _PREPARATION_STAGES[stage]
    stopped = error if isinstance(error, UIProfileStopped) else UIProfileStopped(reason)
    chain, seen, current = [], set(), error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        name = type(current).__name__
        name = name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,99}', name) else 'Exception'
        frames, tb = [], current.__traceback__
        while tb is not None and len(frames) < 32:
            frames.append({'file': tb.tb_frame.f_code.co_filename.rsplit('/', 1)[-1][:120],
                           'line': tb.tb_lineno,
                           'function': tb.tb_frame.f_code.co_name[:100]})
            tb = tb.tb_next
        cause = current.__cause__
        context = current.__context__ if not current.__suppress_context__ else None
        chain.append({'exception_type': name, 'traceback': frames,
                      'traceback_truncated': tb is not None,
                      'chained_via': 'cause' if cause is not None else
                                     'context' if context is not None else None})
        current = cause if cause is not None else context
    native_type = next((item['exception_type'] for item in chain
                        if item['exception_type'] != 'UIProfileStopped'), 'UIProfileStopped')
    stopped.summary.update(preparation_stage=stage, preparation_field=field,
                           error_class=native_type)
    stopped.preparation_diagnostic = {'stage': stage, 'field': field,
                                      'exception_type': native_type, 'cause_chain': chain,
                                      'cause_chain_truncated': current is not None}
    return stopped


@dataclass(frozen=True)
class AppMetadata:
    package: str
    version_name: str
    version_code: int


def resolve(metadata, package):
    # Adapted from the qualified ui_action_capability.resolve, without Authority.
    if (type(metadata) is not AppMetadata or not PACKAGE.fullmatch(package or '')
            or metadata.package != package or type(metadata.version_code) is not int
            or (metadata.version_name, metadata.version_code) not in CERTIFIED):
        raise UIProfileStopped('instagram_app_version_not_certified')
    return CERTIFIED[(metadata.version_name, metadata.version_code)]


def parse_installed_package(output, package):
    """Only the unique installed Package section; never a global first hit."""
    if not PACKAGE.fullmatch(package or '') or type(output) is not str:
        raise UIProfileStopped('instagram_package_metadata_unproved')
    matches = list(re.finditer(r'^\s*Package \[([^\]]+)\].*$', output, re.M))
    sections = [output[m.end():matches[i+1].start() if i+1 < len(matches) else len(output)]
                for i, m in enumerate(matches) if m.group(1) == package]
    if len(sections) != 1:
        raise UIProfileStopped('instagram_package_metadata_ambiguous')
    names = re.findall(r'^\s*versionName=([^\s]+)', sections[0], re.M)
    codes = re.findall(r'^\s*versionCode=(\d+)\b', sections[0], re.M)
    if len(names) != 1 or len(codes) != 1:
        raise UIProfileStopped('instagram_package_metadata_ambiguous')
    value = AppMetadata(package, names[0], int(codes[0]))
    resolve(value, package)
    return value


def read_metadata(serial, package):
    """Existing bounded ADB transport, same exact instance, no retry."""
    if not serial or not PACKAGE.fullmatch(package or ''):
        raise UIProfileStopped('instagram_app_instance_unproved')
    from device import _adb_run
    token = _METADATA_READ.set(True)
    try:
        code, output, _ = _adb_run(serial, ['shell', 'dumpsys', 'package', package])
        if code:
            raise UIProfileStopped('instagram_package_metadata_read_failed')
        return parse_installed_package(output, package)
    except Exception as exc:
        raise UIProfileStopped('instagram_package_metadata_read_failed') from exc
    finally:
        _METADATA_READ.reset(token)


@dataclass(frozen=True)
class RunUIBinding:
    account_id: str
    request_id: str
    run_id: str
    root_business_session_id: str
    attempt_id: int
    device_id: str
    serial: str
    app_instance_id: str
    package: str
    version_name: str
    version_code: int
    ui_profile: str
    lease_id: str

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key not in ('attempt_id', 'version_code') and (type(value) is not str or not value.strip()):
                raise UIProfileStopped('instagram_ui_binding_missing:' + key)
        if type(self.attempt_id) is not int or self.attempt_id < 1:
            raise UIProfileStopped('instagram_ui_attempt_unproved')
        if resolve(AppMetadata(self.package, self.version_name, self.version_code), self.package) != self.ui_profile:
            raise UIProfileStopped('instagram_ui_profile_mismatch')

    def to_dict(self):
        return asdict(self)


def from_dict(value):
    if type(value) is not dict or set(value) != {f.name for f in fields(RunUIBinding)}:
        raise UIProfileStopped('instagram_ui_binding_unproved')
    try:
        return RunUIBinding(**value)
    except (TypeError, ValueError) as exc:
        raise UIProfileStopped('instagram_ui_binding_unproved') from exc


def validate_parent(parent, binding, prior_run_id):
    old = from_dict(parent)
    if old.run_id != prior_run_id or old.attempt_id >= binding.attempt_id:
        raise UIProfileStopped('instagram_ui_resume_lineage_mismatch')
    for key in ('account_id', 'root_business_session_id', 'device_id', 'serial',
                'app_instance_id', 'package', 'version_name', 'version_code', 'ui_profile'):
        if getattr(old, key) != getattr(binding, key):
            raise UIProfileStopped('instagram_ui_resume_profile_mismatch')
    return old


class _Session:
    def __init__(self, binding, reader, parent):
        self.binding = binding
        self.original = binding
        self.reader = reader
        self.parent = from_dict(parent).to_dict() if parent is not None else None
        self.reason = None
        self.device = None
        self.context = None

    def stop(self, reason):
        if self.reason is None:
            self.reason = reason
        raise UIProfileStopped(self.reason)

    def check(self, device=None):
        if self.reason:
            self.stop(self.reason)
        if self.binding is not self.original:
            self.stop('instagram_ui_binding_replaced')
        if device is not None and device is not self.device:
            self.stop('instagram_ui_device_replaced')
        import config
        if config.INSTAGRAM_PACKAGE != self.binding.package:
            self.stop("instagram_ui_package_changed")
        if self.device is not None:
            from device import get_device_serial
            if get_device_serial(self.device) != self.binding.serial:
                self.stop("instagram_ui_device_serial_mismatch")
            values = vars(self.device)
            b = self.binding
            expected = (id(self.device), b.serial, b.package, b.version_name, b.version_code)
            if values.get('_instagram_search_observed_version') != expected:
                self.stop('instagram_ui_version_context_changed')
            key = '_instagram_446_observed_context'
            if b.ui_profile == 'UI_PROFILE_446':
                if values.get(key) is not self.context or self.context is None:
                    self.stop('instagram_ui_446_context_changed')
            elif key in values:
                self.stop('instagram_ui_cross_version_context')

    def revalidate(self):
        self.check()
        try:
            value = self.reader()
            if value != AppMetadata(self.binding.package, self.binding.version_name, self.binding.version_code):
                self.stop('instagram_version_changed_during_run')
            resolve(value, self.binding.package)
        except UIProfileStopped as exc:
            self.stop(str(exc))
        except Exception:
            self.stop('instagram_package_metadata_read_failed')


def armed():
    return _SESSION is not None


def snapshot():
    return _SESSION.original.to_dict() if _SESSION is not None else None


def arm(binding, reader, *, parent=None):
    global _SESSION
    with _LOCK:
        if _SESSION is not None:
            _SESSION.stop('instagram_ui_binding_already_bound')
        if type(binding) is not RunUIBinding:
            raise UIProfileStopped("instagram_ui_binding_unproved")
        _SESSION = _Session(binding, reader, parent)
        _SESSION.revalidate()
    return binding


def attach(device):
    if _SESSION is None:
        raise UIProfileStopped('instagram_ui_binding_required')
    _SESSION.check()
    if _SESSION.device is not None:
        _SESSION.stop('instagram_ui_device_already_bound')
    from device import get_device_serial
    if get_device_serial(device) != _SESSION.binding.serial:
        _SESSION.stop('instagram_ui_device_serial_mismatch')
    _SESSION.device = device
    b = _SESSION.binding
    values = vars(device)
    if '_instagram_446_observed_context' in values or '_instagram_search_observed_version' in values:
        _SESSION.stop('instagram_ui_preexisting_device_context')
    if b.ui_profile == 'UI_PROFILE_446':
        from instagram_ui_446_compat import retain_context, CONTEXT_KEY
        retain_context(device, serial=b.serial, package=b.package,
                       version_name=b.version_name, version_code=b.version_code, unsafe=False)
        _SESSION.context = values[CONTEXT_KEY]
    values['_instagram_search_observed_version'] = (id(device), b.serial, b.package, b.version_name, b.version_code)
    _SESSION.check(device)
    return device


def validate_lock_observation(device, serial, package, version_name, version_code, unsafe):
    """Consume the legacy lock result without replacing the selected profile."""
    if _SESSION is None:
        return
    _SESSION.check(device)
    b = _SESSION.binding
    if unsafe or (serial, package, version_name, version_code) != (b.serial, b.package, b.version_name, b.version_code):
        _SESSION.stop('instagram_ui_lock_observation_unproved')


def retention_allowed(device, serial, package, version_name, version_code, unsafe):
    """Pure validation for the already selected 446 context, no extra I/O."""
    if _SESSION is None:
        return
    _SESSION.check() if _SESSION.context is not None else None
    b = _SESSION.binding
    if (device is not _SESSION.device or unsafe
            or (serial, package, version_name, version_code) != (b.serial, b.package, b.version_name, b.version_code)
            or b.ui_profile != 'UI_PROFILE_446'):
        _SESSION.stop('instagram_ui_retained_profile_mismatch')


def begin_dispatch(device=None):
    if _SESSION is None:
        return
    _SESSION.check(device)
    if _SESSION.device is None:
        _SESSION.stop('instagram_ui_device_not_bound')
    if _DEPTH.get() == 0:
        _SESSION.revalidate()
    _DEPTH.set(_DEPTH.get() + 1)


def end_dispatch():
    if _SESSION is not None:
        _DEPTH.set(max(0, _DEPTH.get() - 1))


def check_adb(serial, argv):
    if _SESSION is None:
        return
    if serial != _SESSION.binding.serial:
        _SESSION.stop('instagram_ui_adb_serial_mismatch')
    if _METADATA_READ.get():
        if list(argv) != ['shell', 'dumpsys', 'package', _SESSION.binding.package]:
            _SESSION.stop('instagram_ui_metadata_command_mismatch')
        return
    _SESSION.check()
    if _DEPTH.get() == 0:
        _SESSION.revalidate()


def check_device(device):
    if _SESSION is not None:
        _SESSION.check(device)


@contextmanager
def dispatch(device):
    begin_dispatch(device)
    try:
        yield
    finally:
        end_dispatch()


def carry_plan(plan, binding=_CURRENT):
    """Add only metadata to the existing plan; never change business policy."""
    result = dict(plan)
    value = snapshot() if binding is _CURRENT else binding
    if value is None and binding is _CURRENT:
        return result
    current = from_dict(value).to_dict()
    if PLAN_KEY in result and result[PLAN_KEY] != current:
        if _SESSION is None or binding is not _CURRENT or result[PLAN_KEY] != _SESSION.parent:
            raise UIProfileStopped('instagram_ui_plan_binding_changed')
    result[PLAN_KEY] = current
    return result
