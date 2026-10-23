"""Dedicated outreach occurrence guard. No device or database IO at import time."""
from __future__ import annotations
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

class SlotBlocked(RuntimeError):
    pass

_ACTIVE: SlotBudget | None = None

def _timestamp(value: Any) -> float:
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            raise ValueError('timezone missing')
        return dt.timestamp()
    except (ValueError, TypeError):
        raise SlotBlocked('outreach_slot_timestamp_invalid')

class SlotBudget:
    def __init__(self, context, *, wall=time.time, monotonic=time.monotonic, rpc=None, emit=None):
        self.context = dict(context)
        self.wall, self.monotonic, self.rpc = wall, monotonic, rpc
        self.emit = emit or (lambda *a, **k: None)
        for key in ('slot_id','slot_start','slot_end','account_id','assignment_id','device_id',
                    'app_instance_id','adb_serial','package_name','request_id'):
            if not self.context.get(key):
                raise SlotBlocked('outreach_slot_missing_' + key)
        self.start = _timestamp(context['slot_start'])
        self.end = _timestamp(context['slot_end'])
        if self.end-self.start != 2400 or context['package_name'] != 'com.instagram.android':
            raise SlotBlocked('outreach_slot_contract_invalid')
        self.anchor_wall, self.anchor_mono = wall(), monotonic()
        self.target = int(context['target_dm'])
        self.sent = int(context.get('sent_confirmed_in_slot',0))
        self.claimed = context.get('claimed_jobs_in_slot')
        if self.target <= 0 or self.sent < 0:
            raise SlotBlocked('outreach_slot_counter_invalid')
        self.stop_reason = None
        self.observations = []
        self.unit_started = None
        self.device_verified = False
        self.finished = False
        self.policy = None

    def remaining_ms(self):
        # Clock rollback cannot buy more time inside a process.
        now = max(self.wall(), self.anchor_wall + self.monotonic()-self.anchor_mono)
        return max(0.0, (self.end-now)*1000)

    def event(self,name,**fields):
        self.emit('info',name,**{**self.context,'sent_confirmed':self.sent,
            'remaining_target':max(0,self.target-self.sent),'remaining_ms':self.remaining_ms(),**fields})

    def check(self, *, unit=False):
        if self is _ACTIVE:
            from cooperative_business_stop import StopContext, read_stop
            intent = read_stop(StopContext.from_env())
            if intent:
                reason = 'slot_deadline' if intent['reason']=='scheduled_business_deadline' else 'human_manual_stop'
                self.stop_reason = reason
                raise SlotBlocked(reason)
        if self.wall()<self.start or self.remaining_ms()<=0:
            self.event('outreach_slot_deadline_reached')
            self.stop_reason = 'slot_deadline'
            raise SlotBlocked('slot_deadline')
        from outreach_measurements import validate_policy
        try:
            policy = validate_policy(self.context, self.wall())
        except ValueError as exc:
            raise SlotBlocked(str(exc)) from exc
        if unit and policy['mode'] == 'normal':
            if self.remaining_ms() < policy['unit_ms'] + policy['cleanup_ms']:
                raise SlotBlocked('outreach_insufficient_next_unit_budget')
        self.event('outreach_slot_budget_remaining')

    def refresh(self):
        fresh=self.rpc('outreach_slot_context_v1',{'p_request_id':self.context['request_id']})
        keys = ('slot_id','slot_start','slot_end','account_id','assignment_id','device_id','app_instance_id','adb_serial','package_name','target_dm')
        if self.context.get('managed_campaign') is True:
            keys += ('managed_campaign', 'client_id', 'campaign_id', 'campaign_revision')
        for key in keys:
            if fresh.get(key)!=self.context.get(key):
                raise SlotBlocked('outreach_slot_context_changed')
        self.sent=int(fresh['sent_confirmed_in_slot'])
        claimed = fresh.get('claimed_jobs_in_slot')
        if self is _ACTIVE and (type(claimed) is not int or claimed < self.sent):
            raise SlotBlocked('outreach_claim_budget_unproven')
        self.claimed = claimed
        return max(0,self.target-self.sent)

    def remaining_claim_budget(self, settings, counters):
        # Reuse the existing quota authority, independently from the product
        # target. Attempt/restart boundaries do not mint another session cap.
        from outreach_session_orchestrator import _resolve_effective_max_jobs
        if type(self.claimed) is not int or self.claimed < self.sent:
            raise SlotBlocked('outreach_claim_budget_unproven')
        quota = _resolve_effective_max_jobs(settings, counters)
        session_cap = min(quota['outreach_per_session_limit'], quota['outreach_hard_max_per_session'])
        return max(0, min(quota['max_jobs_effective'], session_cap - self.claimed))

    def bind_device(self, d):
        from outreach_measurements import sender_identity, validate_policy
        policy = validate_policy(self.context, self.wall())
        info = d.app_info(self.context['package_name'])
        if (policy['sender_identity'] != sender_identity()
                or str(info.get('versionName') or '') != policy['instagram_version']):
            raise SlotBlocked('outreach_measurement_runtime_changed')
        self.device_verified = True

    def begin_unit(self):
        self.check(unit=True)
        self.unit_started = self.monotonic()

    def record_unit(self, result, job_id=None):
        if result.get('outcome') == 'sent' and self.unit_started is not None:
            self.observations.append({'kind': 'dm_unit', 'job_id':job_id, 'duration_ms':
                max(0, (self.monotonic()-self.unit_started)*1000),
                'navigation_ms': result.get('navigation_to_username_typed_total_ms'),
                'observed_at': datetime.fromtimestamp(self.wall(),timezone.utc).isoformat()})
        self.unit_started = None

    def finish(self, *, cleanup_ok, cleanup_ms, code=0, **fields):
        from outreach_terminal_receipt import validate_terminal_receipt
        if self.finished:
            return validate_terminal_receipt(self.terminal, self.context, run_id=fields.get('run_id'))
        from outreach_measurements import sender_identity
        reason = self.stop_reason or ('slot_deadline' if self.remaining_ms() <= 0 else
            'target_reached' if self.sent >= self.target else 'no_safe_work')
        if code != 0:
            reason = 'unsafe_stop' if reason == 'human_manual_stop' else 'technical_failure'
        if not cleanup_ok:
            reason = 'cleanup_failure'
        real_clock = self.wall is time.time and self.monotonic is time.monotonic
        observations = {'measurement_source': 'outreach_real' if self.device_verified and real_clock else 'unverified',
            'clock_source':'monotonic_real' if real_clock else 'synthetic',
            'sender_path':'canonical_outreach', 'environment':'physical_device' if self.device_verified else 'unverified',
            'run_type':'outreach_session','sender_identity':sender_identity(),
            'instagram_version':(self.context.get('execution_policy') or {}).get('instagram_version'),
            'mode':(self.context.get('execution_policy') or {}).get('mode'),
            'successful_units':self.observations,'cleanup_ms':cleanup_ms,
            'promotion_status':'candidate_only'}
        summary=self.rpc('finish_outreach_slot_v2',{'p_request_id':self.context['request_id'],
            'p_reason':reason,'p_cleanup_ok':bool(cleanup_ok),'p_observations':observations})
        summary = validate_terminal_receipt(summary, self.context, run_id=fields.get('run_id'))
        if summary['cleanup_ok'] is not bool(cleanup_ok):
            raise SlotBlocked('outreach_terminal_cleanup_conflict')
        self.sent=int(summary['sent_confirmed_in_slot'])
        self.terminal=summary
        self.finished=True
        self.event('outreach_slot_finished', **{**summary, **fields})
        return summary

    def claim(self):
        self.check(unit=True)
        if self.refresh()<=0:
            return None
        if self is _ACTIVE:
            if not self.device_verified:
                raise SlotBlocked("outreach_measurement_runtime_unverified")
            import supabase_client
            from outreach_session_orchestrator import _is_valid_counter_row
            settings=supabase_client.get_account_dm_settings(self.context['account_id']) or {}
            counters=supabase_client.get_account_dm_counter_today(self.context['account_id'])
            if not _is_valid_counter_row(counters):
                raise SlotBlocked('outreach_counters_unavailable')
            if self.remaining_claim_budget(settings,counters)<=0:
                self.stop_reason = 'no_safe_work'
                return None
        job=self.rpc('outreach_job_boundary_v1',{'p_request_id':self.context['request_id'],'p_action':'claim'})
        self.check()
        if job and job.get('id'):
            if self.context.get('managed_campaign') is True:
                metadata = job.get('metadata') or {}
                if (job.get('account_id') != self.context['account_id']
                        or job.get('campaign_id') != self.context['campaign_id']
                        or metadata.get('client_id') != self.context['client_id']
                        or metadata.get('campaign_revision') != self.context['campaign_revision']
                        or job.get('dm_type') != 'outreach'):
                    raise SlotBlocked('outreach_scoped_claim_binding_mismatch')
            if type(self.claimed) is int:
                self.claimed += 1
            self.event('outreach_job_claimed',job_id=job['id'])
            return job
        return None

    def before_send(self,job_id):
        self.check()
        result=self.rpc('outreach_job_boundary_v1',{'p_request_id':self.context['request_id'],'p_job_id':job_id,'p_action':'before_send'})
        if not result or not result.get('id'):
            raise SlotBlocked('outreach_send_not_authorized')
        if self.context.get('managed_campaign') is True and (result.get('id') != job_id
                or result.get('account_id') != self.context['account_id']
                or result.get('campaign_id') != self.context['campaign_id']):
            raise SlotBlocked('outreach_scoped_send_binding_mismatch')
        self.check()  # RPC latency does not extend the slot.


