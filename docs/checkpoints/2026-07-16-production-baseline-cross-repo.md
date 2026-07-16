# 2026-07-16 Production Baseline - Cross-Repository Summary

Official name: **2026-07-16 Production Baseline - Warmup, Unfollow, Welcome,
Multi-device, Live Counters, Follow Caps, Like Evidence Reuse**

Short name: `JULY_16_PRODUCTION_BASELINE`

This checkpoint is the functional rollback point and comparison baseline for
runs on July 17, 2026 and later.

| Repository | Business SHA | Documentation SHA | Immutable tag |
|---|---|---|---|
| Worker | `5a59ef3677c59b5e65e6b69462f3756fd96cda78` | resolved by the Worker tag below | `checkpoint-worker-july16-production-baseline` |
| Backend | `6f0f3b028379e029f09f66086372bf39798fe193` | `6a34423442a7cdd4110aadcf6340560e3c7e5027` | `checkpoint-backend-july16-production-baseline` |
| BotApp | `fa427ace11b294d3765f4a6e0dda2522770ed70e` | `97209d766c10e0698b46cf03252bb41f0d0a5547` | `checkpoint-botapp-july16-production-baseline` |

The Worker documentation SHA is intentionally resolved through its annotated
tag because a commit cannot embed its own final object ID. The tag target is the
canonical final documentation SHA; all three exact SHAs are also recorded in the
release report and tag annotations.

Repository documents:

- [Worker runtime checkpoint](./2026-07-16-production-baseline-runtime.md)
- Backend: `docs/checkpoints/2026-07-16-production-baseline-backend.md`
- BotApp: `docs/checkpoints/2026-07-16-production-baseline-botapp.md`

## Production references

- Worker: immutable release `5a59ef3-follow-caps-like-evidence`, selected by the
  canonical active symlink.
- Backend: Vercel production deployment `dpl_EQeojpstgH173NGAtjY1tWFAHLGq`,
  commit `6f0f3b0`, state `READY`, canonical production aliases attached.
- BotApp: `/Applications/BotApp.app`; packaged and installed `app.asar` SHA-256
  `6731836948accc1a1ae65838ad0795b41f7f9ba08f6bece6a876d9d5489fe77e`.

## PHYSICALLY VALIDATED IN PRODUCTION

- First-eligible-tick natural scheduling.
- Golden Follow/Mute/Like/Return CT behavior.
- July 16 Mythyl result: 10 follows, 10 likes and 11 successful unfollows.
- Follow live, Like live, warmup completion, effective cap source and BotApp
  active/idle projection.
- Human `Mark reviewed` and Slack/Discord CTA delivery.

## TEST-VALIDATED ONLY

- Electron-independent scheduler gate, same-account preflight lease handoff and
  bounded multi-device dispatch.
- Latest Welcome boundary recovery and safe exhaustion behavior.
- New Follow cap priority and Like evidence reuse.
- Generic incident/action state and notification idempotence.

## PENDING PHYSICAL OBSERVATION

- Tracker complete Welcome recovery, outbound bubble and handoff.
- Two simultaneous physical-device business runs.
- Natural launch with BotApp closed.
- Mythyl 20-follow run under the current resolver.
- Physical Like evidence-reuse gain.

## KNOWN RISKS / LIMITS

- Stale historical runner heartbeats and unknown heartbeat `git_sha` values.
- Vercel CLI deployment provenance must be checked against project and commit.
- BotApp ad-hoc signature plus ScreenCaptureKit/bundle-identifier ambiguity.
- Historical dirty/noncanonical clone worktrees outside active releases.

## OPEN PERFORMANCE WORK AFTER JULY_16_PRODUCTION_BASELINE

1. Pre-Follow.
2. Post-Mute to post open.
3. CT stable to next candidate.

The detailed metrics, Golden constraints, hypotheses and non-regression criteria
are in the [runtime checkpoint](./2026-07-16-production-baseline-runtime.md).

`Any commit after this checkpoint touching these paths must be compared against JULY_16_PRODUCTION_BASELINE.`
