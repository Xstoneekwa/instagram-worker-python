# Known Notes And Deferred Work

## Stop Responsiveness / Graceful Shutdown

In the `fc65bace` golden validation run, `villan.brorslund` was opened after manual stop/graceful shutdown began. No follow, mute, like, or Return CT action was taken on that candidate. This is not a Return CT regression, but it is a future stop-responsiveness topic.

## Facebook / No-Like

The rollback reference run `272f6925` included a Facebook shared / no-like button case. It was handled with safe-continue and did not stop the run. Do not optimize this until after the golden flow remains protected.

### Post-golden approved patch `6922d90` (2026-06-15)

- **Scope**: Facebook/no-like explicit non-likable safe-continue (read-only mini-probe; `still_profile_grid` alone never triggers fast safe-continue).
- **Authorization**: explicit user GO after Golden checkpoint; Golden tag **not** moved.
- **Non-regression run**: `a3265248-2545-4b2c-b2fe-005dd66ab4d6` (`lorielebras_autom` / `RFGL145LZHE`) — 3 complete candidates, 3 follows/mutes/likes/Return CT OK, productive avg `101.66s/candidate`, Return CT avg `15.35s`.
- **Limitation**: no real Facebook/no-like surface encountered on that run; fast path acceleration vs `hansulrich17` (~82.45s post-like) still to validate when a natural case reappears.
- **Golden tag remains**: `golden-follow-mute-like-returnct-110s-20260615` → `86a01a6`.

## No Posts Yet

No Posts Yet handling remains deferred until after golden checkpoint stabilization.

## Device Pre-Run Cleanup

Some preflights found the device on launcher/system surfaces before Instagram was started. Future work may add stronger foreground cleanup/pre-run readiness guards, but must not change the golden flow without explicit GO.

## Tests Not Added In This Checkpoint

This checkpoint adds documentation and a guard script rather than new runtime behavior. Existing tests already cover the Return CT strict proof cases and post-follow like/mute runtime guard paths. If future work touches protected areas, add focused tests in that patch.
