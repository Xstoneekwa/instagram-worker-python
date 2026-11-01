"""Private same-observation evidence for signed canaries and scoped certification.

No transport, backend call, screenshot or background writer. Serialization and
disk IO occur after the classifier has fixed its final decision.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import UUID, uuid4

from certification_admission import CertificationStop


def begin(package, recipient):
    import dm_certification_admission as admission
    if not admission.is_present():
        return _ordinary_thread_record(package, recipient)
    try:
        session = admission.current()
        claims = admission.validate_claims(session['claims'])
        binding = admission.validate_binding(claims, session['binding'])
        if (package, recipient) != (claims['package'], claims['recipient_username']):
            raise ValueError('context')
        # Select values; never copy the session, message body or lease token.
        context = {k: claims[k] for k in (
            'account_id', 'device_id', 'app_instance_id', 'serial', 'package',
            'version_name', 'version_code', 'recipient_username', 'permit_id',
            'candidate_sha', 'candidate_tree')}
        context.update({k: binding[k] for k in ('run_id', 'request_id', 'execution_id')})
        return Recorder(context)
    except Exception:
        raise CertificationStop('dm_exact_observation_context_unproved') from None


def _directory(parent, name, *, private=False, create=False):
    if not name or name in ('.', '..') or '/' in name:
        raise ValueError('component')
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    info = os.fstat(fd)
    mode = stat.S_IMODE(info.st_mode)
    # Root-owned sticky temporary ancestors are safe for descriptor-relative
    # traversal; another user's directory or a writable non-sticky host is not.
    unsafe_host = (info.st_uid not in (0, os.getuid()) or
                   bool(mode & 0o022) and not (info.st_uid == 0 and mode & stat.S_ISVTX))
    if unsafe_host or private and (info.st_uid != os.getuid() or mode != 0o700):
        os.close(fd)
        raise ValueError('private_directory')
    return fd


def _root():
    from runtime_artifacts import runtime_logs_root
    # runtime_logs_root resolves paths. Walk the lexical input as well so a
    # symlink cannot disappear through resolve() before the no-follow check.
    configured = (os.environ.get('PHONEFARM_RUNTIME_ROOT') or
                  os.environ.get('PHONEFARM_RUNTIME_HOME') or
                  str(Path.home() / 'phonefarm-runtime'))
    lexical = Path(configured).expanduser()
    if not lexical.is_absolute() or '..' in lexical.parts:
        raise ValueError('runtime_root')
    target = lexical / 'logs'
    if target != runtime_logs_root():
        raise ValueError('resolved_root_changed')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in target.parts[1:]:
            child = _directory(fd, name)
            os.close(fd)
            fd = child
        child = _directory(fd, 'dm446-exact-observations', private=True, create=True)
        os.close(fd)
        fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write(parent, name, payload):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


class Recorder:
    def __init__(self, context):
        self.context = context
        self.rows = []
        self.finished = False

    def observation(self):
        if self.finished or len(self.rows) >= 10:
            raise CertificationStop('dm_exact_observation_sequence_invalid')
        row = dict(index=len(self.rows) + 1,
                   observed_at=datetime.now(timezone.utc).isoformat(), xml=None,
                   dump_returned=False, foreground_before=None, foreground_after=None,
                   classifier=None, observation_status='OBSERVATION_REJECTED',
                   observation_reason='observation_interrupted')
        self.rows.append(row)
        return row

    def finish(self, snapshot):
        if self.finished:
            raise CertificationStop('dm_exact_observation_repeated_finish')
        self.finished = True
        fds = []
        try:
            fd = _root()
            fds.append(fd)
            for key in ('run_id', 'execution_id'):
                fd = _directory(fd, self.context[key], private=True, create=True)
                fds.append(fd)
            # No previous invocation is overwritten or merged into this one.
            name = uuid4().hex
            os.mkdir(name, 0o700, dir_fd=fd)
            fd = _directory(fd, name, private=True)
            fds.append(fd)
            observations = []
            for row in self.rows:
                item = {k: v for k, v in row.items() if k != 'xml'}
                raw = row['xml']
                item.update(xml_path=None, xml_sha256=None)
                if raw is not None:
                    if type(raw) is not str:
                        raise ValueError('xml_type')
                    payload = raw.encode('utf-8')
                    filename = 'observation_%02d.xml' % row['index']
                    _write(fd, filename, payload)
                    item.update(xml_path=filename, xml_sha256=hashlib.sha256(payload).hexdigest())
                observations.append(item)
            receipt = dict(schema='DM446_EXACT_OBSERVATION_V1', context=self.context,
                           observations=observations,
                           runtime={k: snapshot.get(k) for k in (
                               'thread_state', 'reason', 'number_of_reads',
                               'positive_empty_observations', 'elapsed_s')})
            _write(fd, 'receipt.json', (json.dumps(receipt, sort_keys=True,
                     ensure_ascii=True, allow_nan=False, indent=2) + '\n').encode())
            for directory in reversed(fds):
                os.fsync(directory)
        except Exception:
            raise CertificationStop('dm_exact_observation_persistence_failed') from None
        finally:
            for directory in reversed(fds):
                os.close(directory)


# Composer evidence is passive, bounded and drained only outside runner.main.
# No retained UiObject, backend payload, text or lease secret.
_COMPOSER_FIELDS = ('resourceName', 'className', 'enabled', 'clickable',
                    'focusable', 'focused', 'bounds')
_MAX_COMPOSER_INVOCATIONS = 128
_MAX_COMPOSER_XML_BYTES = 32 * 1024 * 1024
_composer_buffer = None

# Ordinary runs share the private exact-observation writer, not canary authority.
# One runner process owns one RUN; the exclusive packet directory also prevents
# a second packet from another invocation/process for that same RUN.
_ordinary_refusal_attempt = None


def retain_first_ordinary_refusal(package, observation, snapshot):
    """The ordinary first refusal is retained by its scoped session, off-path."""
    buffer = _ordinary_buffer()
    if (buffer is None or package != buffer['context']['package']
            or snapshot.get('thread_state') != 'unknown'
            or observation.get('observation_reason') != 'composer_unproved'
            or (observation.get('composer') or {}).get('visible') is not False):
        return {'status': 'NOT_APPLICABLE'}
    capture_event('initial_composer_refusal', failure=True,
                  reason=snapshot.get('reason'), composer_signal=snapshot.get('composer_signal'))
    return {'status': 'BUFFERED_CERTIFICATION', 'run_id': buffer['context']['run_id']}



def activate_composer(package, recipient):
    global _composer_buffer
    if _composer_buffer is not None:
        raise CertificationStop('dm_composer_retention_already_active')
    recorder = begin(package, recipient)
    if recorder is None:
        raise CertificationStop('dm_composer_retention_context_unproved')
    from threading import get_ident
    _composer_buffer = dict(context=recorder.context, owner=get_ident(), records=[],
                            xml_bytes=0, incomplete=False, invocations=0)


def begin_composer(package):
    buffer = _composer_buffer
    if buffer is None:
        return None
    from threading import get_ident
    if package != buffer['context']['package'] or get_ident() != buffer['owner']:
        buffer['incomplete'] = True
        return None
    buffer['invocations'] += 1
    if buffer['invocations'] > _MAX_COMPOSER_INVOCATIONS:
        buffer['incomplete'] = True
        if not buffer.get('ordinary_certification'):
            return None
    return ComposerRecord(buffer)


class ComposerRecord:
    def __init__(self, buffer):
        self.buffer = buffer
        self.phase = buffer.get('phase', 'composer_qualification')
        self.job_id = buffer.get('job_id')
        self.rows = []
        self.first = self.second = self.selector_count = None
        self.live = self.expected = self.result = None
        self.comparison_fields = []
        self.first_failed_field = None
        self.final_selector_comparison = None
        self.exception_class = None
        self.exception_step = None
        self.invalid = False

    def record_exception(self, step, exc):
        if step in ("selector_count", "selector_info", "compare_live") and self.exception_step is None:
            self.exception_step = step
            self.exception_class = type(exc).__name__

    def observation(self):
        row = {}
        self.rows.append(row)
        return row

    def capture_live(self, info, rid, bounds):
        try:
            # Read no property here. info is the already-returned dictionary.
            if type(info) is not dict:
                self.invalid = True
                return
            self.live = {k: (dict(info[k]) if k == 'bounds' and type(info[k]) is dict
                             else info[k]) for k in _COMPOSER_FIELDS if k in info}
            self.expected = dict(resourceName=rid, className='android.widget.EditText',
                                 enabled=True, clickable=True, focusable=True,
                                 bounds=dict(bounds))
        except Exception:
            self.invalid = True

    def compare_live(self, info, rid, bounds):
        """Retain the exact already-read comparator inputs; never drive the verdict."""
        try:
            fields = ('resourceName', 'className', 'enabled', 'clickable', 'focusable', 'bounds')
            expected = self.expected or dict(resourceName=rid, className='android.widget.EditText',
                                             enabled=True, clickable=True, focusable=True,
                                             bounds=dict(bounds))
            if type(info) is not dict:
                self.final_selector_comparison = 'OBSERVATION_EXCEPTION'
                return
            details = []
            for key in fields:
                expected_present = key in expected
                live_present = key in info
                expected_value = expected.get(key)
                live_value = info.get(key)
                normalized_expected = dict(expected_value) if key == 'bounds' and type(expected_value) is dict else expected_value
                normalized_live = dict(live_value) if key == 'bounds' and type(live_value) is dict else live_value
                if key in ('enabled', 'clickable', 'focusable'):
                    equal = expected_present and live_present and normalized_expected is True and normalized_live is True
                else:
                    equal = expected_present and live_present and normalized_expected == normalized_live
                details.append(dict(field=key, expected_present=expected_present, expected_value=expected_value,
                                    live_present=live_present, live_value=live_value,
                                    normalized_expected=normalized_expected, normalized_live=normalized_live,
                                    equal=bool(equal)))
                if self.first_failed_field is None and not equal:
                    self.first_failed_field = key
            self.comparison_fields = details
            if self.first_failed_field is None:
                self.final_selector_comparison = 'MATCH'
            elif not next(item for item in details if item['field'] == self.first_failed_field)['live_present']:
                self.final_selector_comparison = 'FIELD_ABSENT'
            else:
                self.final_selector_comparison = 'FIELD_MISMATCH'
        except Exception as exc:
            self.record_exception("compare_live", exc)
            self.final_selector_comparison = 'OBSERVATION_EXCEPTION'
            self.invalid = True

    def complete(self, result):
        try:
            # Do not retain result[3]: that is a live UiObject.
            self.result = None if result is None else result[:3]
            if self.buffer.get('ordinary_certification'):
                _keep_composer_record(self.buffer, self)
                return
            # Conservative UTF-8 upper bound without encoding/hashing in UI path.
            size = sum(4 * len(r['xml']) for r in self.rows if type(r.get('xml')) is str)
            if self.invalid or self.buffer['xml_bytes'] + size > _MAX_COMPOSER_XML_BYTES:
                self.buffer['incomplete'] = True
                return
            self.buffer['xml_bytes'] += size
            self.buffer['records'].append(self)
        except Exception:
            self.buffer['incomplete'] = True

    def receipt(self):
        result, live, expected = self.result, self.live or {}, self.expected or {}
        failed = None
        context_stages = ('foreground_before_invalid', 'foreground_after_changed',
                          'version_context_invalid')
        if result and result[0]:
            failed = 'NONE'
        elif self.first is None:
            stage = self.rows[0].get('observation_reason') if self.rows else None
            failed = 'foreground_or_context_changed' if stage in context_stages else 'observation_unqualified'
        elif not self.first['visible']:
            failed = 'observation_unqualified'
        elif self.selector_count is not None and self.selector_count != 1:
            failed = 'selector_not_unique'
        elif self.live is not None:
            for key, reason in (('resourceName', 'resource_name_mismatch'),
                                ('className', 'class_mismatch'), ('enabled', 'enabled_mismatch'),
                                ('clickable', 'clickable_mismatch'), ('focusable', 'focusable_mismatch'),
                                ('bounds', 'bounds_mismatch')):
                valid = live.get(key) is expected[key] if key in ('enabled', 'clickable', 'focusable') else live.get(key) == expected[key]
                if not valid:
                    failed = reason
                    break
            if failed is None:
                if self.second is None:
                    stage = self.rows[-1].get('observation_reason') if self.rows else None
                    failed = 'foreground_or_context_changed' if stage in context_stages else 'second_observation_unqualified'
                elif not self.second['visible']:
                    failed = 'second_observation_unqualified'
                elif self.second != self.first:
                    failed = 'second_observation_drift'
        final_selector_comparison = self.final_selector_comparison
        if final_selector_comparison is None:
            if self.invalid or (result and len(result) > 1 and result[1] == 'dm_composer_observation_unproved'):
                final_selector_comparison = 'OBSERVATION_EXCEPTION'
            elif self.selector_count == 0:
                final_selector_comparison = 'SELECTOR_ABSENT'
            elif self.selector_count is not None and self.selector_count != 1:
                final_selector_comparison = 'SELECTOR_AMBIGUOUS'
        return dict(first_failed_predicate=failed or 'other_exact_existing_reason',
                    composer_result=bool(result and result[0]),
                    composer_reason=result[1] if result else None,
                    first=self.first, second=self.second, selector_count=self.selector_count,
                    live=live, live_present_fields=[k for k in _COMPOSER_FIELDS if k in live],
                    expected=expected, comparison_fields=self.comparison_fields,
                    first_failed_field=self.first_failed_field,
                    final_selector_comparison=final_selector_comparison,
                    exception_class=self.exception_class,
                    exception_step=self.exception_step,
                    second_selector_not_unique='NOT_APPLICABLE',
                    budget_exhausted='NOT_EVALUATED_BY_THIS_HELPER')


def flush_composer():
    """Always clear context; persistence failure never replaces runner outcome."""
    global _composer_buffer
    buffer, _composer_buffer = _composer_buffer, None
    if buffer is None:
        return 'INACTIVE'
    if buffer.get('ordinary_certification'):
        return _flush_ordinary_composer(buffer)
    fds = []
    try:
        fd = _root()
        fds.append(fd)
        for key in ('run_id', 'execution_id'):
            fd = _directory(fd, buffer['context'][key], private=True, create=True)
            fds.append(fd)
        name = 'composer-' + uuid4().hex
        os.mkdir(name, 0o700, dir_fd=fd)
        fd = _directory(fd, name, private=True)
        fds.append(fd)
        records = []
        for index, record in enumerate(buffer['records'], 1):
            item = record.receipt()
            item['observations'] = []
            for observation, row in enumerate(record.rows, 1):
                kept = {k: v for k, v in row.items() if k != 'xml'}
                kept.update(xml_path=None, xml_sha256=None)
                if row.get('xml') is not None:
                    payload = row['xml'].encode('utf-8')
                    filename = 'composer_%03d_observation_%d.xml' % (index, observation)
                    _write(fd, filename, payload)
                    kept.update(xml_path=filename, xml_sha256=hashlib.sha256(payload).hexdigest())
                item['observations'].append(kept)
            records.append(item)
        status = 'UNPROVEN' if buffer['incomplete'] else 'RETAINED'
        receipt = dict(schema='DM446_COMPOSER_LIVE_EVIDENCE_V1', context=buffer['context'],
                       evidence_status=status, invocations=buffer['invocations'], records=records)
        _write(fd, 'receipt.json', (json.dumps(receipt, sort_keys=True, ensure_ascii=True,
                    allow_nan=False, indent=2) + '\n').encode())
        for directory in reversed(fds):
            os.fsync(directory)
        return status
    except Exception:
        return 'UNPROVEN'
    finally:
        for directory in reversed(fds):
            os.close(directory)


# Ordinary certification uses the same buffer, records and private writer as the
# historical canary. This switch is diagnostic only; it grants no run/Send right.
_CERTIFICATION_ENV = 'DM446_CERTIFICATION_RECORDER_ENABLED'
_CERTIFICATION_SCOPE = dict(
    account_id='e4c204e5-1d70-44ba-8e06-8afc1167bc0b',
    device_id='d0f26bfe-5bb1-46c0-af62-dce21be5e8e4',
    app_instance_id='7f84f24b-1ad7-4839-adff-7b9639fb91b2',
    serial='RFGL62SZ7NR', package='com.instagram.android')
_MAX_CERTIFICATION_DISK_BYTES = 256 * 1024 * 1024
_MAX_CERTIFICATION_ENTRIES = 4096
_MAX_CERTIFICATION_EVENTS = 128


def _ordinary_buffer():
    from threading import get_ident
    value = _composer_buffer
    if (isinstance(value, dict) and value.get('ordinary_certification') is True
            and value.get('owner') == get_ident()):
        return value
    return None


def _scope_context(d, account_id, run_id):
    """Consume already-admitted slot/version/run context; no device/DB read."""
    if os.environ.get(_CERTIFICATION_ENV, '').strip().lower() not in ('1', 'true', 'yes', 'on'):
        return None
    import dm_certification_admission as admission
    if admission.is_present():
        return None  # Never manufacture or reinterpret historical canary authority.
    from outreach_slot_contract import active
    from run_latency_observability_v1 import current_context
    from instagram_navigation import _dm_446_context
    slot, correlated, version = active(), current_context(), _dm_446_context(d)
    if slot is None or version is None or account_id != _CERTIFICATION_SCOPE['account_id']:
        return None
    c = slot.context
    if any(c.get(k) != _CERTIFICATION_SCOPE[k] for k in ('account_id', 'device_id', 'app_instance_id')):
        return None
    if (c.get('package_name') != _CERTIFICATION_SCOPE['package']
            or c.get('adb_serial') != _CERTIFICATION_SCOPE['serial']
            or version.version != ('446.0.0.49.77', 385211304)
            or version.serial != c.get('adb_serial') or version.package != c.get('package_name')
            or c.get('managed_campaign') is not True
            or correlated.get('account_id') != account_id
            or correlated.get('run_id') != run_id
            or correlated.get('request_id') != c.get('request_id')):
        return None
    for identifier in (run_id, c.get('request_id')):
        if type(identifier) is not str or str(UUID(identifier)) != identifier:
            return None
    worker = correlated.get('worker_sha')
    if type(worker) is not str or len(worker) != 40 or any(x not in '0123456789abcdef' for x in worker):
        return None
    return dict(_CERTIFICATION_SCOPE, run_id=run_id, request_id=c['request_id'],
                execution_id=c['request_id'], worker_sha=worker,
                version_name=version.version[0], version_code=version.version[1],
                run_type='outreach_session', capture_policy='ORDINARY_DM446_CERTIFICATION')


def certification_session(d, *, account_id, run_id):
    """Persistent opt-in, new bounded packet per admitted RUN; default OFF."""
    from contextlib import contextmanager

    @contextmanager
    def session():
        global _composer_buffer
        owned = None
        try:
            context = _scope_context(d, account_id, run_id)
            if context is not None and _composer_buffer is None:
                from threading import get_ident
                owned = dict(context=context, owner=get_ident(), ordinary_certification=True,
                             records=[], xml_bytes=0, incomplete=False, invocations=0,
                             phase='composer_qualification', job_id=None, events=[],
                             thread_records=[], native_xml=[], keys=set(), event_bytes=0)
                _composer_buffer = owned
        except Exception:
            pass  # Diagnostic eligibility cannot change business admission.
        if owned is None and account_id == _CERTIFICATION_SCOPE['account_id'] and os.environ.get(_CERTIFICATION_ENV, '').lower() in ('1', 'true', 'yes', 'on'):
            try:
                from logs import log
                log('warning', 'dm446_certification_evidence_retention',
                    status='UNAVAILABLE_CONTEXT', run_id=run_id)
            except Exception:
                pass
        try:
            yield
        finally:
            if owned is not None and _composer_buffer is owned:
                import sys
                error = sys.exc_info()[0]
                if error is not None:
                    capture_event('sender_exception', failure=True, exception_class=error.__name__)
                try:
                    status = flush_composer()
                except Exception:
                    _composer_buffer = None
                    status = 'UNPROVEN'
                try:
                    from logs import log
                    log('info', 'dm446_certification_evidence_retention',
                        status=status, run_id=owned['context']['run_id'],
                        request_id=owned['context']['request_id'],
                        worker_sha=owned['context']['worker_sha'])
                except Exception:
                    pass
    return session()


def phase(name):
    """Annotate existing calls; never drive a device or alter an exception."""
    from contextlib import contextmanager

    @contextmanager
    def scope():
        buffer = _ordinary_buffer()
        previous = buffer.get('phase') if buffer is not None else None
        if buffer is not None:
            buffer['phase'] = ((previous + '/') if previous else '') + name
        try:
            yield
        finally:
            if buffer is not None:
                import sys
                error = sys.exc_info()[0]
                if error is not None:
                    capture_event('phase_exception', failure=True, exception_class=error.__name__)
                buffer['phase'] = previous
    return scope()


def _plain(value, depth=0):
    # Never inspect a UiObject, exception properties, session, lease or policy.
    if depth > 5:
        return None
    if value is None or type(value) in (bool, int, float, str):
        return value
    if type(value) in (tuple, list):
        return [_plain(x, depth + 1) for x in value[:128]]
    if type(value) is dict:
        return {k: _plain(v, depth + 1) for k, v in list(value.items())[:64]
                if type(k) is str and k not in ('lease_id', 'lease_token', 'policy',
                    'claims', 'permit', 'password', 'message_body') and not k.startswith('text_prefix')}
    return None


def capture_event(name, *, failure=False, **fields):
    """In-memory only; first positive/negative per stage/job, no text in logs."""
    try:
        buffer = _ordinary_buffer()
        if buffer is None:
            return
        key = ('event', buffer.get('job_id'), buffer['phase'], name, bool(failure))
        if key in buffer['keys']:
            return
        if len(buffer['events']) >= _MAX_CERTIFICATION_EVENTS:
            buffer['incomplete'] = True
            if not failure:
                return
            # Preserve the first responsible failure instead of a redundant positive.
            victim = next((i for i in range(len(buffer['events']) - 1, -1, -1)
                           if not buffer['events'][i]['failure']), None)
            if victim is None:
                return
            buffer['event_bytes'] -= _text_size(buffer['events'].pop(victim))
        row = dict(stage=name, phase=buffer['phase'], job_id=buffer.get('job_id'),
                   observed_at=datetime.now(timezone.utc).isoformat(), failure=bool(failure),
                   fields=_plain(fields))
        # Strings are retained as references until off-path serialization/hashing.
        size = _text_size(row)
        if size > 1024 * 1024 or buffer['event_bytes'] + size > 4 * 1024 * 1024:
            buffer['incomplete'] = True
            return
        buffer['event_bytes'] += size
        buffer['keys'].add(key)
        buffer['events'].append(row)
    except Exception:
        pass


def bind_job(job_id, recipient):
    try:
        buffer = _ordinary_buffer()
        if buffer is not None:
            buffer['job_id'] = job_id
            capture_event('job_binding', recipient_username=recipient)
    except Exception:
        pass


def capture_backend_completion(job_id, row, *, outcome=None):
    try:
        import sys
        error = sys.exc_info()[0]
        value = ({k: row.get(k) for k in ('id', 'status', 'sent_at', 'attempts', 'max_attempts')}
                 if type(row) is dict else None)
        capture_event('backend_completion', failure=(error is not None or value is None
                      or type(outcome) is str and outcome.startswith('failed')),
                      job_id=job_id, returned_row=value, outcome=outcome,
                      primary_exception_class=error.__name__ if error else None)
    except Exception:
        pass


def _text_size(value):
    if type(value) is str:
        return 4 * len(value)
    if type(value) is dict:
        return sum(_text_size(v) for v in value.values())
    if type(value) in (list, tuple):
        return sum(_text_size(v) for v in value)
    return 32


def _keep_composer_record(buffer, record):
    key = ('composer', record.job_id, record.phase, bool(record.result and record.result[0]))
    if key in buffer['keys']:
        return
    size = sum(4 * len(r['xml']) for r in record.rows if type(r.get('xml')) is str)
    failure = not bool(record.result and record.result[0])
    if len(buffer['records']) >= _MAX_COMPOSER_INVOCATIONS:
        buffer['incomplete'] = True
        victim = next((x for x in reversed(buffer['records']) if x.result and x.result[0]), None)
        if not failure or victim is None:
            return
        buffer['records'].remove(victim)
        buffer['xml_bytes'] -= sum(4 * len(r['xml']) for r in victim.rows if type(r.get('xml')) is str)
    if buffer['xml_bytes'] + size > _MAX_COMPOSER_XML_BYTES and failure:
        # Existing memory only: keep the first refusal over later positive samples.
        while buffer['records'] and buffer['xml_bytes'] + size > _MAX_COMPOSER_XML_BYTES:
            victim = next((x for x in reversed(buffer['records']) if x.result and x.result[0]), None)
            if victim is None:
                break
            buffer['incomplete'] = True
            buffer['records'].remove(victim)
            buffer['xml_bytes'] -= sum(4 * len(r['xml']) for r in victim.rows if type(r.get('xml')) is str)
    if record.invalid or buffer['xml_bytes'] + size > _MAX_COMPOSER_XML_BYTES:
        buffer['incomplete'] = True
        return
    buffer['xml_bytes'] += size
    buffer['keys'].add(key)
    buffer['records'].append(record)


def capture_xml(label, xml, *, failure=False):
    """Keep only the XML already returned at this existing observation point."""
    try:
        buffer = _ordinary_buffer()
        if buffer is None or type(xml) is not str:
            return
        key = ('xml', buffer.get('job_id'), buffer['phase'], label, bool(failure))
        size = 4 * len(xml)
        if key in buffer['keys']:
            latest = next((row for row in buffer['native_xml']
                           if row.get('latest_for') == key), None)
            previous_size = 4 * len(latest['xml']) if latest is not None else 0
            if buffer['xml_bytes'] - previous_size + size > _MAX_COMPOSER_XML_BYTES:
                buffer['incomplete'] = True
                return
            buffer['xml_bytes'] += size - previous_size
            row = dict(label=label, phase=buffer['phase'], job_id=buffer.get('job_id'),
                       failure=bool(failure), xml=xml, latest_for=key,
                       observed_at=datetime.now(timezone.utc).isoformat())
            if latest is None:
                buffer['native_xml'].append(row)
            else:
                buffer['native_xml'][buffer['native_xml'].index(latest)] = row
            return
        if buffer['xml_bytes'] + size > _MAX_COMPOSER_XML_BYTES:
            buffer['incomplete'] = True
            return
        buffer['xml_bytes'] += size
        buffer['keys'].add(key)
        buffer['native_xml'].append(dict(label=label, phase=buffer['phase'],
            job_id=buffer.get('job_id'), failure=bool(failure), xml=xml,
            observed_at=datetime.now(timezone.utc).isoformat()))
    except Exception:
        pass


class _OrdinaryThreadRecorder(Recorder):
    def __init__(self, buffer):
        super().__init__(buffer['context'])
        self.buffer = buffer
        self.phase = buffer['phase']
        self.job_id = buffer.get('job_id')

    def observation(self):
        try:
            if len(self.rows) >= 10:
                self.buffer['incomplete'] = True
                self.rows.pop()  # Keep first comparisons and the current decisive read.
            return super().observation()
        except (Exception, CertificationStop):
            self.buffer['incomplete'] = True
            return {}

    def finish(self, snapshot):
        try:
            if self.finished:
                return
            self.finished = True
            for row in self.rows:
                raw = row.get('xml')
                if type(raw) is str:
                    size = 4 * len(raw)
                    if self.buffer['xml_bytes'] + size > _MAX_COMPOSER_XML_BYTES:
                        self.buffer['incomplete'] = True
                        row.pop('xml', None)
                    else:
                        self.buffer['xml_bytes'] += size
            if len(self.buffer['thread_records']) < _MAX_CERTIFICATION_EVENTS:
                self.buffer['thread_records'].append(dict(phase=self.phase, job_id=self.job_id,
                    observations=self.rows, runtime={k: snapshot.get(k) for k in (
                        'thread_state', 'reason', 'composer_signal', 'number_of_reads',
                        'positive_empty_observations', 'elapsed_s')}))
            else:
                self.buffer['incomplete'] = True
        except Exception:
            self.buffer['incomplete'] = True


def _ordinary_thread_record(package, recipient):
    try:
        buffer = _ordinary_buffer()
        if buffer is not None and package == buffer['context']['package'] and type(recipient) is str:
            return _OrdinaryThreadRecorder(buffer)
    except Exception:
        pass
    return None


def _storage_size(root):
    """Bounded off-path accounting only. Never delete old/open evidence."""
    total, entries, pending = 0, 0, [os.dup(root)]
    try:
        while pending:
            fd = pending.pop()
            try:
                with os.scandir(fd) as children:
                    for item in children:
                        entries += 1
                        if entries > _MAX_CERTIFICATION_ENTRIES:
                            raise ValueError('evidence_entry_limit')
                        info = item.stat(follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(_directory(fd, item.name, private=True))
                        elif stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600:
                            total += info.st_size
                        else:
                            raise ValueError('unclassified_evidence_keep')
                        if total > _MAX_CERTIFICATION_DISK_BYTES:
                            raise ValueError('evidence_volume_limit')
            finally:
                os.close(fd)
        return total
    finally:
        for fd in pending:
            os.close(fd)


def _receipt_fields(value):
    if type(value) is dict:
        result = {}
        for key, item in value.items():
            if key in ('actual_text', 'expected_text') and type(item) is str:
                result[key] = dict(characters=len(item), utf16_units=len(item.encode('utf-16-le')) // 2,
                                  sha256=hashlib.sha256(item.encode('utf-8')).hexdigest())
            else:
                result[key] = _receipt_fields(item)
        return result
    if type(value) is list:
        return [_receipt_fields(x) for x in value]
    return value


def _flush_ordinary_composer(buffer):
    """Existing protected writer after sender outcome, never in the UI path."""
    fds = []
    try:
        root = _root()
        fds.append(root)
        if _storage_size(root) + buffer['xml_bytes'] + buffer['event_bytes'] + 1024 * 1024 > _MAX_CERTIFICATION_DISK_BYTES:
            return 'UNPROVEN_VOLUME_LIMIT'
        fd = root
        for key in ('run_id', 'execution_id'):
            fd = _directory(fd, buffer['context'][key], private=True, create=True)
            fds.append(fd)
        name = 'composer-' + uuid4().hex
        os.mkdir(name, 0o700, dir_fd=fd)
        fd = _directory(fd, name, private=True)
        fds.append(fd)
        index = 0

        def observation(row):
            nonlocal index
            kept = {k: v for k, v in row.items() if k != 'xml'}
            kept.update(xml_path=None, xml_sha256=None)
            if type(row.get('xml')) is str:
                index += 1
                payload = row['xml'].encode('utf-8')
                filename = 'observation_%03d.xml' % index
                _write(fd, filename, payload)
                kept.update(xml_path=filename, xml_sha256=hashlib.sha256(payload).hexdigest())
            return kept

        records = []
        for record in buffer['records']:
            item = record.receipt()
            item.update(phase=record.phase, job_id=record.job_id,
                        observations=[observation(row) for row in record.rows])
            records.append(item)
        threads = [dict(phase=t['phase'], job_id=t['job_id'], runtime=t['runtime'],
                        observations=[observation(row) for row in t['observations']])
                   for t in buffer['thread_records']]
        receipt = dict(schema='DM446_COMPOSER_LIVE_EVIDENCE_V1', context=buffer['context'],
            evidence_status='UNPROVEN' if buffer['incomplete'] else 'RETAINED',
            invocations=buffer['invocations'], records=records, thread_records=threads,
            events=_receipt_fields(buffer['events']),
            native_observations=[observation(row) for row in buffer['native_xml']])
        _write(fd, 'receipt.json', (json.dumps(receipt, sort_keys=True, ensure_ascii=True,
                    allow_nan=False, indent=2) + '\n').encode('utf-8'))
        for directory in reversed(fds):
            os.fsync(directory)
        return receipt['evidence_status']
    except ValueError as error:
        return 'UNPROVEN_VOLUME_LIMIT' if error.args == ('evidence_volume_limit',) else 'UNPROVEN'
    except Exception:
        return 'UNPROVEN'
    finally:
        for directory in reversed(fds):
            try:
                os.close(directory)
            except OSError:
                pass
