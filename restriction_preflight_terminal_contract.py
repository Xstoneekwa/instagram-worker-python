"""Dedicated terminal proof for a restriction-only physical verification.

This is not a business phase result. Never synthesize Follow/Unfollow success.
The dispatcher validates persisted facts against the current canonical request
and the cleared hold, independently of the child process exit code.
"""
from typing import Any
from instagram_action_restriction import validate_restriction_preflight_policy

REASON = 'restriction_physical_preflight_passed'
SCHEMA = 'RESTRICTION_PREFLIGHT_TERMINAL_V1'


def success_summary(*, account_id, request_id, run_id, policy, released):
    valid, reason, incident_id = validate_restriction_preflight_policy(policy)
    if not valid or released.get('ok') is not True or released.get('hold_status') != 'cleared':
        raise ValueError(reason if not valid else 'restriction_hold_release_unproven')
    binding = dict(account_id=account_id, request_id=request_id, run_id=run_id,
                   incident_id=incident_id, authorization_id=policy.get('authorization_id'),
                   resume_plan_id=policy.get('resume_plan_id'), hold_id=released.get('hold_id'))
    if any(not isinstance(v, str) or not v.strip() for v in binding.values()):
        raise ValueError('restriction_preflight_terminal_binding_missing')
    return dict(reason=REASON, physical_preflight_passed=True,
                restriction_preflight_only=True, business_actions_executed=0, preflight_cleanup_completed=False,
                incident_id=incident_id,
                restriction_preflight_terminal_contract=dict(schema=SCHEMA, **binding))


def validate_terminal(*, request: dict, run: dict, hold: dict,
                      cleanup_completed: bool, lock_released: bool) -> dict[str, Any]:
    record = lambda value: value if isinstance(value, dict) else {}
    request, run, hold = record(request), record(run), record(hold)
    metadata = record(request.get('metadata_safe'))
    policy = record(metadata.get('resume_plan'))
    summary = record(run.get('performance_summary'))
    proof = record(summary.get('restriction_preflight_terminal_contract'))
    valid, _, incident = validate_restriction_preflight_policy(policy)
    zero = lambda v: type(v) is int and v == 0
    checks = {
        'request': request.get('requested_run_type') == 'account_session'
            and metadata.get('recovery_mode') == 'human_confirmed_resume'
            and metadata.get('restriction_preflight_only') is True and valid
            and request.get('status') in ('running', 'completed')
            and not request.get('cancel_requested_at'),
        'binding': proof.get('schema') == SCHEMA and all(
            isinstance(value, str) and bool(value) and proof.get(key) == value
            for key, value in {
                'request_id': request.get('id'), 'run_id': request.get('run_id'),
                'account_id': request.get('account_id'), 'incident_id': incident,
                'authorization_id': metadata.get('authorization_id'),
                'resume_plan_id': metadata.get('resume_plan_id'), 'hold_id': hold.get('id'),
            }.items()) and policy.get('authorization_id') == metadata.get('authorization_id')
            and policy.get('resume_plan_id') == metadata.get('resume_plan_id')
            and policy.get('account_id') == request.get('account_id')
            and metadata.get('incident_id') == incident,
        'run': run.get('id') == request.get('run_id')
            and run.get('account_id') == request.get('account_id')
            and run.get('status') == 'completed' and bool(run.get('finished_at')),
        'physical': summary.get('reason') == REASON
            and summary.get('physical_preflight_passed') is True
            and summary.get('restriction_preflight_only') is True,
        'zero_business': zero(summary.get('business_actions_executed'))
            and all(zero(run.get(k)) for k in ('total_follow','total_like','total_dm','total_story')),
        'cleared_hold': hold.get('status') == 'cleared'
            and hold.get('account_id') == request.get('account_id')
            and hold.get('incident_id') == incident
            and hold.get('verified_by_run_id') == run.get('id')
            and bool(hold.get('verified_cleared_at')),
        'cleanup': cleanup_completed is True and lock_released is True
            and summary.get('preflight_cleanup_completed') is True,
    }
    return {'ok': all(checks.values()), 'checks': checks,
            'reason': REASON if all(checks.values()) else 'restriction_preflight_terminal_proof_invalid'}


def load_terminal_evidence(*, run_id: str, account_id: str, incident_id: str):
    """Read exact terminal counters and cleared hold, not the active-hold API."""
    import supabase_client
    runs = supabase_client._request_json('GET', 'ig_runs', query={
        'id': f'eq.{run_id}', 'account_id': f'eq.{account_id}', 'limit': '1',
        'select': 'id,account_id,status,finished_at,performance_summary,total_follow,total_like,total_dm,total_story'}) or []
    holds = supabase_client._request_json('GET', 'instagram_account_restriction_holds', query={
        'account_id': f'eq.{account_id}', 'incident_id': f'eq.{incident_id}',
        'status': 'eq.cleared', 'verified_by_run_id': f'eq.{run_id}', 'limit': '1',
        'select': 'id,account_id,incident_id,status,verified_by_run_id,verified_cleared_at'}) or []
    return (runs[0] if len(runs) == 1 else {}, holds[0] if len(holds) == 1 else {})
