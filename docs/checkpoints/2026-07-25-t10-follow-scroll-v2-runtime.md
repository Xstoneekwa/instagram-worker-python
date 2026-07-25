# Checkpoint T-10, Follow 7+1 and Target Followers V2 runtime

Date: 2026-07-25. This is an intermediate Phone Farm checkpoint, not the final
Frontend/Stripe handover.

## Scope and invariants

This checkpoint contains one Worker behavior change: the legacy Follow list
continuation prefers seven new fully visible rows plus one verified overlap.
It does not change caps, schedules, CT selection, Auto Restart policy,
historical Auto Login 07ee, Welcome DM, Unfollow, Target Followers V2 flags or
enforcement. No physical run, ADB action or manual scheduler tick is authorized
for the delivery.

## T-10 versus Worker startup

| Proof | T-10 backend | Worker startup | Equivalence |
|---|---|---|---|
| Account already `connected` + `ready` | Skips before request creation | Not entered because no request exists | YES for the no-phone result |
| Assignment, device, clone and package binding | Reads the lightweight canonical assignment projection | Resolves and validates the exact assignment, device, app instance, serial and package | PARTIAL; Worker is stricter |
| Foreground package | No Android action for the skip path | Exact package guard before business actions | NO direct T-10 probe; safe because the skip opens no phone |
| Instagram identity | No Android action for the skip path | Historical Identity Guard and exact own-profile username | NO direct T-10 probe; safe because the skip opens no phone |
| Existing request/run/lock | Read before enqueue | Validated again at claim/startup | YES with Worker revalidation |
| Failure handling | Does not enqueue when the account is already connected and ready | Canonical incident and safe-stop paths | YES for the skip; Worker owns runtime failure handling |

The route parser treats empty or invalid values as defaults. Production
inspection found both `INSTAGRAM_LOGIN_PREFLIGHT_CRON_ENABLED` and
`INSTAGRAM_LOGIN_PREFLIGHT_CRON_DRY_RUN` present with empty values, which means
`enabled=false` and `dryRun=true`. The operational values for the natural cron
are `true` and `false` respectively. `CRON_SECRET` remains the sole caller
secret. No valid cron invocation is part of this checkpoint.

The natural completed sessions proved the stricter Worker path before business
actions: exact assigned package, expected phone, canonical username and
Identity Guard. Therefore no T-10 code or Worker startup patch is required.

Verdict: `CONNECTED_READY_T10_SKIP_CONFIRMED_SAFE`.

## Natural Follow evidence before the patch

| Account | Natural result | Scrolls | Old new rows | Overlap | Continuity | Fallback |
|---|---|---:|---|---|---:|---:|
| `lorielebras_autom` | 20 follows, 18 likes, `global_follow_cap_reached` | 3 | 6, 7, 4 | 2, 2, 2 | 100% | 0% |
| `mythyl_fitness` | 40 follows, 39 likes, `global_follow_cap_reached` | 7 | 6, 7, 4, 6, 6, 6, 7 | 2, 2, 3, 2, 2, 2, 2 | 100% | 0% |

The two supplied screenshots correlate with the XML/runtime evidence. The
Loriele viewport was the normal 6+2 case. The Mythyl viewport included a
duplicated or truncated accessibility identity and a partial bottom row; the
old identity-deduplicated geometry shortened the gesture even though the
physical cadence contained more rows. This is not explained by phone height
alone.

Observed scroll plus rescan averages were approximately 3.59 seconds for
Loriele and 3.41 seconds for Mythyl. The expected geometry-only gain is about
1.33 additional rows per Loriele scroll and 1 additional row per Mythyl scroll.
These are projections; runtime performance of the new geometry remains pending
because this delivery performs no physical validation.

## Follow 7+1 contract

- Geometry uses physical centers of fully visible primary rows; identity
  deduplication remains authoritative for fingerprints and continuity.
- Partial top or bottom rows are excluded from cadence, anchor and exploitable
  row counts.
- The preferred viewport is seven new rows plus one verified overlap.
- A previous overlap of three or more adds at most half a measured row and no
  more than four percent of viewport height to the next gesture.
- Overlap one or two keeps the normal distance.
- Zero overlap is never reusable and still triggers the established excessive
  scroll refusal, correction and bounded fallback path.
- Maximum adaptive distance remains bounded to 56% of viewport height and to
  the validated 0.78 to 0.22 safe band.
- Safe 6+2, 5+3 and deterministic 0.24-height fallback behaviors remain
  available when the measured surface cannot support 7+1.

New diagnostics include full and partial row counts, requested and effective
ratio, target new rows, target overlap, actual new rows, actual overlap,
verified anchor, adaptation reason, fallback reason and continuity result.

Verdicts:

- `FOLLOW_SCROLL_TARGET_7_NEW_1_OVERLAP`
- `OVERLAP_3_REDUCED_TO_RECOVERY_ONLY`
- `PARTIAL_ROWS_EXCLUDED_FROM_SAFE_ANCHOR_CALCULATION`
- `ZERO_OVERLAP_REMAINS_FORBIDDEN`
- `FOLLOW_GOLDEN_SAFETY_PRESERVED`

## Target Followers Progressive Resume V2 natural observation

Only `mythyl_fitness` entered the UUID allowlist. Shadow was true and enforce
was false. Two CT checkpoints were created at depth zero. The runtime loaded
the checkpoint, built the resume plan, claimed the shadow lease and attempted
bounded commits. Six commits conflicted with `lease_expired`; legacy navigation
continued and reached the natural Follow cap.

The database contains two Mythyl checkpoints and two redacted `claimed` events.
It contains no persisted depth advance for this run. Loriele,
`j_automatise_pour_toi` and `i_m_your_traker` contain no V2 checkpoint or event.
No V2 patch or enforcement activation is authorized.

Verdicts:

- `MYTHYL_V2_SHADOW_RUNTIME_EXECUTION_CERTIFIED`
- `V2_TRIGGERED_BUT_FAILED_OPEN`
- `V2_LEGACY_AUTHORITY_PRESERVED`
- `ZERO_V2_RUNTIME_FOR_OTHER_ACCOUNTS`

## Validation and rollout gates

Before commit, the targeted continuation contract passed 38/38, the grouped
Follow/Welcome/Unfollow/navigation tests passed 154/154, the grouped Auto Login
07ee, Identity Guard, dispatcher/preflight, incidents, notifications and V2
tests passed 247/247, and the complete non-physical Worker suite passed
1883/1883. Final `py_compile`, no-leak, exact staged allowlist and
`git diff --check` are required after documentation is complete.

Production rollout requires an immutable release, one atomic symlink switch,
one dispatcher restart protected by the one-shot startup-tick guard, a unique
PID, canonical runtime root and zero request/run/lock. The next scheduler and
T-10 checks must be natural observations only.

BotApp is an observability surface. The prepared Scheduler package may be
installed only after its tests, TypeScript, build, package, signature, `app.asar`
and no-leak gates pass. Profiles, Scheduler and Incidents are read-only smokes;
no CTA may be used.

Rollback restores the previous immutable Worker symlink with the same one-shot
startup guard and one dispatcher restart, restores the previous official
BotApp backup if its projections regress, and reverts only the two T-10 boolean
environment values if the natural cron behaves unexpectedly. Stored V2 audit
rows are not deleted.
