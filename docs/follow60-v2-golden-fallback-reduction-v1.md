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
