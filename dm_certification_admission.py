"""One-shot DM certification binding. Inactive production calls perform no IO.

Only the verified external child configures this process. SQL owns durable
consumption; a response lost at a mutation boundary never permits a retry.
The canonical Outreach orchestrator remains the sole sender entrypoint.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
import hashlib
import json
import os
import re
import threading

SCHEMA = 'DM446_CERTIFICATION_ONE_ATTEMPT_V1'
EXACT = {
    'account_id': 'fd3dc588-06c0-4f09-a327-ed6c73b13836',
    'username': 'bizgrowth_bmb',
    'device_id': 'd0f26bfe-5bb1-46c0-af62-dce21be5e8e4',
    'app_instance_id': '7f84f24b-1ad7-4839-adff-7b9639fb91b2',
    'serial': 'RFGL62SZ7NR', 'package': 'com.instagram.android',
    'version_name': '446.0.0.49.77', 'version_code': 385211304,
    'message_body': 'salut test',
    'dm_type': 'outreach', 'max_jobs': 1, 'max_send_attempts': 1,
}
FIELDS = frozenset(EXACT) | {'recipient_username'} | {
    'schema', 'permit_id', 'nonce', 'job_id', 'candidate_sha', 'candidate_tree',
    'certification_not_before', 'certification_expires_at', 'message_sha256',
    'launcher_sha256',
}
BINDING = frozenset({'permit_hash', 'run_id', 'request_id', 'lease_id',
                     'worker_id', 'execution_id'})
_SESSION = None
_CONFIGURED = False
_LOCK = threading.Lock()


class CertificationStop(RuntimeError):
    """Safe reason only; never embed backend payloads or credentials."""


def require(ok, reason):
    if not ok:
        raise CertificationStop(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def utc(value):
    require(type(value) is str, 'dm_certification_timestamp_type')
    match = re.fullmatch(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)', value)
    require(match is not None, 'dm_certification_timestamp_format')
    body, fraction, zone = match.groups()
    require(zone == 'Z' or (int(zone[1:3]) < 24 and int(zone[4:]) < 60), 'dm_certification_timezone')
    try:
        return datetime.fromisoformat(body + ('.'+fraction.ljust(6, '0') if fraction else '') + ('+00:00' if zone == 'Z' else zone))
    except ValueError:
        raise CertificationStop('dm_certification_timestamp_date') from None


def validate_claims(claims, *, now=None):
    require(type(claims) is dict and set(claims) == FIELDS, 'dm_certification_claim_fields')
    require(claims['schema'] == SCHEMA, 'dm_certification_schema')
    for key, value in EXACT.items():
        require(type(claims[key]) is type(value) and claims[key] == value,
                'dm_certification_tuple:'+key)
    recipient = claims['recipient_username']
    require(type(recipient) is str and re.fullmatch(r'[A-Za-z0-9._]{1,30}', recipient),
            'dm_certification_recipient')
    for key in ('permit_id', 'nonce', 'job_id', 'account_id', 'device_id', 'app_instance_id'):
        try:
            require(type(claims[key]) is str and str(UUID(claims[key])) == claims[key], 'dm_certification_uuid:'+key)
        except (ValueError, TypeError, AttributeError):
            raise CertificationStop('dm_certification_uuid:'+key) from None
    for key, length in (('candidate_sha', 40), ('candidate_tree', 40), ('launcher_sha256', 64), ('message_sha256', 64)):
        require(type(claims[key]) is str and re.fullmatch('[0-9a-f]{%d}' % length, claims[key]), 'dm_certification_hash:'+key)
    require(claims['message_sha256'] == digest(claims['message_body'].encode()), 'dm_certification_message_hash')
    start, end = utc(claims['certification_not_before']), utc(claims['certification_expires_at'])
    require(start <= (now or datetime.now(timezone.utc)) < end, 'dm_certification_window')
    return json.loads(canonical(claims))


def validate_binding(claims, binding):
    require(type(binding) is dict and set(binding) == BINDING, 'dm_certification_binding_fields')
    require(binding['permit_hash'] == digest(canonical(claims)), 'dm_certification_permit_hash')
    for key in BINDING - {'permit_hash', 'worker_id'}:
        try:
            require(type(binding[key]) is str and str(UUID(binding[key])) == binding[key], 'dm_certification_binding:'+key)
        except (ValueError, TypeError, AttributeError):
            raise CertificationStop('dm_certification_binding:'+key) from None
    worker = binding['worker_id']
    require(type(worker) is str and re.fullmatch(r'dm446-cert-[0-9a-f]{32}', worker), 'dm_certification_worker')
    return json.loads(canonical(binding))


def receipt(claims, binding, result, state):
    require(type(result) is dict and result.get('ok') is True, 'dm_certification_receipt_unproved')
    require(result.get('claims') == claims and result.get('binding') == binding,
            'dm_certification_receipt_identity')
    require(result.get('state') == state, 'dm_certification_receipt_state')
    require(result.get('authority_live') is True, 'dm_certification_authority_lost')
    return result


def _rpc(name, payload):
    import supabase_client
    return supabase_client._request_json('POST', 'rpc/'+name, body=payload,
                                         request_timeout=5.0, max_retries=0)


def configure_from_signed_permit(*, manifest_path, signature_path, permit_id, binding):
    """Configure once after the durable CHILD_STARTED CAS, never re-enter."""
    global _CONFIGURED, _SESSION
    with _LOCK:
        require(not _CONFIGURED, 'dm_certification_reconfigure_forbidden')
        _CONFIGURED = True  # Invalid-present authority cannot fall back to inactive.
        root = Path(__file__).resolve().parent
        from follow60_deployment_gate_v1 import verify_deployment_candidate
        raw = Path(manifest_path).read_bytes()
        verdict = verify_deployment_candidate(root, manifest_path=Path(manifest_path), signature_path=Path(signature_path))
        require(verdict.get('ok') is True, 'dm_certification_approval2_invalid')
        require(Path(manifest_path).read_bytes() == raw, 'dm_certification_manifest_changed')
        manifest = json.loads(raw)
        rows = manifest.get('dm446_certification_permits')
        require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, 'dm_certification_permit_count')
        claims = validate_claims(rows[0])
        require(claims['permit_id'] == permit_id, 'dm_certification_permit_id')
        require(claims['candidate_sha'] == (manifest.get('candidate_identity') or {}).get('candidate_commit_sha') and
                claims['candidate_tree'] == (manifest.get('candidate_identity') or {}).get('candidate_tree_sha'), 'dm_certification_candidate')
        binding = validate_binding(claims, binding)
        result = _rpc('dm446_certification_receipt_v1', {'p_permit_hash': binding['permit_hash']})
        receipt(claims, binding, result, 'CHILD_STARTED')
        _SESSION = {'claims': claims, 'binding': binding, 'pid': os.getpid(), 'dispatch_started': False}


def current():
    require(_SESSION is not None and _SESSION['pid'] == os.getpid(), 'dm_certification_context_unproved')
    validate_claims(_SESSION['claims'])
    return _SESSION


def is_present():
    return _CONFIGURED or 'DM446_CERTIFICATION_PERMIT_ID' in os.environ


def validate_runner(args):
    """Absent certification is a no-op, with no backend/filesystem/device IO."""
    if not _CONFIGURED and 'DM446_CERTIFICATION_PERMIT_ID' not in os.environ:
        return None
    session = current()
    c, b = session['claims'], session['binding']
    values = {'account_id': c['account_id'], 'run_type': 'outreach_session',
              'run_request_id': b['request_id'], 'admitted_run_id': b['run_id'],
              'device_serial': c['serial'], 'package_name': c['package'],
              'expected_app_instance_id': c['app_instance_id']}
    require(all(getattr(args, key, None) == value for key, value in values.items()), 'dm_certification_runner_args')
    require(os.environ.get('DM446_CERTIFICATION_PERMIT_ID') == c['permit_id'], 'dm_certification_child_context')
    require(os.environ.get('DM_SENDER_ONLY_JOB_ID') == c['job_id'], 'dm_certification_job_filter')
    import config
    require(config.RUNTIME_HEARTBEATS_ENABLED is False and config.DM_SENDER_RESERVED_BY == b['worker_id'], 'dm_certification_child_configuration')
    receipt(c, b, _rpc('dm446_certification_receipt_v1', {'p_permit_hash': b['permit_hash']}), 'CHILD_STARTED')
    return session


def routing(account_id, account_username, run_id, request_id):
    s = current(); c, b = s['claims'], s['binding']
    require((account_id, account_username, run_id, request_id) ==
            (c['account_id'], c['username'], b['run_id'], b['request_id']), 'dm_certification_runtime_identity')
    return {'assignment_found': False, 'fallback_used': False,
            'reason': 'signed_dm446_certification_context', 'device_id': c['device_id'],
            'clone_id': c['app_instance_id'], 'adb_serial': c['serial'], 'package_name': c['package']}


def validate_job(c, b, prepared):
    require(type(prepared) is dict and prepared.get('session_status') == 'prepared' and
            type(prepared.get('exit_code')) is int and prepared['exit_code'] == 0 and
            type(prepared.get('prepared_jobs_count')) is int and prepared['prepared_jobs_count'] == 1,
            'dm_certification_preparation_unproved')
    require(prepared.get('account_id') == c['account_id'] and prepared.get('account_username') == c['username']
            and prepared.get('run_id') == b['run_id'] and prepared.get('dm_type') == 'outreach', 'dm_certification_prepared_binding')
    rows = prepared.get('prepared_jobs')
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, 'dm_certification_job_count')
    expected = {k: c[k] for k in ('account_id', 'recipient_username', 'message_body', 'dm_type')}
    expected.update(id=c['job_id'], reserved_by=b['worker_id'], status='reserved', attempts=0, max_attempts=1)
    require(all(type(rows[0].get(k)) is type(v) and rows[0][k] == v for k, v in expected.items()), 'dm_certification_job_identity')
    return rows[0]


def dispatch(d, *, account_id, account_username, run_id, verified_username):
    s = current(); c, b = s['claims'], s['binding']
    routing(account_id, account_username, run_id, b['request_id'])
    require(verified_username == c['username'], 'dm_certification_identity_guard_unproved')
    from instagram_navigation import _dm_446_context
    require(_dm_446_context(d) is not None, 'dm_certification_446_context_unproved')
    with _LOCK:
        require(not s['dispatch_started'], 'dm_certification_dispatch_repeated')
        s['dispatch_started'] = True
    receipt(c, b, _rpc('dm446_certification_receipt_v1', {'p_permit_hash': b['permit_hash']}), 'CHILD_STARTED')
    from outreach_session_orchestrator import prepare_outreach_session, dispatch_outreach_session
    prepared = prepare_outreach_session(d, account_id=account_id, account_username=account_username,
                                        run_id=run_id, max_jobs_override=1)
    validate_job(c, b, prepared)
    receipt(c, b, _rpc('dm446_certification_receipt_v1', {'p_permit_hash': b['permit_hash']}), 'JOB_CLAIMED')
    return dispatch_outreach_session(d, account_id=account_id, account_username=account_username,
                                     run_id=run_id, prepared_outreach=prepared)


def revoke_local():
    global _SESSION
    with _LOCK:
        _SESSION = None  # _CONFIGURED stays true: no resurrection in this process.
