# Golden Flow 110s: Follow → Mute → Like → Return CT

This folder locks the validated Instagram worker flow at commit `fb3dbc0` on branch `rollback-follow-flow-9x3x3`.

## What Is Validated

Flow locked:

`candidate selected → follow → mute → post open → like verify → return CT → next candidate`

Validated behaviors:

- fast pre-follow proof reuse remains active;
- exact Follow detection is restored and taps only exact `profile_header_follow_button` with bounds;
- mute engine v2 sheet/dismiss optimization remains active;
- post-like rollback behavior is restored, with legacy-safe open back at the validated speed;
- Return CT uses strict proof reuse after compact back;
- no dangerous `dff205f` behavior is present;
- `stale_candidate_action_bar_ignored_with_strong_list` remains forbidden.

## Why This Is Golden

The latest real run on `fb3dbc0` validated the complete path over 3 complete candidates with no security regression:

- request: `b93cb2d4-86d6-4411-b7bf-3fd23d1db745`
- run: `fc65bace-78f9-4d20-bd4d-c6ec29da583e`
- account/device: `lorielebras_autom` / `RFGL145LZHE`
- CT/source: `betongdesign`
- 3 follows, 3 mutes, 3 likes verified, 3 Return CT OK
- no multi-like
- Return CT avg: `14.70s`
- normal selected → next cycles: `103.80s`, `101.93s`
- productive strict DB average: `116.89s/candidate`

This matches the historical 110s target closely enough to use as a protected checkpoint while optimizing other flows.

## Target Metrics

- normal complete candidate: about `102-110s`
- historical baseline: `992s / 9 = 110.22s/candidate`
- Return CT: about `14-16s`
- post-open normal: about `7-9s`
- no `4f754ea`-style post-open regression back toward `12s`
- no `dff205f`-style CT proof weakening

## Protected Rule

Before changing any protected area listed in `locked_files_manifest.json`:

1. STOP.
2. Explain why the golden flow must be touched.
3. Get explicit GO.
4. Keep the patch minimal.
5. Run targeted tests.
6. Compare against the golden runs in this folder.

Use `scripts/check_golden_flow_untouched.sh` before and after future patches.

## Archived Logs

The `logs/` directory stores sanitized critical-event extracts as `.jsonl.gz`, not raw full logs. They preserve the events needed to recalculate the golden metrics while avoiding raw dumps and unnecessary identifiers. See `log_manifest.json` for source paths, checksums, redactions, and run IDs.
