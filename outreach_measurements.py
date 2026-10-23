"""Validation of operator-approved DB policy. Observations never self-promote."""
from datetime import datetime
from pathlib import Path
import hashlib


def sender_identity():
    digest = hashlib.sha256()
    for name in ('dm_sender_engine.py', 'instagram_navigation.py',
                 'outreach_slot_contract.py', 'outreach_measurements.py'):
        digest.update(name.encode())
        digest.update((Path(__file__).parent / name).read_bytes())
    return digest.hexdigest()


def stamp(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('measurement_timestamp_invalid')
    return dt.timestamp()


def validate_policy(context, now):
    """The trusted marker is issued by a restricted RPC, never assignment metadata."""
    p = context.get('execution_policy') or {}
    try:
        if p.get('authority') != 'operator_db_v2' or not p.get('approval_id'):
            raise ValueError('outreach_measurements_required')
        for key in ('account_id', 'device_id', 'assignment_id', 'package_name'):
            if p.get(key) != context.get(key):
                raise ValueError('outreach_policy_binding_mismatch')
        if (not p.get('approved_by') or not p.get('approval_reason')
                or not p.get('sender_identity') or not p.get('instagram_version')
                or type(p.get('cleanup_grace_ms')) is not int or p['cleanup_grace_ms'] <= 0
                or stamp(p['approved_at']) > now or stamp(p['expires_at']) <= now
                or stamp(p['expires_at']) < stamp(context['slot_end'])):
            raise ValueError('outreach_policy_invalid_or_stale')
        if p.get('mode') == 'bootstrap':
            if (p.get('slot_id') != context['slot_id'] or p.get('target_dm') != context['target_dm']
                    or int(context.get('attempt_id', 1)) != 1 or int(context.get('restart_count', 0)) != 0):
                raise ValueError('outreach_bootstrap_scope_invalid')
        elif p.get('mode') == 'normal':
            if (p.get('measurement_source') != 'outreach_real'
                    or p.get('run_type') != 'outreach_session'
                    or p.get('sample_criteria') != 'operator_reviewed_success_only'
                    or not p.get('evidence_request_ids')
                    or not p.get('sample_count_justification')
                    or type(p.get('sample_count')) is not int or p['sample_count'] <= 0
                    or not (stamp(p['observed_at']) <= stamp(p['approved_at']) <= now)
                    or type(p.get('max_age_seconds')) is not int or p['max_age_seconds'] <= 0
                    or stamp(p['observed_at']) + p['max_age_seconds'] < stamp(context['slot_end'])
                    or any(type(p.get(k)) is not int or p[k] <= 0 for k in ('unit_ms', 'cleanup_ms'))
                    or p['cleanup_grace_ms'] < p['cleanup_ms']):
                raise ValueError('outreach_measurement_provenance_invalid')
        else:
            raise ValueError('outreach_mode_invalid')
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('outreach_policy_invalid') from exc
    return p


def dispatcher_deadlines(context):
    from datetime import timedelta
    p = validate_policy(context, datetime.now().astimezone().timestamp())
    end = datetime.fromisoformat(context['slot_end'].replace('Z', '+00:00'))
    return {'BUSINESS_ACTION_DEADLINE': context['slot_end'],
            'SCHEDULED_SESSION_START': context['slot_start'],
            'SCHEDULED_SESSION_END': (end + timedelta(milliseconds=p['cleanup_grace_ms'])).isoformat()}