def active():
    return _ACTIVE

def call_slot_rpc_v21(transport, context, name, payload):
    """Only an execution-bound, explicit DB deadline proof is a normal stop.

    Transport/SQL exceptions propagate unchanged. Terminal writes retain their
    existing RPC and idempotency; no business mutation is retried.
    """
    if name not in ('outreach_slot_context_v1', 'outreach_job_boundary_v1'):
        return transport(name, payload)
    action = 'context' if name == 'outreach_slot_context_v1' else payload['p_action']
    result = transport('outreach_boundary_v21', {
        'p_request_id': context['request_id'], 'p_action': action,
        'p_job_id': payload.get('p_job_id'),
        'p_expected_slot_id': context['slot_id'],
        'p_expected_slot_end': context['slot_end'],
        'p_expected_account_id': context['account_id'],
    })
    if (not isinstance(result, dict) or result.get('schema') != 'OUTREACH_BOUNDARY_V21'
            or result.get('action') != action
            or any(result.get(k) != context.get(k) for k in ('request_id','account_id','slot_id','slot_end'))):
        raise SlotBlocked('outreach_rpc_invalid')
    if result.get('ok') is False and result.get('reason_code') == 'SLOT_DEADLINE_REACHED':
        if _timestamp(result.get('server_observed_at')) < _timestamp(context['slot_end']):
            raise SlotBlocked('outreach_deadline_proof_invalid')
        raise SlotBlocked('slot_deadline')
    if result.get('ok') is not True or 'result' not in result:
        raise SlotBlocked('outreach_rpc_invalid')
    data = result['result']
    if not isinstance(data, dict) and not (action == 'claim' and data is None):
        raise SlotBlocked('outreach_rpc_invalid')
    return data


