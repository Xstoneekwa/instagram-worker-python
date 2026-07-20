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

## FOLLOW_85S_PERFORMANCE_GOLDEN_V1

| Item | Reference |
|---|---|
| Worker runtime | `ff99db6d7de48d75ede439c704e770feaaec6b7c` |
| Active release | `ff99db6-follow-persistence-rpc-v1` |
| Annotated tag | `golden-follow-85s-v1` |
| Canonical request | `46a45bf5-3c90-4ee1-863a-6980d07aec74` |
| Canonical run | `9597985e-d357-4a44-a3d5-a6f0457ca05e` |
| Result | 20/20 verified, 85.226 s candidate-to-candidate |

The before-optimization run `245613bb-8d26-4dca-ba05-7d8202496071` is retained
as a slower reference. The `d53a6b1` run
`903ea73c-c853-4853-9df0-b8514fc0e9bb` is a rejected Post-Mute canary, not a
release baseline. Full evidence: [Follow 85s V1](./golden-evidence/follow-85s/README.md).
