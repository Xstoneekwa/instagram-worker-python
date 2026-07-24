# Release Registry

## GOLDEN_PHONEFARM_PRODUCTION_2026_07_24 — official

Human name: **Phone Farm Production Golden — 24 July 2026**.

| Artifact | Reference |
|---|---|
| Worker business commit | `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f` |
| Worker business parent | `abf0ebf90bcccd063355b41505d4d6541e870047` |
| Navigation source patch | `f93c501c334b6c5b87ab04b72759bc60a452f5a8` |
| Immutable release | `/Users/admin/phonefarm-worker-releases/abf0ebf-f93c501-navigation-consolidated-v1` |
| Active pointer | `/Users/admin/phonefarm-worker-current` |
| Documentation branch | `docs/golden-phonefarm-production-20260724` |
| Annotated tag | `golden-phonefarm-production-2026-07-24` |
| Golden documents | [`docs/golden/2026-07-24-production-golden/`](./golden/2026-07-24-production-golden/README.md) |

The tag resolves the final docs-only commit. Its first parent is the Worker
business commit above. Restoring runtime means selecting the immutable business
release, never executing from the documentation checkout.

### Cross-repository live references

| Surface | Live evidence | Provenance boundary |
|---|---|---|
| Backend/frontend | Vercel production deployment `dpl_Ab6AKB5rXxvuGyuUiXe7f2tZc5K4`, status `READY`, canonical alias `https://www.boostmybusinesses.com` | `vercel inspect` did not expose a Git commit; no exact SHA claim is made |
| BotApp | `/Applications/BotApp.app`, bundle `com.boostmybusinesses.botapp`, version `0.1.0`, `app.asar` SHA-256 `8b956c792bfdc08a0663bc952a883fe77d4010a2dcb7de5c95ce5cd523c38253` | Ad-hoc signature (`TeamIdentifier` absent); package does not embed source provenance |
| Last consolidated source references | Backend code `108f7de`, docs `bd37a1e`; BotApp code `5f5b6a8`, docs `9bc2697` | Coordination references only; not asserted to equal the live artifacts |

## FOLLOW_WARMUP_ACTIVE_SAST_DAYS_V1 — 2026-07-23

Worker code checkpoint `51278eadf1613f80349a7930e17420c8d8dd1e64`
introduced active-SAST-day warmup. It is an ancestor policy of the Golden Worker.
See [the policy checkpoint](./checkpoints/2026-07-23-follow-warmup-active-sast-days-v1.md).

## JULY_16_PRODUCTION_BASELINE — historical physical reference

| Repository | Business reference | Immutable tag |
|---|---|---|
| Worker | `5a59ef3677c59b5e65e6b69462f3756fd96cda78` | `checkpoint-worker-july16-production-baseline` |
| Backend | `6f0f3b028379e029f09f66086372bf39798fe193` | `checkpoint-backend-july16-production-baseline` |
| BotApp | `fa427ace11b294d3765f4a6e0dda2522770ed70e` | `checkpoint-botapp-july16-production-baseline` |

Detailed provenance:
[cross-repository checkpoint](./checkpoints/2026-07-16-production-baseline-cross-repo.md).
