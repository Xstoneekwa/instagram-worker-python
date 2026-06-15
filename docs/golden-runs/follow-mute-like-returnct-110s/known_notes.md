# Known Notes And Deferred Work

## Stop Responsiveness / Graceful Shutdown

In the `fc65bace` golden validation run, `villan.brorslund` was opened after manual stop/graceful shutdown began. No follow, mute, like, or Return CT action was taken on that candidate. This is not a Return CT regression, but it is a future stop-responsiveness topic.

## Facebook / No-Like

The rollback reference run `272f6925` included a Facebook shared / no-like button case. It was handled with safe-continue and did not stop the run. Do not optimize this until after the golden flow remains protected.

## No Posts Yet

No Posts Yet handling remains deferred until after golden checkpoint stabilization.

## Device Pre-Run Cleanup

Some preflights found the device on launcher/system surfaces before Instagram was started. Future work may add stronger foreground cleanup/pre-run readiness guards, but must not change the golden flow without explicit GO.

## Tests Not Added In This Checkpoint

This checkpoint adds documentation and a guard script rather than new runtime behavior. Existing tests already cover the Return CT strict proof cases and post-follow like/mute runtime guard paths. If future work touches protected areas, add focused tests in that patch.
