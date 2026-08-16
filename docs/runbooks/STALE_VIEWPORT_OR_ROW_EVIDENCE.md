# Stale viewport or row evidence

## Contract

Followers candidate selection is an identity intent, never an authorization to tap cached
coordinates. Every physical action must use a single-use `RowActionToken` issued from an
exact username row in a fresh `FollowersViewportProof` for the current device, app instance,
account, CT, navigation generation and scroll generation.

Any navigation, swipe, corrective backstep, See More expansion, recovery or list
reacquisition invalidates the current hierarchy, viewport, row proofs and unused action
tokens atomically. Superseded evidence may be retained only for comparison/telemetry; it is
never actionable.

## Safe flow

1. Keep only the expected candidate username as intent.
2. Immediately before a profile-open tap, use the current-generation hierarchy only when it
   is at most 750 ms old; otherwise acquire one fresh hierarchy on Instagram MainActivity.
3. Rebuild the viewport and exact row proof from that hierarchy.
4. Require exactly one matching username row and valid bounds.
5. Issue and consume one action token.
6. Invalidate the followers surface generation before dispatching the tap.
7. Confirm the opened profile with the existing exact identity boundary.

If any step fails, send no tap and reacquire the Followers surface. Visual geometry and old
snapshot coordinates are comparison-only.

## CT Resume rule

Once any physical fast-forward gesture is dispatched, pre-gesture candidates are discarded
even when continuity is unproved or a corrective backstep fails. The next action is a fresh
viewport acquisition; legacy evaluation may not consume the old candidate list.

## Incident response

Useful events:

- `follower_profile_open_row_action_token_committed`
- `follower_profile_open_blocked_stale_or_missing_row_proof`
- `visual_candidate_open_blocked_without_exact_live_row_proof`
- `ct_resume_candidate_geometry_invalidated_after_physical_gesture`

The invariant is: `expected_username + current generation + fresh exact row + single-use
token`, otherwise `physical_tap_attempted=false`.
