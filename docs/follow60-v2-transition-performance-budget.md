# Follow60 V2 transition operation budget

This budget covers only the proven happy paths optimized after field run #4. It
does not cap fail-closed recovery when evidence is absent, stale, mismatched, or
when Instagram is still transitioning.

## Return CT after final Mute close

When Ordering V2 has a fresh exact post-Mute candidate verdict:

- UI Back dispatches: at most 1.
- Fresh full Followers detections before Back: 0.
- Fresh full Followers detections after Back: normally 1, bounded poll maximum 3.
- Duplicate compact detections over the same pre-Back evidence: 0.
- Foreground package reads before Back: at most 1.
- DB writes inside Return CT: 0.
- Screenshots: 0.
- The final exact CT header/Followers proof and visual ACK remain mandatory.

If the post-Mute verdict is absent, stale, generation-mismatched, or otherwise
invalid, the existing compact/Golden recovery budget remains authoritative.

## Followers ready to next candidate ready

For a stable Followers viewport:

- Foreground package reads in the loop guard: at most 1.
- Row snapshot parses: at most 1 per acquired hierarchy generation.
- Candidate tap dispatches: exactly 1 after fresh bounds and eligibility proof.
- Profile identity ACK: exactly 1 successful candidate identity contract.
- Critical durable Post-Follow persistence: exactly 1 idempotent barrier before
  the next UI action.

After a proven scroll, the hierarchy acquired for the existing injection refresh
must also supply the candidate-selection snapshot. A second live Followers
detection is forbidden while that snapshot is fresh (3.5 seconds), has the same
source CT and scroll index, and retains the exact own-unified Followers signals.
Missing or stale evidence always falls back to the existing live detector.

## Structural test coverage

- Return CT tests assert two full detections on compact fallback (one before and
  one after Back), and three only when post-Back evidence must be rejected and
  repolled.
- Ordering V2 tests assert fresh final-Mute evidence enables the one-Back V1 fast
  path and stale evidence keeps the safe fallback.
- Navigation tests assert one fresh package proof eliminates the duplicate
  `app_current`; expired proof forces a refresh.
- Post-scroll tests assert the fresh hierarchy snapshot is reusable even when the
  older open snapshot expired, while the 3.5-second expiry remains fail-closed.
