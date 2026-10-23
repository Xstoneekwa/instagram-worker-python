"""V24 local reconciliation progress; never an execution/retry queue.

One finite keyset sweep per owner, one bounded page per invocation. Hold files
record invalid durable proof for operator review; missing proof has no deadline.
All processes for an owner must share this directory on the same runtime host.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone
from uuid import UUID

OPERATOR_ESCALATION_DELAY = 'UNRESOLVED_NOT_ARBITRARILY_SET'


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.checkpoint-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class InvalidReconciliationTimestamp(ValueError):
    pass


def parse_reconciliation_timestamp(value):
    """Finite PostgREST instant, UTC-aware, lossless through microseconds (Py3.9).

    Reuse the existing strict aware parser; reject unsupported precision rather
    than letting that helper truncate digits. Compact numeric offsets are only
    a representation change, not a timezone assumption.
    """
    from follow_persistence_rpc import parse_postgres_timestamp
    match = re.fullmatch(
        r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)(Z|[+-]\d{2}:?\d{2})',
        value if isinstance(value, str) else '')
    if not match:
        raise InvalidReconciliationTimestamp('reconciliation_timestamp_invalid_or_unsupported_format')
    local, offset = match.groups()
    if offset != 'Z':
        digits = offset[1:].replace(':', '')
        if int(digits[:2]) > 23 or int(digits[2:]) > 59:
            raise InvalidReconciliationTimestamp('reconciliation_timestamp_invalid_offset')
        offset = offset[0] + digits[:2] + ':' + digits[2:]
    try:
        parsed = parse_postgres_timestamp(local + offset)
    except (ValueError, OverflowError):
        parsed = None
    if parsed is None:
        raise InvalidReconciliationTimestamp('reconciliation_timestamp_invalid_calendar_or_range')
    return parsed


def key(row):
    identifier = str(UUID(row['id']))
    stamp = parse_reconciliation_timestamp(row.get('created_at'))
    return [stamp.isoformat(timespec='microseconds'), identifier]


def bound(position, *, lower):
    stamp, identifier = position
    key({'created_at': stamp, 'id': identifier})
    timestamp = json.dumps(key({'created_at': stamp, 'id': identifier})[0])
    op, tie = ('gt', 'gt') if lower else ('lt', 'lte')
    return f'or(created_at.{op}.{timestamp},and(created_at.eq.{timestamp},id.{tie}.{identifier}))'


class ReconciliationScan:
    def __init__(self, worker_id):
        self.owner = re.sub(r'[^a-zA-Z0-9_.:-]', '_', str(worker_id).strip() or 'run-dispatcher')[:160]
        root = Path(os.environ.get('RECONCILIATION_STATE_DIR', '/Users/admin/phonefarm-worker-runtime/reconciliation_v24'))
        self.directory = root / hashlib.sha256(self.owner.encode()).hexdigest()
        self.lock = None
        self.metrics = dict(rows_fetched=0, rows_evaluated=0, rows_validated=0,
                            rows_progressed=0, rows_deferred=0, rows_operator_required=0, high_water_rows_fetched=0, malformed_timestamp_rows=0, cursor_writes=0)

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.directory / 'scan.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            return None
        return self

    def __exit__(self, *args):
        if self.lock:
            self.lock.close()

    def fetch(self, read, query, limit, *, log=None):
        path = self.directory / 'cursor.json'
        state = json.loads(path.read_text()) if path.exists() else {'version': 1, 'cursor': None, 'upper': None}
        if state.get('version') != 1 or not all(k in state for k in ('cursor', 'upper')):
            raise ValueError('invalid_reconciliation_checkpoint_operator_required')
        mode = state.get('mode', 'time')
        if mode not in ('time', 'id_recovery'):
            raise ValueError('invalid_reconciliation_checkpoint_mode_operator_required')
        # Corrupt on-disk positions never silently switch ordering or reset.
        for field in ('cursor', 'upper'):
            if state[field] is not None:
                state[field] = (key({'created_at': state[field][0], 'id': state[field][1]})
                                if mode == 'time' else str(UUID(state[field])))
        if state['cursor'] is not None and state['upper'] is None:
            raise ValueError('cursor_without_upper_operator_required')
        query = {**query, 'claimed_by': 'eq.' + json.dumps(self.owner)}

        def checkpoint():
            atomic_json(path, state)
            self.metrics['cursor_writes'] += 1

        def malformed(row, exc):
            identifier = str(UUID(row['id']))
            self.metrics['malformed_timestamp_rows'] += 1
            if log:
                log('warning', 'reconciliation_timestamp_operator_required',
                    request_id=identifier, worker_id=self.owner, reason=str(exc),
                    business_retry=False, row_completed=False)

        # Recovery uses only typed UUIDs for ordering, never an invented timestamp.
        # It is a finite sweep, on later invocations, under the same owner/lock.
        position = key if mode == 'time' else lambda row: str(UUID(row['id']))
        row = None
        try:
            if state['upper'] is None:
                newest = read('GET', 'account_run_requests', query={**query,
                              'select': 'id,created_at' if mode == 'time' else 'id',
                              'order': 'created_at.desc,id.desc' if mode == 'time' else 'id.desc',
                              'limit': '1'}, request_timeout=5.0, max_retries=0) or []
                if not newest:
                    return []
                self.metrics['high_water_rows_fetched'] = len(newest)
                row = newest[0]
                state['upper'] = position(row)
            if mode == 'time':
                terms = [bound(state['upper'], lower=False)]
                if state['cursor'] is not None:
                    terms.append(bound(state['cursor'], lower=True))
            else:
                terms = ['id.lte.' + state['upper']]
                if state['cursor'] is not None:
                    terms.append('id.gt.' + state['cursor'])
            rows = read('GET', 'account_run_requests', query={**query, 'and': '(' + ','.join(terms) + ')',
                        'order': 'created_at.asc,id.asc' if mode == 'time' else 'id.asc',
                        'limit': str(limit)}, request_timeout=5.0, max_retries=0) or []
            if len(rows) > limit:
                raise ValueError('reconciliation_page_limit_exceeded')
            self.metrics['rows_fetched'] = len(rows)
            positions = []
            for row in rows:
                positions.append(position(row))
        except InvalidReconciliationTimestamp as exc:
            malformed(row, exc)
            # No inner requery: at most the normal upper/page queries this pass.
            # Record an explicit order transition rather than retrying this head.
            state = {'version': 1, 'mode': 'id_recovery', 'cursor': None, 'upper': None,
                     'reason': 'malformed_live_row_timestamp', 'request_id': str(UUID(row['id']))}
            checkpoint()
            return []

        state['cursor'] = positions[-1] if positions else None
        if len(rows) < limit or (positions and positions[-1] == state['upper']):
            state = {'version': 1, 'cursor': None, 'upper': None}
        # Crash after advance rechecks the page next cycle; never business replay.
        checkpoint()
        if mode == 'id_recovery':
            valid_rows = []
            for row in rows:
                try:
                    key(row)
                except InvalidReconciliationTimestamp as exc:
                    malformed(row, exc)
                    continue  # UUID cursor already advanced; cannot poison the head.
                valid_rows.append(row)
            return valid_rows
        return rows

    def hold_path(self, request_id, run_id):
        identity = json.dumps([request_id, run_id])
        return self.directory / ('invalid-' + hashlib.sha256(identity.encode()).hexdigest() + '.json')

    def held(self, request_id, run_id):
        return self.hold_path(request_id, run_id).exists()

    def hold(self, request_id, run_id, reason):
        atomic_json(self.hold_path(request_id, run_id), {
            'request_id': request_id, 'run_id': run_id, 'worker_id': self.owner,
            'state': 'OPERATOR_REQUIRED_INVALID_RECEIPT', 'reason': str(reason)[:200],
            'observed_at': datetime.now(timezone.utc).isoformat(), 'business_retry': False,
        })
