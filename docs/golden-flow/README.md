# Golden Flow Guard

Golden Flow 110s is a sanctuary reference for the validated Instagram gesture:

`candidate selected -> follow -> mute -> post open -> like verify -> return CT -> next candidate`

It remains read-only. Do not modify Golden Flow artifacts, do not cherry-pick or
merge them into the modern worker, and do not copy whole files from Golden Flow
into production.

The modern production worker remains the active base:

- release label: `b6fedca-preflight-keyguard`
- commit: `7224ccdafca39e4ab68bae3299e7004103f8071f`
- scope: Scheduler, scheduled preflight, keyguard, BotApp runtime, entitlements,
  package clone dispatch, and modern `account_session` orchestration.

## Protected Files

The protected file list lives in `docs/golden-flow/locked_files_manifest.json`.
Any change to those files requires explicit Liam GO before patching.

The guard is local and non-mutating:

```bash
scripts/check_golden_flow_untouched.sh
```

If a protected worker patch is explicitly approved, run the guard in allow mode
to document that the touch is intentional:

```bash
scripts/check_golden_flow_untouched.sh --allow-worker-golden-touch
```

## Required Before Release

Before any worker release that touches protected files:

1. State which protected invariant may be affected.
2. Get explicit Liam GO.
3. Keep the patch minimal and local.
4. Run the guard and the targeted tests listed in the manifest.
5. Confirm no Golden Flow sanctuary artifacts were modified.
6. Confirm the release is clean, reproducible, and not directly edited in place.

## 2026-07-25 Unfollow handoff addendum

The Unfollow handoff budget checkpoint does not alter the protected Golden
Follow/Mute/Like/Return-CT gesture. It changes only deadline transport, the
Unfollow phase budget/guard, the optional Outreach boundary decision, and
request terminal ordering. Its device-free regression suite is required before
release; physical validation remains deferred to a natural run or a separate
Liam GO.
