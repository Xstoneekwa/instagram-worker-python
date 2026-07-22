# WORKER_FOLLOW_LIMIT_SHADOW_V1

Status: local passive checkpoint. Not committed, deployed, or active in production.

## Authority boundary

`resolve_follow_runtime_limits()` remains unchanged and is the only resolver whose
result controls `global_follow_goal_effective`, `_follow_max_per_run`, Follow loop
stops, counters, exit codes, resume, and Auto Restart. The shadow calculation is
called after that legacy result exists. Its return value is used only to build one
structured local log event and is never assigned to a runtime cap.

`FOLLOW_LIMIT_PROVENANCE_SHADOW_ENABLED` defaults to `false`. When absent or false,
the runner performs no shadow calculation and emits no shadow log. There is no
enforce mode: enforcement is `NOT_IMPLEMENTED / NO_GO` for this checkpoint.

## Canonical transport contract

The current production `account_package_summary` projection does not expose the
new contract. The existing once-per-session Follow input load contains a passive
transport slot named `canonical_follow_limit_payload`, populated only if that same
projection eventually exposes `follow_limit_provenance`. The shadow adds no table,
RPC, HTTP call, retry, or UI-loop request.

An evaluable payload contains:

- matching `account_id` and a Follow-capable package (`Growth`, `Pro`, or `Premium`);
- positive package day/session limits;
- an absent override with null values, or an explicit override with a bounded
  provenance (`admin`, `support`, `migration_confirmed`);
- coherent warmup state and positive caps when enabled;
- positive business-effective limits bounded by package, override, and warmup;
- a bounded limiting source and reason;
- no unknown fields at any consumed level.

No value is inferred from `max_actions_per_day`, `follow_limit`, or
`max_follow_per_run`. An absent required section is `not_evaluable`; a contradictory,
unbounded, mismatched, negative, or malformed value is `invalid_payload`.

## Passive runtime calculation

The pure module applies only Worker-owned protections:

```text
shadow day = min(canonical business day, optional Ops day hard cap)
shadow session = min(canonical business session, optional Ops session hard cap)
shadow remaining = max(0, shadow day - completed today)
shadow run = min(shadow session, shadow remaining, optional run hard cap)
```

The calculation mutates no input, config, counter, database row, or device state.
It imports neither the runner nor device/network libraries. Any integration
exception is caught, produces a redacted `not_evaluable` event, and leaves the
already-resolved legacy values untouched.

## Stable statuses and classifications

Statuses: `evaluated`, `disabled`, `not_evaluable`, `invalid_payload`.

Classifications: `exact_match`, `numeric_match_source_difference`,
`canonical_lower_than_legacy`, `canonical_higher_than_legacy`,
`mixed_difference`, `canonical_not_evaluable`, `canonical_payload_invalid`, and
`shadow_disabled`.

## Structured logs

Logs exist only when the flag is true:

- `follow_limit_provenance_shadow_evaluated`;
- `follow_limit_provenance_shadow_not_evaluable`;
- `follow_limit_provenance_shadow_payload_invalid`.

The logger receives only identifiers already present in session context, package,
numeric legacy/canonical/shadow caps, classification/deltas, bounded source/reason,
and flag state. Raw projections, configuration objects, credentials, tokens,
Stripe data, and secrets are excluded.

## Offline reconciliation fixtures

- `j_automatise_pour_toi`: Growth 80/80, no override, Day 2 warmup 20/20.
  Expected comparison against legacy numeric 20/20 with a legacy source mismatch:
  `numeric_match_source_difference`.
- `i_m_your_traker`: Pro 120/120 and future explicit 120/20 override. Warmup is
  absent from the certified snapshot, so the fixture is `not_evaluable`.
- `mythyl_fitness`: Pro 120/120 and future explicit 120/40 override. Warmup is
  absent from the certified snapshot, so the fixture is `not_evaluable`.

The fixtures are redacted and offline. They do not represent a production read.

## Activation criteria and rollback

Future order, each requiring a separate approval:

1. apply the backend migration;
2. backfill only confirmed explicit overrides;
3. deploy an account-bound canonical projection;
4. activate Worker shadow for selected accounts;
5. review comparisons and invalid/not-evaluable rates;
6. connect BotApp to the projection;
7. make a separate enforcement decision.

Rollback is setting `FOLLOW_LIMIT_PROVENANCE_SHADOW_ENABLED=false` or removing the
passive hook. Because the legacy resolver is unchanged and remains authoritative,
rollback requires no quota, counter, database, session, or device repair.

## Known gaps

- Backend migration and canonical transport are not deployed.
- No override backfill exists.
- No live shadow was run.
- The repository has no canonical virtual environment. Launchd resolves the
  Command Line Tools Python 3.9 interpreter and its user-site dependencies; tests
  must not install or modify them.
- Enforcement remains explicitly out of scope and prohibited.
