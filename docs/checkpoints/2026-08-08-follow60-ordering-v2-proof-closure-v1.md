# Follow60 Ordering V2 proof closure V1

Status: source/test proof closure only. Follow60 V1 remains the only production
behavior engine. Ordering V2 behavior and enforcement remain OFF. No runtime,
device, production database, scheduler, or account setting was changed.

## Evidence corpus and strict boundary

The offline analyzer reads these immutable JSONL logs:

- `ff58e380-05ee-4bd5-b211-4ba3f76b6d08`
- `ee2a2fdb-b0f9-4976-aeae-d29b95840ada`

Corpus: two runs, 33 complete cycles, 27 `DIRECT_GRID_SAFE`, and six No Posts.
The raw avoidable interval begins at `post_follow_post_likes_phase_started` and
ends either at `post_open_intent_v2_consumed` immediately before the physical
tap, or at the explicit no-tap Golden terminal. Viewer ACK, V5, Like, Follow,
Mute, and Return CT are excluded.

This corrects the former Shadow shortcut that equated the entire Golden stage
with avoidable work and could include viewer/V5 work.

| Candidate | V1 path | Raw avoidable (s) | Source evidence | Confidence |
|---|---:|---:|---|---|
| buildings.ma | GOLDEN_DIRECT | 20.563 | pre-tap intent | HIGH |
| meddesign40 | GOLDEN_DIRECT | 20.316 | pre-tap intent | HIGH |
| oumaimaessytale | GOLDEN_DIRECT/no tap | 13.377 | explicit no-tap terminal | HIGH |
| fadma_bf | GOLDEN_DIRECT | 21.564 | pre-tap intent | HIGH |
| imane.makhlouk | GOLDEN_DIRECT/no tap | 13.176 | explicit no-tap terminal | HIGH |
| c_c.l3_ons | GOLDEN_DIRECT | 18.113 | pre-tap intent | HIGH |
| nataliagvindadze | GOLDEN_DIRECT | 18.738 | pre-tap intent | HIGH |
| 96627ray | SAFE_DIRECT | 2.143 | pre-tap intent | HIGH |
| pauline.credot | GOLDEN_DIRECT | 18.986 | pre-tap intent | HIGH |
| axel.lc_ | GOLDEN_DIRECT | 18.532 | pre-tap intent | HIGH |
| a.y.k.o_ | GOLDEN_DIRECT/no tap | 15.390 | explicit no-tap terminal | HIGH |
| solexec | GOLDEN_DIRECT/no tap | 12.231 | explicit no-tap terminal | HIGH |
| rubensfournis | GOLDEN_DIRECT/no tap | 12.412 | explicit no-tap terminal | HIGH |
| erika__cd | GOLDEN_DIRECT/no tap | 15.519 | explicit no-tap terminal | HIGH |
| jinyiiii_cc | GOLDEN_DIRECT | 19.612 | pre-tap intent | HIGH |
| nolan.cuenca | GOLDEN_DIRECT/no tap | 12.413 | explicit no-tap terminal | HIGH |
| douli_sama | GOLDEN_DIRECT/no tap | 12.719 | explicit no-tap terminal | HIGH |
| mariejuumentier | GOLDEN_DIRECT | 19.504 | pre-tap intent | HIGH |
| the25th_alex | GOLDEN_DIRECT | 18.992 | pre-tap intent | HIGH |
| nahim.gouv.fr | GOLDEN_DIRECT/no tap | 12.470 | explicit no-tap terminal | HIGH |
| vika_kostuhina | GOLDEN_DIRECT | 18.637 | pre-tap intent | HIGH |
| arun__ls | GOLDEN_DIRECT | 18.558 | pre-tap intent | HIGH |
| chloeschollhammer | GOLDEN_DIRECT | 19.345 | pre-tap intent | HIGH |
| tiphaine_dsa | GOLDEN_DIRECT | 18.880 | pre-tap intent | HIGH |
| jimin_kang0907 | GOLDEN_DIRECT/no tap | 12.471 | explicit no-tap terminal | HIGH |
| amos_mtb_ | GOLDEN_DIRECT/no tap | 12.105 | explicit no-tap terminal | HIGH |
| jeanne_lsre | GOLDEN_DIRECT/no tap | 12.330 | explicit no-tap terminal | HIGH |

Coverage is 27/27 (100%). Raw avoidable mean is 15.892 s, median 18.113 s,
P90 19.894 s, P95 20.489 s, min 2.143 s, max 21.564 s.

