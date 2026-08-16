# Wrong-depth Followers recovery

## Symptom

The generic candidate-profile open detector reports a transition, but exact
identity proves that Instagram is still on the source CT, already on Followers,
or on another surface. A legacy recovery may then Back past the recoverable
Followers list and terminate with `followers_surface_reacquisition_unproved`.

## Evidence to collect

- `candidate_profile_open_not_confirmed` with expected candidate and source CT.
- `followers_wrong_depth_recovery_surface_observed` for every fresh observation.
- navigation/scroll generations and invalidation reasons.
- `followers_wrong_depth_recovery_navigation` actions and their count.
- terminal `followers_recovery_surface_unproved` when no surface is provable.

Never infer the current surface from the attempted action or reuse stale XML,
bounds, coordinates or a previous `FollowersSurfaceProof`.

## Safe behavior

- Followers already proved: continue with no Back.
- Source CT proved: reopen Followers canonically with no Back.
- Candidate or post proved: at most one Back, then mandatory reclassification.
- Explore/Search proved: recover the exact source CT and reopen Followers.
- Unknown: bounded read-only observations, then fail closed.

Strict candidate identity remains mandatory before Follow. Recovery sends no
Follow, Mute or Like and creates no business receipt.

The historical `return_to_followers_list` API may still return the compatibility
label `back` when Followers is already freshly proved. The typed recovery detail
is authoritative: in that branch no physical Back is dispatched.

## Forbidden behavior

- blind Back chains;
- exploratory taps or swipes;
- restoring stale Followers evidence;
- marking the target completed because recovery failed;
- hiding the first causal reason behind a session-envelope reason.

## Expected terminal reasons

- `candidate_profile_open_not_confirmed`: exact candidate identity was not
  established after an apparent open.
- `followers_recovery_surface_unproved`: bounded classification could not prove
  a safe recovery surface.
- `followers_surface_reacquisition_unproved`: public compatibility reason for
  callers of the historical return helper; detailed recovery evidence remains
  logged.
