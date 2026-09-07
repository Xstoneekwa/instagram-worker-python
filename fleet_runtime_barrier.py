"""Fleet V1 local fencing primitives. No network, device or runtime activation.

The durable adapter must atomically persist snapshots before returning. Backend
admission and the consumer must register every execution owner/attempt before
spawn. These primitives alone do not implement that transport or certify ACTIVE
in production. A failed durable write poisons the owner until reconciliation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Any, Callable


class FleetBlocked(RuntimeError):
    pass


def _number(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise FleetBlocked("invalid_" + field)
    return value


def follow_allowance(normal: int, emergency: int | None, consumed: int) -> tuple[int, int]:
    normal = _number(normal, "normal_cap")
    consumed = _number(consumed, "canonical_consumed")
    cap = normal if emergency is None else min(normal, _number(emergency, "emergency_cap"))
    return cap, max(0, cap - consumed)


class FleetRuntimeBarrier:
    """One tenant/account, one shared local spawn mutex, durable owner registry.

    Hold observation and spawn are serialized with the same mutex. Spawn may
    win, but its registered attempt prevents an ACTIVE ACK until reconciliation.
    No persistence callback is allowed to perform device work or claim ACTIVE
    without the exact snapshot revision. No transport polling is created here.
    """

    def __init__(self, tenant_id: str, account_id: str,
                 persist: Callable[[dict[str, Any]], None],
                 restored: dict[str, Any] | None = None) -> None:
        if not tenant_id or not account_id or not callable(persist):
            raise FleetBlocked("missing_scope_or_durable_adapter")
        self._lock = threading.RLock()
        self._persist = persist
        self._failed = False
        self._s = copy.deepcopy(restored) if restored is not None else {
            "tenant_id": tenant_id, "account_id": account_id, "revision": 0,
            "hold": "CLEARED", "active_revision": None, "owners": {}, "attempts": {}, "receipts": {},
            "cap": None, "cap_state": "CAP_ACTIVE", "cap_acks": {},
        }
        if self._s["tenant_id"] != tenant_id or self._s["account_id"] != account_id:
            raise FleetBlocked("restore_scope_mismatch")
        _number(self._s["revision"], "revision")
        if self._s["hold"] not in ("REQUESTED", "ACTIVE", "RELEASING", "CLEARED"):
            raise FleetBlocked("invalid_restored_hold")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._s)

    def _healthy(self) -> None:
        if self._failed:
            raise FleetBlocked("durability_unavailable_reconciliation_required")

    def _save(self, value: dict[str, Any]) -> None:
        try:
            self._persist(copy.deepcopy(value))
        except BaseException:
            self._failed = True
            raise
        self._s = value

    def register_owner(self, owner: str) -> None:
        with self._lock:
            self._healthy()
            if not owner:
                raise FleetBlocked("missing_owner")
            if owner in self._s["owners"]:
                return
            value = self.snapshot()
            value["owners"][owner] = -1
            if value["hold"] == "ACTIVE":
                value["hold"] = "REQUESTED"
                value["active_revision"] = None
            self._save(value)

    def apply(self, key: str, *, expected_revision: int, operation: str,
              cap: int | None = None, package_max: int | None = None) -> dict[str, Any]:
        with self._lock:
            self._healthy()
            if not key or operation not in ("hold", "release", "cap", "clear_cap"):
                raise FleetBlocked("invalid_operation")
            payload = dict(operation=operation, cap=cap, package_max=package_max,
                           expected_revision=expected_revision)
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            prior = self._s["receipts"].get(key)
            if prior is not None:
                if prior["digest"] != digest:
                    raise FleetBlocked("idempotency_conflict")
                return copy.deepcopy(prior)
            if _number(expected_revision, "revision") != self._s["revision"]:
                raise FleetBlocked("stale_revision")
            if operation == "cap":
                if _number(cap, "emergency_cap") > _number(package_max, "package_max"):
                    raise FleetBlocked("cap_exceeds_package")
            elif cap is not None or package_max is not None:
                raise FleetBlocked("unexpected_cap_fields")
            value = self.snapshot()
            value["revision"] += 1
            value["active_revision"] = None
            if value["hold"] == "ACTIVE":
                value["hold"] = "REQUESTED"
            if operation == "hold":
                value["hold"] = "REQUESTED"
            elif operation == "release":
                value["hold"] = "RELEASING"
            else:
                value["cap"] = cap if operation == "cap" else None
                value["cap_state"] = "CAP_REQUESTED"
                value["cap_acks"] = {}
            # Every revision invalidates pending ACKs, including release races.
            value["owners"] = {owner: -1 for owner in value["owners"]}
            receipt = {"digest": digest, "revision": value["revision"],
                       "status": "WAITING_RUNTIME_ACK", "operation": operation}
            value["receipts"][key] = receipt
            self._save(value)
            return copy.deepcopy(receipt)

    def register_attempt(self, attempt: str, owner: str, revision: int,
                         historical_allowed: bool = True) -> None:
        with self._lock:
            self._healthy()
            if not historical_allowed:
                raise FleetBlocked("historical_eligibility_blocked")
            if owner not in self._s["owners"] or not attempt:
                raise FleetBlocked("unregistered_owner_or_attempt")
            if revision != self._s["revision"] or self._s["hold"] != "CLEARED":
                raise FleetBlocked("fleet_admission_fenced")
            if attempt in self._s["attempts"]:
                raise FleetBlocked("attempt_already_registered")
            value = self.snapshot()
            value["attempts"][attempt] = {"owner": owner, "revision": revision,
                                           "state": "ADMITTED", "pid": None}
            self._save(value)

    def spawn(self, attempt: str, spawn_child: Callable[[], Any]) -> Any:
        with self._lock:
            self._healthy()
            row = self._s["attempts"].get(attempt)
            if row is None or row["state"] != "ADMITTED":
                raise FleetBlocked("attempt_not_admitted")
            if row["revision"] != self._s["revision"] or self._s["hold"] != "CLEARED":
                raise FleetBlocked("fleet_spawn_fenced")
            value = self.snapshot()
            value["attempts"][attempt]["state"] = "STARTING"
            self._save(value)  # Crash here leaves a durable, non-ACKable attempt.
            child = spawn_child()
            value = self.snapshot()
            value["attempts"][attempt].update(state="STARTED", pid=child.pid)
            self._save(value)
            return child

    def reconcile_terminal(self, attempt: str, owner: str,
                           *, canonical_terminal: bool, child_gone: bool) -> None:
        with self._lock:
            self._healthy()
            row = self._s["attempts"].get(attempt)
            if row is None or row["owner"] != owner:
                raise FleetBlocked("attempt_owner_mismatch")
            if canonical_terminal is not True or child_gone is not True:
                raise FleetBlocked("terminal_proof_incomplete")
            if row["state"] == "TERMINAL":
                return
            value = self.snapshot()
            value["attempts"][attempt]["state"] = "TERMINAL"
            self._save(value)

    def acknowledge(self, owner: str, revision: int) -> str:
        with self._lock:
            self._healthy()
            if owner not in self._s["owners"] or revision != self._s["revision"]:
                raise FleetBlocked("stale_or_unknown_owner_ack")
            if any(a["owner"] == owner and a["state"] != "TERMINAL"
                   for a in self._s["attempts"].values()):
                raise FleetBlocked("unreconciled_execution_attempt")
            value = self.snapshot()
            value["owners"][owner] = revision
            if value["owners"] and all(v == revision for v in value["owners"].values()):
                if value["hold"] == "REQUESTED":
                    value["hold"] = "ACTIVE"
                    value["active_revision"] = revision
                elif value["hold"] == "RELEASING":
                    value["hold"] = "CLEARED"
                    value["active_revision"] = None
            self._save(value)
            return value["hold"]

    def safe_follow_boundary(self, owner: str, revision: int, *, normal_cap: int,
                             canonical_consumed: int, composite_reconciled: bool,
                             boundary: str = "before_candidate_selection") -> tuple[int, int]:
        with self._lock:
            self._healthy()
            if owner not in self._s["owners"] or revision != self._s["revision"]:
                raise FleetBlocked("stale_cap_ack")
            if boundary != "before_candidate_selection" or composite_reconciled is not True:
                raise FleetBlocked("unsafe_follow_cap_boundary")
            result = follow_allowance(normal_cap, self._s["cap"], canonical_consumed)
            if self._s["cap_state"] == "CAP_REQUESTED":
                value = self.snapshot()
                value["cap_acks"][owner] = revision
                if all(value["cap_acks"].get(o) == revision for o in value["owners"]):
                    value["cap_state"] = "CAP_ACTIVE"
                self._save(value)
            return result


class FleetRunnerBoundary:
    """Consume an already-transported canonical frame; never fetch authority.

    The consumer owns the channel and submits ACKs to the Backend separately.
    The canonical pre-run daily count must be supplied by atomic admission,
    not guessed from a partial counters payload. Frames are bound to one boot.
    """

    def __init__(self, *, account_id, request_id, owner_boot_id, read_local, write_ack):
        if not account_id or not request_id or not owner_boot_id:
            raise FleetBlocked("missing_runner_binding")
        self.binding = dict(account_id=account_id, request_id=request_id, owner_boot_id=owner_boot_id)
        self.read_local = read_local
        self.write_ack = write_ack
        self.revision = -1
        self.frame_digest = None
        self.acked_revision = -1

    def consume(self, *, session_follows, business_day, composite_reconciled,
                boundary="before_candidate_selection"):
        if boundary != "before_candidate_selection" or composite_reconciled is not True:
            raise FleetBlocked("unsafe_follow_cap_boundary")
        frame = self.read_local()
        if not isinstance(frame, dict) or frame.get("schema") != "FLEET_RUNTIME_LOCAL_V1":
            raise FleetBlocked("missing_or_invalid_local_authority")
        if any(frame.get(key) != value for key, value in self.binding.items()):
            raise FleetBlocked("runner_authority_binding_mismatch")
        if frame.get("business_day") != business_day:
            raise FleetBlocked("canonical_business_day_refresh_required")
        revision = _number(frame.get("revision"), "revision")
        digest = hashlib.sha256(json.dumps(frame, sort_keys=True).encode()).hexdigest()
        if revision < self.revision or (revision == self.revision and digest != self.frame_digest):
            raise FleetBlocked("stale_or_conflicting_local_revision")
        consumed = (_number(frame.get("canonical_follows_before_run"), "canonical_baseline")
                    + _number(session_follows, "session_follows"))
        if "emergency_cap" not in frame:
            raise FleetBlocked("missing_emergency_cap_contract")
        effective, remaining = follow_allowance(
            frame.get("normal_effective_day_cap"), frame["emergency_cap"], consumed)
        self.revision, self.frame_digest = revision, digest
        if self.acked_revision != revision:
            self.write_ack(dict(self.binding, schema="FLEET_CAP_BOUNDARY_ACK_V1",
                                revision=revision, boundary=boundary,
                                business_day=business_day, canonical_consumed=consumed,
                                effective_day_cap=effective, remaining=remaining))
            self.acked_revision = revision
        return dict(revision=revision, effective_day_cap=effective, remaining=remaining)


class FleetLocalChannel:
    """Atomic local journal and policy channel, scoped to an exact owner boot."""
    def __init__(self, root, binding):
        import os
        import stat
        from pathlib import Path
        required = ('account_id', 'request_id', 'attempt_id', 'consumer_instance_id', 'consumer_boot_id')
        if any(not isinstance(binding.get(k), str) or not binding[k] for k in required):
            raise FleetBlocked('incomplete_execution_binding')
        self.binding = {k: binding[k] for k in required}
        self.token = hashlib.sha256(json.dumps(self.binding, sort_keys=True).encode()).hexdigest()
        self.path = Path(root) / self.token
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.path.lstat()
        if (self.path.resolve() != self.path or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise FleetBlocked('untrusted_channel_directory')

    def write(self, name, value):
        import os
        import tempfile
        if name not in ('journal.json', 'authority.json'):
            raise FleetBlocked('invalid_channel_write')
        fd, temporary = tempfile.mkstemp(prefix='.fleet-', dir=str(self.path))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path / name)
            parent = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read(self, name):
        import os
        import stat
        if name not in ('journal.json', 'authority.json', 'cap-ack.json'):
            raise FleetBlocked('invalid_channel_read')
        try:
            fd = os.open(self.path / name, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'r', encoding='utf-8', errors='strict') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or info.st_size > 262144):
                raise FleetBlocked('untrusted_channel_record')
            value = json.load(stream)
        if not isinstance(value, dict):
            raise FleetBlocked('invalid_channel_record')
        return value


def fleet_local_authority_from_journal(journal, *, token, account_id, request_id, owner_boot_id):
    """Derive transport bytes only from a trusted, complete durable authority."""
    if not isinstance(journal, dict) or journal.get('schema') != 'FLEET_EXECUTION_JOURNAL_V1':
        raise FleetBlocked('missing_or_invalid_durable_authority')
    binding = journal.get('binding')
    required = {'account_id', 'request_id', 'attempt_id', 'consumer_instance_id', 'consumer_boot_id'}
    if (not isinstance(binding, dict) or set(binding) != required
            or any(not isinstance(value, str) or not value for value in binding.values())):
        raise FleetBlocked('invalid_durable_binding')
    actual_token = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    if (actual_token != token or binding['account_id'] != account_id
            or binding['request_id'] != request_id or binding['consumer_boot_id'] != owner_boot_id):
        raise FleetBlocked('durable_authority_binding_mismatch')
    state = journal.get('state')
    if not isinstance(state, dict):
        raise FleetBlocked('missing_durable_state')
    authority = state.get('execution_authority')
    if (not isinstance(authority, dict)
            or authority.get('schema') != 'FLEET_EXECUTION_AUTHORITY_V1'
            or authority.get('execution_registered') is not True
            or any(authority.get(key) != value for key, value in binding.items())):
        raise FleetBlocked('missing_or_misbound_durable_execution_authority')
    revision = _number(state.get('revision'), 'durable_revision')
    if _number(authority.get('revision'), 'authority_revision') != revision:
        raise FleetBlocked('durable_revision_authority_mismatch')
    if (state.get('account_id') != account_id
            or not isinstance(state.get('tenant_id'), str) or not state['tenant_id']
            or authority.get('tenant_id') != state['tenant_id']):
        raise FleetBlocked('durable_tenant_or_account_mismatch')
    if ('emergency_cap' not in authority or 'cap' not in state
            or state['cap'] != authority['emergency_cap']):
        raise FleetBlocked('durable_cap_authority_mismatch')
    follow_allowance(authority.get('normal_effective_day_cap'), authority['emergency_cap'], 0)
    _number(authority.get('canonical_follows_before_run'), 'canonical_baseline')
    if not isinstance(authority.get('business_day'), str) or not authority['business_day']:
        raise FleetBlocked('missing_durable_business_day')
    digest = hashlib.sha256(json.dumps(authority, sort_keys=True).encode()).hexdigest()
    return dict(schema='FLEET_RUNTIME_LOCAL_V1', integrity_version=1,
                authority_sha256=digest, tenant_id=state['tenant_id'],
                account_id=account_id, request_id=request_id,
                attempt_id=binding['attempt_id'],
                consumer_instance_id=binding['consumer_instance_id'],
                owner_boot_id=owner_boot_id, revision=revision,
                business_day=authority['business_day'],
                canonical_follows_before_run=authority['canonical_follows_before_run'],
                normal_effective_day_cap=authority['normal_effective_day_cap'],
                emergency_cap=authority['emergency_cap'])


def fleet_process_group_absent(process_group_id):
    """Only POSIX ESRCH proves absence. Permission/other errors stay fenced."""
    import errno
    import os
    if (type(process_group_id) is not int or process_group_id <= 1
            or process_group_id == os.getpgrp()):
        raise FleetBlocked('invalid_execution_process_group')
    try:
        os.killpg(process_group_id, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return True
        raise FleetBlocked('process_group_quiescence_unknown') from exc
    return False


class FleetConsumerSession:
    """A registered execution attempt supervised by its exact consumer boot.

    RPC adapters must prove canonical registration/reconciliation. No ACTIVE is
    exported by local guesses. A STARTING journal with unknown child is retained
    for explicit orphan reconciliation; heartbeat expiry is never death proof.
    """
    def __init__(self, *, binding, authority, root, rpc, child=None):
        self.channel = FleetLocalChannel(root, binding)
        self.binding = self.channel.binding
        self.rpc = rpc
        self.child = child
        self.owner = binding['consumer_instance_id'] + ':' + binding['consumer_boot_id']
        self._mutex = threading.RLock()
        self._cap_ack_sent = -1
        self._runtime_ack_sent = -1
        self._authority_digest = None
        self._authority = None
        self._validate(authority)
        restored = self.channel.read('journal.json')
        if restored is not None:
            persisted = restored.get('state', {}).get('execution_authority', {})
            if persisted.get('process_generation') != authority['process_generation']:
                raise FleetBlocked('process_generation_authority_mismatch')
        if restored is not None:
            if restored.get('binding') != self.binding:
                raise FleetBlocked('journal_boot_or_binding_mismatch')
            state = restored['state']
        else:
            revision = _number(authority['revision'], 'revision')
            admitted_revision = _number(authority['attempt_revision'], 'attempt_revision')
            if admitted_revision > revision:
                raise FleetBlocked('future_attempt_revision')
            state = dict(tenant_id=authority['tenant_id'], account_id=binding['account_id'],
                         revision=revision, hold='CLEARED', active_revision=None,
                         owners={self.owner: -1},
                         attempts={binding['attempt_id']: dict(owner=self.owner,
                             revision=admitted_revision, state='STARTED' if child is not None else 'ADMITTED',
                             pid=child.pid if child is not None else None)},
                         receipts={}, cap=None, cap_state='CAP_REQUESTED', cap_acks={})
        self.barrier = FleetRuntimeBarrier(authority['tenant_id'], binding['account_id'],
                                          self._persist, restored=state)
        self._persist(state)
        self.observe(authority, initial=True)

    def _persist(self, state):
        self.channel.write('journal.json', dict(schema='FLEET_EXECUTION_JOURNAL_V1',
                                                binding=self.binding, state=state))

    def _validate(self, frame):
        generation = frame.get('process_generation') if isinstance(frame, dict) else None
        if (not isinstance(generation, str) or len(generation) != 32
                or any(char not in '0123456789abcdef' for char in generation)):
            raise FleetBlocked('missing_or_misbound_process_generation')
        previous = getattr(self, '_authority', None)
        if previous is not None and previous.get('process_generation') != generation:
            raise FleetBlocked('process_generation_authority_mismatch')
        if not isinstance(frame, dict) or frame.get('schema') != 'FLEET_EXECUTION_AUTHORITY_V1':
            raise FleetBlocked('invalid_execution_authority')
        if frame.get('execution_registered') is not True:
            raise FleetBlocked('execution_not_registered')
        if any(frame.get(k) != v for k, v in self.binding.items()):
            raise FleetBlocked('execution_authority_binding_mismatch')
        if not isinstance(frame.get('tenant_id'), str) or not frame['tenant_id']:
            raise FleetBlocked('missing_tenant_authority')
        _number(frame.get('revision'), 'revision')
        _number(frame.get('attempt_revision'), 'attempt_revision')
        _number(frame.get('canonical_follows_before_run'), 'canonical_baseline')
        follow_allowance(frame.get('normal_effective_day_cap'), frame.get('emergency_cap'), 0)
        if 'emergency_cap' not in frame or frame.get('hold') not in ('REQUESTED', 'ACTIVE', 'RELEASING', 'CLEARED'):
            raise FleetBlocked('incomplete_fleet_policy')
        if not isinstance(frame.get('business_day'), str) or not frame['business_day']:
            raise FleetBlocked('missing_business_day')

    def observe(self, frame, initial=False):
        with self._mutex, self.barrier._lock:
            self._validate(frame)
            state = self.barrier.snapshot()
            if frame['tenant_id'] != state['tenant_id']:
                raise FleetBlocked('tenant_authority_mismatch')
            revision = frame['revision']
            digest = hashlib.sha256(json.dumps(frame, sort_keys=True).encode()).hexdigest()
            if revision < state['revision']:
                raise FleetBlocked('stale_authority_revision')
            committed = state.get('execution_authority')
            if revision == state['revision'] and committed is not None:
                committed_digest = hashlib.sha256(json.dumps(committed, sort_keys=True).encode()).hexdigest()
                if digest != committed_digest:
                    raise FleetBlocked('conflicting_authority_revision')
                self._authority_digest, self._authority = digest, copy.deepcopy(committed)
                # Includes retry after durable commit succeeded but publish failed.
                self.republish_current_authority()
                return
            if not initial and revision == state['revision']:
                raise FleetBlocked('missing_durable_execution_authority')
            state.update(revision=revision, active_revision=None,
                         hold='REQUESTED' if frame['hold'] == 'ACTIVE' else frame['hold'],
                         cap=frame['emergency_cap'], cap_state='CAP_REQUESTED', cap_acks={},
                         execution_authority=copy.deepcopy(frame))
            state['owners'] = {owner: -1 for owner in state['owners']}
            self.barrier._save(state)
            self._authority_digest, self._authority = digest, copy.deepcopy(frame)
            self.republish_current_authority()

    def republish_current_authority(self):
        """One bounded local recovery; never derive a policy from the cache."""
        with self._mutex, self.barrier._lock:
            journal = self.channel.read('journal.json')
            expected = fleet_local_authority_from_journal(
                journal, token=self.channel.token,
                account_id=self.binding['account_id'], request_id=self.binding['request_id'],
                owner_boot_id=self.binding['consumer_boot_id'])
            state = self.barrier.snapshot()
            if (journal['state']['revision'] != state['revision']
                    or journal['state'].get('execution_authority') != state.get('execution_authority')):
                raise FleetBlocked('durable_authority_changed_during_republish')
            try:
                published = self.channel.read('authority.json')
            except (ValueError, UnicodeError):
                published = None
            if published != expected:
                self.channel.write('authority.json', expected)
            if self.channel.read('authority.json') != expected:
                raise FleetBlocked('authority_republish_verification_failed')
            latest = fleet_local_authority_from_journal(
                self.channel.read('journal.json'), token=self.channel.token,
                account_id=self.binding['account_id'], request_id=self.binding['request_id'],
                owner_boot_id=self.binding['consumer_boot_id'])
            if latest != expected:
                raise FleetBlocked('durable_authority_changed_during_republish')
            return expected

    def environment(self):
        return dict(FLEET_RUNTIME_CHANNEL_V1=self.channel.token,
                    FLEET_RUNTIME_OWNER_BOOT_ID=self.binding['consumer_boot_id'])

    def spawn(self, spawn_child):
        with self._mutex:
            def register_group():
                child = spawn_child()
                # The consumer launch contract uses start_new_session=True.
                # Capture its group now; never derive it from a PID at recovery.
                if type(child.pid) is not int or child.pid <= 1:
                    raise FleetBlocked('invalid_spawned_group_leader')
                state = self.barrier.snapshot()
                row = state['attempts'][self.binding['attempt_id']]
                row['process_group'] = dict(
                    self.binding, process_group_id=child.pid, leader_pid=child.pid,
                    fleet_revision=row['revision'],
                    process_generation=self._authority['process_generation'],
                    isolation_version='isolated_process_group_v1')
                self.barrier._save(state)
                return child
            return self.barrier.spawn(self.binding['attempt_id'], register_group)

    def is_legacy_unisolated(self):
        return self.barrier.snapshot().get('legacy_unisolated_execution') is True

    def register_late_child_isolation(self, child):
        # Neither a PID nor a matching PGID proves how an old runner was launched.
        with self._mutex, self.barrier._lock:
            evidence = getattr(child, '_fleet_isolated_launch_v1', None)
            state = self.barrier.snapshot()
            if evidence is None:
                state['legacy_unisolated_execution'] = True
                state['convergence_reason'] = 'legacy_unisolated_execution'
                if state['hold'] != 'CLEARED':
                    state['hold'] = 'REQUESTED'
                    state['active_revision'] = None
                self.barrier._save(state)
                return
            channel, expected = evidence
            actual = channel.read('journal.json')
            if actual != expected or actual.get('schema') != 'RUNNER_ISOLATION_V1':
                raise FleetBlocked('invalid_isolated_launch_evidence')
            group = actual.get('process_group')
            if (actual.get('state') != 'STARTED' or not isinstance(group, dict)
                    or any(group.get(k) != v for k, v in self.binding.items())
                    or group.get('leader_pid') != child.pid
                    or group.get('process_group_id') != child.pid
                    or group.get('isolation_version') != 'isolated_process_group_v1'
                    or group.get('launched_without_fleet_policy') is not True
                    or group.get('fleet_revision') is not None):
                raise FleetBlocked('misbound_isolated_launch_evidence')
            state['attempts'][self.binding['attempt_id']]['process_group'] = copy.deepcopy(group)
            self.barrier._save(state)
            self._registered_process_group()

    def _registered_process_group(self):
        row = self.barrier.snapshot()['attempts'][self.binding['attempt_id']]
        group = row.get('process_group')
        if (not isinstance(group, dict)
                or any(group.get(key) != value for key, value in self.binding.items())
                or type(group.get('process_group_id')) is not int
                or group['process_group_id'] <= 1
                or type(group.get('leader_pid')) is not int
                or group['leader_pid'] != row['pid']
                or group['process_group_id'] != group['leader_pid']
                or (type(group.get('fleet_revision')) is not int and not (group.get('fleet_revision') is None and group.get('launched_without_fleet_policy') is True))
                or (group['fleet_revision'] != row['revision'] and not (group['fleet_revision'] is None and group.get('launched_without_fleet_policy') is True))
                or (self.child is not None and self.child.pid != group['leader_pid'])):
            raise FleetBlocked('missing_or_misbound_process_group')
        generation = group.get('process_generation')
        if (group.get('isolation_version') != 'isolated_process_group_v1'
                or not isinstance(generation, str) or len(generation) != 32
                or any(c not in '0123456789abcdef' for c in generation)):
            raise FleetBlocked('missing_or_misbound_process_generation')
        if generation != self._authority['process_generation']:
            raise FleetBlocked('process_generation_authority_mismatch')
        return copy.deepcopy(group)

    def process_group_quiescent(self):
        with self._mutex:
            group = self._registered_process_group()
            return fleet_process_group_absent(group['process_group_id'])

    def terminate_process_group(self, *, graceful_timeout_seconds=30.0):
        """Existing TERM grace then KILL/10s policy, applied to the bound group."""
        import errno
        import os
        import signal
        import time
        import subprocess
        with self._mutex:
            group = self._registered_process_group()
            if self.child is None:
                # A numeric group record alone is not permission to signal after
                # a consumer restart: require the currently owned child handle.
                raise FleetBlocked('group_termination_requires_owned_child')
            pgid = group['process_group_id']
            def send(sig):
                if self._registered_process_group() != group:
                    raise FleetBlocked('process_group_binding_changed')
                try:
                    os.killpg(pgid, sig)
                except OSError as exc:
                    if exc.errno != errno.ESRCH:
                        raise FleetBlocked('process_group_signal_failed') from exc
            def wait_absent(seconds):
                deadline = time.monotonic() + seconds
                # A post-TERM poll can race with exit and leave a Darwin zombie
                # whose group probe returns EPERM. Reap the owned leader first;
                # this is NOT proof that descendants or the group are absent.
                try:
                    self.child.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    return False
                while True:
                    if self._registered_process_group() != group:
                        raise FleetBlocked('process_group_binding_changed')
                    unknown = None
                    try:
                        if fleet_process_group_absent(pgid):
                            return True
                    except FleetBlocked as exc:
                        # A reparented descendant can be tearing down after
                        # the owned leader was reaped. EPERM remains UNKNOWN:
                        # retry observation only, never infer absence or ACK.
                        if (str(exc) != 'process_group_quiescence_unknown'
                                or not isinstance(exc.__cause__, OSError)
                                or exc.__cause__.errno != errno.EPERM):
                            raise
                        unknown = exc
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        if unknown is not None:
                            raise unknown
                        return False
                    time.sleep(min(0.05, remaining))
            self.child.poll()
            if not fleet_process_group_absent(pgid):
                send(signal.SIGTERM)
                if not wait_absent(max(1.0, float(graceful_timeout_seconds))):
                    send(signal.SIGKILL)
                    if not wait_absent(10.0):
                        raise FleetBlocked('process_group_still_alive_after_escalation')
            if not fleet_process_group_absent(pgid):
                raise FleetBlocked('process_group_quiescence_lost')
            return int(self.child.wait(timeout=10))

    def register_child(self, child):
        with self._mutex:
            row = self.barrier.snapshot()['attempts'][self.binding['attempt_id']]
            if row['state'] != 'STARTED' or row['pid'] != child.pid:
                raise FleetBlocked('child_registration_mismatch')
            self.child = child

    def _call(self, operation, **proof):
        revision = self.barrier.snapshot()['revision']
        result = self.rpc(operation, dict(self.binding, revision=revision, **proof))
        if (not isinstance(result, dict) or result.get('ok') is not True
                or result.get('revision') != revision
                or any(result.get(k) != v for k, v in self.binding.items())):
            raise FleetBlocked('invalid_or_stale_backend_ack')
        return result

    def request_canonical_cancel(self):
        with self._mutex:
            if self.barrier.snapshot()['hold'] not in ('REQUESTED', 'ACTIVE'):
                return None
            proof = self._call('cancel')
            row = proof.get('request')
            if (not isinstance(row, dict) or row.get('id') != self.binding['request_id']
                    or not (row.get('cancel_requested_at') or row.get('status') == 'canceled')):
                raise FleetBlocked('canonical_cancellation_not_proven')
            return row

    def reconcile(self, *, unstarted=False, orphan_proof=None):
        if self.is_legacy_unisolated():
            raise FleetBlocked('legacy_unisolated_execution')
        with self._mutex:
            row = self.barrier.snapshot()['attempts'][self.binding['attempt_id']]
            if unstarted:
                if row['state'] != 'ADMITTED' or self.child is not None:
                    raise FleetBlocked('unstarted_proof_invalid')
            elif orphan_proof is not None:
                if (orphan_proof.get('binding') != self.binding
                        or orphan_proof.get('process_group_absent') is not True
                        or orphan_proof.get('heartbeat_expiry_used') is not False
                        or row['pid'] is None or orphan_proof.get('pid') != row['pid']):
                    raise FleetBlocked('orphan_death_not_proven')
            elif self.child is None or self.child.poll() is None:
                raise FleetBlocked('child_unknown_or_alive')
            group = None
            if not unstarted:
                group = self._registered_process_group()
                if not fleet_process_group_absent(group['process_group_id']):
                    raise FleetBlocked('process_group_still_alive')
            proof = self._call('reconcile', child_gone=True, unstarted=unstarted,
                               pid=row['pid'], orphan_proof=orphan_proof,
                               process_group=group, process_group_absent=True)
            if proof.get('canonical_terminal') is not True:
                raise FleetBlocked('canonical_terminal_not_proven')
            self.barrier.reconcile_terminal(self.binding['attempt_id'], self.owner,
                                            canonical_terminal=True, child_gone=True)
            state = self.barrier.snapshot()
            if state['hold'] in ('REQUESTED', 'RELEASING') and self._runtime_ack_sent != state['revision']:
                self._call('runtime_ack', child_gone=True, canonical_terminal=True,
                           process_group=group, process_group_absent=True)
                self.barrier.acknowledge(self.owner, state['revision'])
                self._runtime_ack_sent = state['revision']

    def flush_cap_ack(self):
        with self._mutex:
            ack = self.channel.read('cap-ack.json')
            if ack is None:
                return False
            revision = self.barrier.snapshot()['revision']
            if (ack.get('schema') != 'FLEET_CAP_BOUNDARY_ACK_V1'
                    or ack.get('account_id') != self.binding['account_id']
                    or ack.get('request_id') != self.binding['request_id']
                    or ack.get('owner_boot_id') != self.binding['consumer_boot_id']
                    or ack.get('revision') != revision
                    or ack.get('boundary') != 'before_candidate_selection'):
                raise FleetBlocked('stale_or_misbound_cap_ack')
            if self._cap_ack_sent == revision:
                return False
            self._call('cap_ack', boundary_ack=ack)
            self._cap_ack_sent = revision
            return True


def fleet_spawn_isolated_v1(spawn_child, *, binding, root):
    """Durable local isolation receipt; no inferred Fleet revision or network call."""
    from uuid import uuid4
    from pathlib import Path
    generation = uuid4().hex
    channel = FleetLocalChannel(Path(root) / generation, binding)
    record = {'schema': 'RUNNER_ISOLATION_V1', 'binding': dict(binding),
              'state': 'STARTING', 'process_generation': generation,
              'isolation_version': 'isolated_process_group_v1', 'process_group': None}
    channel.write('journal.json', record)
    child = spawn_child()
    if type(child.pid) is not int or child.pid <= 1:
        raise FleetBlocked('invalid_isolated_child_pid')
    group = dict(binding, process_group_id=child.pid, leader_pid=child.pid,
                 process_generation=generation, isolation_version='isolated_process_group_v1',
                 fleet_revision=None, launched_without_fleet_policy=True)
    record = dict(record, state='STARTED', process_group=group)
    channel.write('journal.json', record)
    child._fleet_isolated_launch_v1 = (channel, record)
    return child