## Reentry: common work versus incremental V2 work

The previously reported 4.640 s was `ct_poll_elapsed_ms`, a candidate-to-CT
poll duration. It is not a new V2 reentry charge.

Fifteen observed Post opens provide exact Post/Like terminal-to-candidate
boundaries:

| Subphase | N | Median | P90 | P95 |
|---|---:|---:|---:|---:|
| terminal -> Back dispatch | 15 | 0.507 s | 0.534 s | 0.578 s |
| Back dispatch -> profile detected | 15 | 1.138 s | 1.242 s | 1.328 s |
| profile detected -> candidate exact proof | 15 | 0.925 s | 1.084 s | 1.111 s |
| common return boundary | 15 | 2.531 s | 2.816 s | 2.971 s |
| existing screen/overlay guard | 27 | 0.492 s | 0.536 s | 0.550 s |
| existing exact FollowTapContext | 27 | 1.238 s | 1.329 s | 1.352 s |
| existing guard + FollowTapContext | 27 | 1.730 s | 1.856 s | 1.885 s |

The future minimal envelope is therefore about 4.261 s median
(2.531 + 1.730), about 4.672 s at P90, and about 4.855 s at P95. This is not a
4.261 s incremental V2 cost: V1 already pays the return boundary after Like
and already pays the exact Follow guard before Follow. V2 relocates the latter
after Back. With a deferred-not-duplicated implementation, incremental UI
acquisition is zero. The pure validator adds 0.008417 ms median CPU, 0.008916
ms P90, and 0.009166 ms P95 over 100,000 iterations.

Level 0 cannot authorize Follow without a fresh post-Back surface. Level 1 is
the required happy path: one fresh surface, exact candidate, exact current
Follow CTA/bounds, package/activity, overlay/challenge absence, and current UI
generations. Level 2 is fallback only. Full profile parsing, filters,
eligibility, Posts count, tab/grid classification, and Vision are not required.

### Stable proof reuse

| Proof | Decision | Reason |
|---|---|---|
| account/request/run/business-session/attempt/binding/Worker SHA | REUSE_SAFE | immutable lineage |
| target, source CT, candidate, action ID | REUSE_SAFE | immutable cycle binding |
| filter and eligibility verdicts | REUSE_SAFE | business verdict unchanged during viewer roundtrip |
| Follow budget reservation | REUSE_SAFE | action-scoped reservation |
| public/private verdict | REUSE_SAFE | business evidence survives; UI identity does not |
| Posts count/source and Posts tab identity | REUSE_SAFE | not needed to authorize post-Back Follow |
| viewer and V5 candidate provenance | REUSE_SAFE | binds the Back origin; never authorizes Follow alone |

### Volatile proof refresh

| Proof | Decision | Reason |
|---|---|---|
| package/activity | REFRESH_REQUIRED | app/activity may change |
| candidate profile surface and exact visible identity | REFRESH_REQUIRED | Back invalidates viewer surface evidence |
| Follow CTA state and current bounds | REFRESH_REQUIRED | old bounds are always stale |
| overlay/challenge absence | REFRESH_REQUIRED | asynchronous safety condition |
| navigation/UI generations | REFRESH_REQUIRED | Back creates a new generation |

The fail-closed validator rejects wrong candidate, wrong package/activity,
non-Follow CTA, invalid/stale bounds, overlays/challenges, stale generations,
consumed proof, and proof older than 1,250 ms.

## Durable Like-before-Follow contract

`FOLLOW60_ORDERING_V2_LEDGER_V1` is a dormant source and PostgreSQL test
contract. Its exact idempotency scope is account, run, request, business
session, target, action, normalized candidate, ordering version, and action
type. Each stage produces one immutable receipt and one outbox row.

The state machine is:

`profile_certified -> post_opened -> (like_verified | like_skipped) ->
profile_reentry_verified -> follow_pending -> (follow_verified |
follow_failed) -> mute_posts_verified -> mute_stories_verified ->
return_ct_exact -> cycle_complete`.

`stop_recorded` is orthogonal. A Stop after Like preserves `like_verified`,
leaves the cycle incomplete, invents no Follow/Mute, and resumes at
`profile_reentry_verified`. ACK replay is a duplicate no-op. Conflicting
payloads and illegal transitions fail closed. A Follow failure cannot clear a
real Like or produce `cycle_complete`.