def initialize_from_environment(*, account_id, serial, package, run_type):
    global _ACTIVE
    raw=os.environ.get('OUTREACH_SLOT_CONTEXT_V1')
    if not raw:
        return None
    from logs import log
    import supabase_client
    context=json.loads(raw)
    if run_type!='outreach_session' or any(context.get(k)!=v for k,v in
       [('account_id',account_id),('adb_serial',serial),('package_name',package)]):
        raise SlotBlocked('outreach_worker_binding_mismatch')
    def transport(name,payload):
        result=supabase_client.call_rpc_once(name,payload,timeout_seconds=5.0)
        if not isinstance(result,dict) and result is not None:
            raise SlotBlocked('outreach_rpc_invalid')
        return result
    def rpc(name,payload):
        return call_slot_rpc_v21(transport, context, name, payload)
    import config
    # A target is not an execution allowance. Both claim and SQL apply the
    # unchanged hard/configured caps to the cumulative slot claim budget.
    if context.get('managed_campaign') is not True or any(not context.get(k) for k in
            ('client_id', 'campaign_id', 'campaign_revision')):
        raise SlotBlocked('outreach_campaign_binding_missing')
    _ACTIVE=SlotBudget(context,rpc=rpc,emit=log)
    _ACTIVE.refresh()
    from outreach_measurements import validate_policy, sender_identity
    policy = validate_policy(_ACTIVE.context, time.time())
    if policy['sender_identity'] != sender_identity():
        raise SlotBlocked('outreach_measurement_sender_changed')
    _ACTIVE.event('outreach_slot_resolved')
    _ACTIVE.event('outreach_slot_started')
    if int(context.get('restart_count',0)):
        _ACTIVE.event('outreach_restart_inside_slot')
    return _ACTIVE
