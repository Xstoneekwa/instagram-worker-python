"""Private same-observation evidence for the existing signed DM canary only.

No transport, backend call, screenshot or background writer. Serialization and
disk IO occur after the classifier has fixed its final decision.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

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
        if self.finished or len(self.rows) >= 9:
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
