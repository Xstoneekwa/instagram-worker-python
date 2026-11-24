"""Passive, bounded CT evidence delivery outside Follow persistence."""

from datetime import datetime, timezone
import hashlib
import logging
from queue import Full, Queue
import re
from threading import Lock, Thread
from uuid import UUID


_USERNAME = re.compile(r"[a-z0-9._]{1,30}")
_OUTCOMES = {"eligible", "private"}
_LOG = logging.getLogger(__name__)


def _username(value):
    return str(value or "").strip().lower().removeprefix("@")


def _uuid(value):
    return str(UUID(str(value)))


class EvaluationEvidenceWriter:
    def __init__(self, send, *, capacity=128):
        self._send = send
        self._queue = Queue(maxsize=capacity)
        self._start_lock = Lock()
        self._thread = None

    def submit(self, evidence):
        try:
            with self._start_lock:
                if self._thread is None:
                    thread = Thread(target=self._drain, name="ct-evaluation-evidence", daemon=True)
                    thread.start()
                    self._thread = thread
            self._queue.put_nowait(evidence)
            return True
        except Full:
            _LOG.warning("ct_evaluation_evidence_queue_full")
        except RuntimeError:
            _LOG.warning("ct_evaluation_evidence_writer_unavailable")
        return False

    def _drain(self):
        while True:
            evidence = self._queue.get()
            try:
                self._send(evidence)
            except Exception as exc:
                _LOG.warning("ct_evaluation_evidence_write_failed %s", type(exc).__name__)
            finally:
                self._queue.task_done()


class TargetEvaluationCapture:
    def __init__(self, *, account_id, target_id, run_id, worker_sha, source_username, submit):
        self._scope = (_uuid(account_id), _uuid(target_id), _uuid(run_id))
        self._worker_sha = str(worker_sha or "").strip().lower()
        self._source_username = _username(source_username)
        if not re.fullmatch(r"[0-9a-f]{40}", self._worker_sha) or not _USERNAME.fullmatch(self._source_username):
            raise ValueError("ct_evaluation_evidence_scope_invalid")
        self._submit = submit
        self._seen = set()

    def observe(self, *, candidate_username, observed_username, outcome):
        try:
            candidate = _username(candidate_username)
            if (
                not _USERNAME.fullmatch(candidate)
                or candidate == self._source_username
                or candidate != _username(observed_username)
                or outcome not in _OUTCOMES
                or candidate in self._seen
            ):
                return False
            account_id, target_id, run_id = self._scope
            key = hashlib.sha256(":".join((*self._scope, candidate)).encode()).hexdigest()
            accepted = self._submit({
                "p_account_id": account_id,
                "p_target_id": target_id,
                "p_source_run_id": run_id,
                "p_normalized_username": candidate,
                "p_evaluated_at": datetime.now(timezone.utc).isoformat(),
                "p_outcome": outcome,
                "p_source_worker": "phonefarm_followers",
                "p_attribution_reliability": "strong",
                "p_worker_version": self._worker_sha,
                "p_idempotency_key": f"ct-evaluation-v1:{key}",
            })
            if accepted:
                self._seen.add(candidate)
            return accepted
        except Exception:
            _LOG.warning("ct_evaluation_evidence_capture_failed")
            return False
