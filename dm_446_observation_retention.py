"""Private same-observation evidence for signed canaries and ordinary refusals.

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
        return None
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
    """Persist an already-decided refusal; never read UI or alter its outcome."""
    global _ordinary_refusal_attempt
    if (snapshot.get('thread_state') != 'unknown'
            or observation.get('observation_reason') != 'composer_unproved'
            or (observation.get('composer') or {}).get('visible') is not False):
        return {'status': 'NOT_APPLICABLE'}
    fds = []
    run_id = None
    try:
        from run_latency_observability_v1 import current_context
        # Existing runner-correlated identity only; no message, lease or permit.
        correlated = current_context()
        run_id = correlated.get('run_id')
        request_id = correlated.get('request_id')
        worker_sha = correlated.get('worker_sha')
        if not all(type(v) is str and v for v in (run_id, request_id, worker_sha)):
            return {'status': 'UNAVAILABLE_CONTEXT'}
        try:
            identifiers_exact = str(UUID(run_id)) == run_id and str(UUID(request_id)) == request_id
        except ValueError:
            return {'status': 'UNAVAILABLE_CONTEXT'}
        if (not identifiers_exact or len(worker_sha) != 40
                or any(c not in '0123456789abcdef' for c in worker_sha)):
            return {'status': 'UNAVAILABLE_CONTEXT'}
        attempt = (os.getpid(), run_id)
        if _ordinary_refusal_attempt == attempt:
            return {'status': 'ALREADY_ATTEMPTED'}
        _ordinary_refusal_attempt = attempt
        raw = observation.get('xml')
        if type(raw) is not str:
            return {'status': 'UNAVAILABLE_XML', 'run_id': run_id}
        payload = raw.encode('utf-8')
        if len(payload) > _MAX_COMPOSER_XML_BYTES:
            return {'status': 'UNAVAILABLE_SIZE_LIMIT', 'run_id': run_id}
        context = {k: correlated[k] for k in (
            'run_id', 'request_id', 'worker_sha', 'account_id', 'device_id',
            'app_instance_id') if k in correlated}
        context.update(package=package,
                       version_name=observation.get('version_name'),
                       version_code=observation.get('version_code'),
                       display_size=observation.get('display_size'))
        fd = _root()
        fds.append(fd)
        fd = _directory(fd, run_id, private=True, create=True)
        fds.append(fd)
        name = 'ordinary-first-composer-refusal'
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
        except FileExistsError:
            return {'status': 'ALREADY_ATTEMPTED', 'run_id': run_id}
        fd = _directory(fd, name, private=True)
        fds.append(fd)
        xml_hash = hashlib.sha256(payload).hexdigest()
        _write(fd, 'observation_01.xml', payload)
        row = {k: observation.get(k) for k in (
            'observed_at', 'dump_returned', 'foreground_before', 'foreground_after',
            'observation_status', 'observation_reason', 'composer', 'classifier')}
        row.update(index=1, read_index=snapshot.get('number_of_reads'),
                   xml_path='observation_01.xml', xml_sha256=xml_hash)
        receipt = dict(schema='DM446_EXACT_OBSERVATION_V1',
                       capture_policy='FIRST_ORDINARY_COMPOSER_UNPROVED_PER_RUN',
                       recorded_at=datetime.now(timezone.utc).isoformat(),
                       context=context, observations=[row],
                       runtime={k: snapshot.get(k) for k in (
                           'thread_state', 'reason', 'composer_signal',
                           'number_of_reads', 'positive_empty_observations', 'elapsed_s')})
        _write(fd, 'receipt.json', (json.dumps(receipt, sort_keys=True,
               ensure_ascii=True, allow_nan=False, indent=2) + '\n').encode('utf-8'))
        for directory in reversed(fds):
            os.fsync(directory)
        return {'status': 'RETAINED', 'run_id': run_id, 'xml_sha256': xml_hash}
    except Exception:
        # A failed diagnostic never replaces the original admission refusal.
        return {'status': 'UNAVAILABLE_STORAGE'}
    finally:
        for directory in reversed(fds):
            try:
                os.close(directory)
            except OSError:
                pass



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
        return None
    return ComposerRecord(buffer)


class ComposerRecord:
    def __init__(self, buffer):
        self.buffer = buffer
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
