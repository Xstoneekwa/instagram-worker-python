"""Strict dedicated-outreach receipt validation; no IO and no full_cycle use."""
from datetime import datetime

SCHEMA = 'OUTREACH_TERMINAL_V22'
IDENTITY = ('request_id', 'slot_id', 'root_request_id', 'account_id',
            'assignment_id', 'device_id', 'app_instance_id', 'package_name')


class InvalidTerminalReceipt(ValueError):
    pass


def validate_terminal_receipt(receipt, context, *, run_id):
    def require(ok, field):
        if not ok:
            raise InvalidTerminalReceipt('outreach_terminal_receipt_invalid:' + field)

    require(type(receipt) is dict and type(context) is dict, 'object')
    require(receipt.get('schema_version') == SCHEMA, 'schema_version')
    for key in (*IDENTITY, 'run_id'):
        expected = run_id if key == 'run_id' else context.get(key)
        if key == 'root_request_id' and type(context.get('attempt_id')) is int and context['attempt_id'] == 1:
            expected = context.get('request_id')
        require(type(expected) is str and bool(expected.strip()), 'expected_' + key)
        require(type(receipt.get(key)) is str and receipt[key] == expected, key)
    for key in ('attempt_id', 'target_dm'):
        require(type(context.get(key)) is int and context[key] > 0, 'expected_' + key)
        require(type(receipt.get(key)) is int and receipt[key] == context[key], key)
    if context.get('managed_campaign') is True:
        for key in ('client_id', 'campaign_id'):
            require(type(context.get(key)) is str and bool(context[key].strip()), 'expected_' + key)
            require(type(receipt.get(key)) is str and receipt[key] == context[key], key)
        require(type(context.get('campaign_revision')) is int and context['campaign_revision'] > 0,
                'expected_campaign_revision')
        require(type(receipt.get('campaign_revision')) is int
                and receipt['campaign_revision'] == context['campaign_revision'], 'campaign_revision')
    for key in ('sent_confirmed_in_slot', 'remaining_target', 'ambiguous_send_count'):
        require(type(receipt.get(key)) is int and receipt[key] >= 0, key)
    sent, target, remaining = (receipt[k] for k in
                              ('sent_confirmed_in_slot', 'target_dm', 'remaining_target'))
    require(sent <= target and remaining == target - sent, 'counters')
    for key in ('cleanup_ok', 'business_failure', 'partial_completion'):
        require(type(receipt.get(key)) is bool, key)
    require(receipt['partial_completion'] == (remaining > 0), 'partial_completion')
    persisted = receipt.get('persisted_at')
    require(type(persisted) is str and bool(persisted.strip()), 'persisted_at')
    try:
        stamp = datetime.fromisoformat(persisted.replace('Z', '+00:00'))
        start = datetime.fromisoformat(context['slot_start'].replace('Z', '+00:00'))
        require(stamp.tzinfo is not None and start.tzinfo is not None and stamp >= start, 'persisted_at')
    except (ValueError, TypeError, KeyError):
        raise InvalidTerminalReceipt('outreach_terminal_receipt_invalid:persisted_at')
    reason = receipt.get('stop_reason')
    require(type(reason) is str and reason in {
        'slot_deadline', 'target_reached', 'outreach_insufficient_next_unit_budget',
        'no_safe_work', 'unsafe_stop', 'human_manual_stop', 'technical_failure',
        'cleanup_failure', 'dispatcher_watchdog'}, 'stop_reason')
    # Match the SQL producer's precedence, not just membership in an allowlist.
    if not receipt['cleanup_ok'] or reason in ('technical_failure', 'cleanup_failure', 'dispatcher_watchdog'):
        state = 'TECHNICAL_FAILURE'
    elif receipt['ambiguous_send_count'] or reason in ('unsafe_stop', 'human_manual_stop'):
        state = 'UNSAFE_STOP'
    elif remaining == 0:
        state = 'TARGET_REACHED'
    elif reason in ('slot_deadline', 'outreach_insufficient_next_unit_budget'):
        state = 'SLOT_DEADLINE_PARTIAL'
    else:
        state = 'COMPLETED_PARTIAL'
    require(receipt.get('terminal_class') == state, 'terminal_class')
    failure = (not receipt['cleanup_ok'] or receipt['ambiguous_send_count'] > 0 or
               reason in ('unsafe_stop', 'technical_failure', 'cleanup_failure', 'dispatcher_watchdog'))
    require(receipt['business_failure'] == failure, 'business_failure')
    require(reason != 'target_reached' or remaining == 0, 'target_reached')
    require(reason != 'cleanup_failure' or not receipt['cleanup_ok'], 'cleanup_failure')
    return dict(receipt)


def receipt_allows_success(receipt):
    """Only call with a validated receipt."""
    return (receipt['cleanup_ok'] is True and receipt['business_failure'] is False
            and receipt['terminal_class'] in
            ('TARGET_REACHED', 'SLOT_DEADLINE_PARTIAL', 'COMPLETED_PARTIAL'))
