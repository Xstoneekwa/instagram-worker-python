# Follow Warmup — Active SAST Days V1

Status: code checkpoint pushed; production activation is recorded separately by
the rollout report. This is a scoped Phone Farm checkpoint, not the final
Frontend/Stripe handover.

## Canonical rule

Warmup is generic for every account and every package. Progress is derived from
distinct `Africa/Johannesburg` business dates that contain at least one
persisted, successful `follow_verified` event linked to a run:

- first active date: cap 10;
- second active date: cap 20;
- third active date: cap 40;
- fourth active date and later: the progressive restriction is removed.

An inactive date, several runs on the same date, or a failed run without a
verified Follow does not advance the warmup day. Package/service start time is
metadata only.

## Limit resolution

Packages provide distinct defaults and maxima for day and session caps. Account
settings are initialized from defaults, can be lowered, and cannot exceed the
package maxima. Warmup never overwrites persisted account settings.

```text
effective_day = min(configured_day, package_max_day, warmup_cap, ops_hard_day)
effective_session = min(configured_session, package_max_session, warmup_cap,
                        ops_hard_session, remaining_effective_day_quota)
```

Day 4+ restores the configured cap subject to package, ops and remaining-quota
limits. Example: configured 50 and package maximum 80 resolves to 50.

## Runtime and observability

The Worker independently recalculates the effective limits at run startup and
keeps the existing global Follow guards authoritative. Structured cap logs must
identify configured, package, warmup, ops and remaining-quota inputs without
credentials or account-specific policy.

Code checkpoint: `51278eadf1613f80349a7930e17420c8d8dd1e64`.

Validation evidence:

- 26 targeted runtime-cap tests;
- 156 existing runtime guards;
- Python compilation and `git diff --check`;
- no account-specific logic and no secret-shaped additions;
- zero device, ADB, Instagram, Auto Login or Follow run.

## Rollback

Rollback means repointing the immutable Worker runtime to the previous certified
release and restarting the dispatcher once. Do not reverse the additive schema
unless a separate reviewed migration is approved. Before and after rollback,
verify queue, requests, runs, locks and account sessions remain empty.
