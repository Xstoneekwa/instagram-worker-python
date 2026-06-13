# Manual-only schedule mode (Phase 1 + Phase 2 rules)

## Phase 1 (implemented)

`schedule_mode` on `account_assignments`:

- `scheduled` — normal recurring window (`starts_at` / `ends_at` required).
- `manual_only` — device + `app_instance_id` reserved; **no** hourly window; **no** auto runtime in Phase 1.

Placement is created via `assign_account_manual_only`. Scheduled slots use `assign_account_slot` unchanged except that overlap checks ignore `manual_only` rows.

`evaluate_account_schedule_gate` returns `manual_only_runtime_disabled` for manual accounts until Phase 2 manual run is implemented.

## Phase 2 (future — not implemented)

### Run tags (required on every manual run)

- `run_trigger=manual`
- `schedule_mode=manual_only`
- `manual_run=true`
- `launched_by`
- `reason`

### Quota policy for manual runs

**Count toward daily social quotas:**

- follow
- unfollow
- like
- welcome DM
- outreach DM
- profile interaction
- any real Instagram action that changes state or contacts someone

**Do not count toward social quotas:**

- `login_provisioning`
- `login_check`
- `readiness`
- `preflight`
- device health check
- dry-run

Manual runs must not bypass quota enforcement.
