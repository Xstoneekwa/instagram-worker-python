# Release Registry

## T10_PREFLIGHT_AND_FOLLOW_SOURCE_DEFAULTS_2026-07-24

Worker baseline is `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f`; the final immutable release
and backend deployment SHA are recorded at activation. See the [addendum](./checkpoints/2026-07-24-t10-preflight-follow-source-defaults.md).

## JULY_16_PRODUCTION_BASELINE

| Repository | Business reference | Final documentation reference |
|---|---|---|
| Worker | `5a59ef3677c59b5e65e6b69462f3756fd96cda78` | `checkpoint-worker-july16-production-baseline` |
| Backend | `6f0f3b028379e029f09f66086372bf39798fe193` | `checkpoint-backend-july16-production-baseline` |
| BotApp | `fa427ace11b294d3765f4a6e0dda2522770ed70e` | `checkpoint-botapp-july16-production-baseline` |

Human name: **2026-07-16 Production Baseline - Warmup, Unfollow, Welcome,
Multi-device, Live Counters, Follow Caps, Like Evidence Reuse**.

Purpose: immutable rollback baseline before performance optimization of
Pre-Follow, Post-Mute to post open and CT stable to next candidate.

Detailed provenance: [cross-repository checkpoint](./checkpoints/2026-07-16-production-baseline-cross-repo.md).

## FOLLOW_WARMUP_ACTIVE_SAST_DAYS_V1 — prepared 2026-07-23

| Repository | Code reference | Documentation reference |
|---|---|---|
| Worker | `51278eadf1613f80349a7930e17420c8d8dd1e64` | `8a5e9726e3dc3bdedaad6a8090ebc3a2ce83d88d` |
| Backend | `108f7defc17c2bce328801f4c272f82fb1f62706` | `bd37a1eea82cc44c0cf50f02c4f6fdedde80c579` |
| BotApp | `5f5b6a8ba97ce6af73482fc05767a3c4efb81f50` | `9bc269740e7d036bee852351f3ccedfe3618e7eb` |

Worker branch: `feature/follow-warmup-active-days-v1-20260723`. Backend and
BotApp final consolidated heads: `feature/targets-ui-parity-v1-20260723`;
these preserve the validated Warmup commits and add the Targets UI parity
checkpoint.
Activation requires documentation, additive migration verification, backend
deployment, immutable Worker release and official BotApp package validation.
