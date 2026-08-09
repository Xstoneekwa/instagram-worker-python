# Follow60 V2 Golden fallback reduction V1

## Terrain evidence

- Run: `45c8a86c-b657-4d10-93bb-74ade8cbb455`
- Request: `ab171bb5-605e-46c1-9e00-f4f0d9466687`
- Business session: `168fe490-9a55-4b5b-a056-e12074371335`
- Control: `c117a5d3-e426-44e5-a98e-797786a5d698`
- Worker: `3ee85d9a89a15ac3eef65eb13cc850ae57ff2fa8`
- V2 selected/complete/partial: `9/8/1`
- V1/Golden fallbacks: `4/4`

| Candidate | First V2 rejection | Post-Mute evidence | Golden result | Classification |
|---|---|---|---|---|
| `madetohonor_events` | `v2_physical_post_cell_missing` | no physical cell; tabs bounds missing | no viewer | true/persistent ambiguity |
| `cericrains` | `v2_physical_post_cell_missing` | three physical cells; clipped | top-left row 1/column 1 opened | premature transient |
| `ramialqadoumi` | `v2_physical_post_cell_missing` | three physical cells; clipped | top-left row 1/column 1 opened | premature transient |
| `finefoodfam` | `v2_physical_post_cell_missing` | three physical cells; clipped | top-left row 1/column 1 opened | premature transient |

The initial rejection-to-grid delays were 17.679 s, 17.710 s and 17.763 s,
but they include Follow/Mute work and are not a safe wait budget.  After the
single reveal rejected its first fresh XML, Golden observed the absolute
top-left 13.201 s, 13.324 s and 13.446 s later.  Those are upper bounds because
Golden performed its own work in the interval; the exact readiness instant was
not observed.

## Contract

Only a post-reveal rejection caused by a missing/temporarily unsafe physical
top-left row is eligible.  Exact candidate identity, public surface, positive
post count, selected Posts tab, package/activity, overlay absence, one reveal,
old-bounds invalidation and the original UI generation must already be proved.

The retry budget is exactly one hierarchy dump and one in-memory
classification.  It adds no explicit sleep, scroll, screenshot, Vision call or
poll loop.  Package/activity and the stable post-Mute proof are reused; only
the XML-backed grid cells, coordinate frame, loading state and generation are
refreshed.  The same `PostRevealSafeFirstRowV1` contract must pass.  A changed
generation, unsafe surface or persistent ambiguity falls back to Golden.

V5 remains mandatory after any direct post open.  Return CT, next-candidate,
Mute, Follow, Stop, CT Resume and Follow-to-Unfollow code paths are unchanged.

## Final field retry on `6701b2d`

- Run: `cb7dcc07-b3bb-46cf-ac54-ae37cdfde8d7`
- Worker: `6701b2dfc661ae98d97de9b2da2ff03dfc93ecce`
- Canonical totals: 10 Follows and 10 verified Likes.
- Ordering V2: 9 selected, 7 complete, 2 partial; 2 V1/Golden fallbacks.
- Candidate-to-candidate field median: 50.586 s across ten identical
  `opened_at_monotonic_ns` boundaries.
- Pure V2 candidate-to-candidate median: 50.280 s.
- Complete V2 profile-to-Return-CT median/P90/P95:
  41.991 / 43.241 / 43.241 s.
- Return CT exact median/P90/P95: 5.431 / 5.558 / 5.590 s.
- Next candidate median/P90/P95: 7.768 / 8.470 / 8.538 s.

Both residual Golden candidates (`beegreen68`, `fannybhafs`) received exactly
one transient revalidation.  Each revalidation performed one fresh hierarchy
dump and one strict in-memory classification, with no explicit wait,
screenshot, Vision call, second scroll or poll loop.  Durations were 1.759 s
and 2.071 s.  Both refreshed surfaces still failed to prove the absolute
top-left origin after the single reveal, so both correctly remained
`POST_GRID_AMBIGUOUS_FINAL` and entered Golden.  Golden opened the post on the
first tap and V5 stayed mandatory.  This is accepted as residual safety cost;
no additional Golden retry is authorized.

## Future Follow60 V2 production checkpoint criteria

This section prepares the next task only.  It does not promote V2, change
account routing, alter onboarding, create a production tag or activate a
release.

- Functional completion: complete V2 cycles reach exact Return CT and durable
  persistence without unsafe partial receipts.
- Post-first: strict direct-grid evidence is required; ambiguous evidence
  remains fail-closed to Golden.
- V5: mandatory after every direct or Golden post open, including
  Story/Highlight rejection.
- Like: exact target, stage-scoped context and verified terminal outcome.
- Reentry: one exact candidate-profile reentry before Follow.
- Follow: exact CTA, fresh context, verified Following ACK and idempotent
  persistence.
- Mute: Posts and Stories are independently verified; partial Mute never
  becomes a complete cycle.
- Return CT: exact CT Followers proof and terminal ACK are mandatory.
- Next candidate: fresh mapped row, eligibility proof and exact profile ACK.
- See More: one-shot committed-surface progression; zero blind repeat taps.
- CT Resume: checkpoint identity, lease, overlap and commit evidence remain
  certified.
- Barrier: account-scoped baseline, cap and one-shot control are exact.
- Stop: canonical safe quiescence, terminal reconciliation and no false
  success.
- Follow to Unfollow: phase ownership and lineage remain isolated and
  non-regressed.
- Incident recovery: non-security resolution authorizes only the next natural
  recovery; security incidents remain fail-closed.
- Golden residual policy: one bounded micro-revalidation maximum, then Golden;
  no iterative Golden optimization.
- Performance references: 85.226 s Golden historical, 67.192 s prior V2,
  50.586 s current field candidate-to-candidate median.
- Rollback: V1 remains the documented behavioral rollback until a separate V2
  production promotion is explicitly authorized and certified.
