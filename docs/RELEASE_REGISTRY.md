# Release Registry

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
| Backend | `9a472903d4d6d3202e1489347a42b06df425f244` | `c8383001362a3b5400a4d6c41d0047e140b4f350` |
| BotApp | `4606fc29b3717611219a62cc7132e2245647422a` | `261afcf5858d17fac2e3ddded2d6d31c2cf4a8d0` |

Branch: `feature/follow-warmup-active-days-v1-20260723` in each repository.
Activation requires documentation, additive migration verification, backend
deployment, immutable Worker release and official BotApp package validation.