PostgreSQL 17 local/test replay passed all ten required scenarios plus scope
isolation and illegal-transition rejection. `public` has no privileges on the
test schema, tables, or functions. This fixture is explicitly not a production
migration.

Local PostgreSQL benchmark, 500 samples per operation:

| Operation | Median | P90 | P95 |
|---|---:|---:|---:|
| insert + ledger update + outbox | 0.090 ms | 0.149 ms | 0.183 ms |
| Like receipt RPC | 0.087 ms | 0.135 ms | 0.165 ms |
| duplicate replay | 0.058 ms | 0.090 ms | 0.114 ms |
| outbox ACK | 0.016 ms | 0.024 ms | 0.054 ms |

Four additional ledger stage receipts can be piggybacked in the existing
persistence path: estimated median 0.360 ms, P90 0.596 ms, P95 0.732 ms. No
extra synchronous network round-trip is required by the source design.

## Economic recertification

Fifteen V1 paths already paid Post tap, V5, Like, and return-to-candidate. No
new action-pipeline cost is charged to them. Twelve V1 no-tap paths did not;
they are charged the measured tap-to-exact-candidate median 14.530 s, and the
conservative model charges P95 19.744 s. This avoids assuming zero cost.

| Metric | Standard | Conservative |
|---|---:|---:|
| eligible N | 27 | 27 |
| eligible V1 median | 58.325 s | 58.325 s |
| eligible V2 median | 44.649 s | 48.813 s |
| eligible V2 P90 | 49.508 s | 52.097 s |
| eligible V2 P95 | 51.465 s | 55.094 s |
| eligible net-gain median | 18.112 s | 18.112 s |
| eligible weighted mean gain | 9.434 s | 7.117 s |
| eligible worst paired gain | -2.425 s | -7.639 s |
| global N / eligibility | 33 / 81.818% | 33 / 81.818% |
| global V2 median | 43.911 s | 45.080 s |
| global V2 P90 | 48.901 s | 51.429 s |
| global V2 P95 | 50.832 s | 54.184 s |
| global weighted mean gain | 7.719 s | 5.823 s |

The conservative global median paired gain is 0 because six No Posts paths
remain unchanged and several no-tap paths pay a new action pipeline. The gate
named “global conservative gain” is satisfied by the weighted mean (5.823 s),
not by concealing this bimodal distribution. The eligible median gain remains
18.112 s. The historical 2.5 s reentry target is not used as an artificial
blocker.

## Behavioral canary plan (documentation only)

Future explicit source GO may implement
`FOLLOW60_ORDERING_V2_BEHAVIORAL_CANARY_V1` as follows:

1. Certify candidate under V1 and journal `profile_certified`.
2. If and only if the fresh initial classification is `DIRECT_GRID_SAFE`,
   consume fresh top-left bounds once. Stale bounds are forbidden.
3. Open Post, require V5, then persist `like_verified` or explicit
   `like_skipped`.
4. Back once; obtain the Level 1 minimal fresh proof. Wrong profile or any
   ambiguous/stale signal fails closed to V1 before a V2 Follow tap.
5. Journal `follow_pending`, tap exact current Follow bounds, and persist the
   verified terminal.
6. Reuse unchanged V1 Mute, Return CT, persistence, Stop, and recovery.
7. For every non-eligible/ambiguous case, retain Follow60 V1 unchanged.

Canary constraints: Rex only, explicit SHA-scoped control, maximum ten initial
cycles, no inheritance, V1 fallback before any V2 action, V5 mandatory, and
immediate rollback to V1. No part of this plan is active in this checkpoint.

## Gates

- Corpus >= 30: PASS (33).
- Raw coverage >= 90%: PASS (100%).
- Eligible median net gain >= 8 s: PASS (18.112 s).
- Global conservative weighted mean gain >= 5 s: PASS (5.823 s).
- Durable/idempotent/fail-closed ledger: PASS locally.
- Stale bounds forbidden: PASS in source validator.
- V5 mandatory and top-left contract unchanged: PASS by non-modification.
- Wrong-profile authorization excluded: PASS in source validator.
- V1 fallback intact and production behavior unchanged: PASS.

Final proof-closure verdict: `GO_FOR_EXPLICIT_V2_BEHAVIORAL_CANARY_SOURCE`,
not GO for activation. A separate explicit source authorization and a new
reviewed runtime SHA remain mandatory.
