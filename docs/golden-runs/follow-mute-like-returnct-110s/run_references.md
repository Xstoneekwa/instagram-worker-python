# Run References

## Golden Run: Return CT Patch Validation

- commit: `fb3dbc0 fix(worker): reuse fresh return ct proof after compact back`
- request_id: `b93cb2d4-86d6-4411-b7bf-3fd23d1db745`
- run_id: `fc65bace-78f9-4d20-bd4d-c6ec29da583e`
- account/device: `lorielebras_autom` / `RFGL145LZHE`
- CT/source: `betongdesign`
- result: 3 complete candidates, 3 follows, 3 mutes, 3 likes verified, 3 Return CT OK
- key metric: Return CT avg `14.70s`

## Rollback Post-Like Reference

- commit: `b935653 revert(worker): remove post grid proof reuse`
- request_id: `327857aa-74da-4128-bb75-5baadf0402c0`
- run_id: `272f6925-acc0-4228-b5e9-da95eb7adafd`
- account/device: `lorielebras_autom` / `RFGL145LZHE`
- CT/source: `messodie_creations`
- key metric: useful complete candidate about `110.01s`
- note: Facebook/no-like safe-continue appeared and did not block the run

## Mute / Stable Flow Reference

- commit: `7fbe001 fix(worker): reuse mute dismiss sheet proofs`
- run_id: `bebf74a7-b66f-44d1-a040-0a83806db967`
- result: 3 follows / 3 mutes / 3 likes / 3 Return CT OK
- note: mute dismiss optimization validated; no multi-like

## Historical Baseline

- run_id: `5ad58174-95a0-45f2-91d7-33fcfdc3b5a0`
- code executed: `f22c83c`
- checkpoint: `37c0cf1`
- metric: `992s / 9 = 110.22s/candidate`
- local logs unavailable; metric retained as historical baseline

## NO-GO Reference To Avoid

- commit: `4f754ea fix(worker): reuse post grid proof before legacy open`
- verdict: NO-GO performance
- reason: proof reuse accepted 0 times, fallback still ran, reveal → open success regressed toward `12s`
