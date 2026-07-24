# Current Production State

## Official Golden checkpoint — 2026-07-24

The official production reference is
[`GOLDEN_PHONEFARM_PRODUCTION_2026_07_24`](./golden/2026-07-24-production-golden/README.md).

| Surface | Locked reference | Evidence status |
|---|---|---|
| Worker business code | `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f` | Exact Git commit and immutable release |
| Worker active release | `abf0ebf-f93c501-navigation-consolidated-v1` | Active symlink and runtime root verified |
| Worker Golden tag | `golden-phonefarm-production-2026-07-24` | Resolves the documentation commit whose parent is the business commit |
| Dispatcher | `run-dispatcher:mac-admin-01` | Healthy, unique, launch-enabled, queue empty |
| Auto Login | isolated historical 07ee engine plus bounded late-popup stabilization | Deployed; clone routing physically validated |
| Follow navigation | `f93c501` behavior consolidated on the 07ee baseline | Deployed and offline-certified; physical navigation replay pending |
| Backend/frontend production | Vercel `dpl_Ab6AKB5rXxvuGyuUiXe7f2tZc5K4` | `READY`; Git source metadata unavailable from the live deployment inspection |
| Installed BotApp | `/Applications/BotApp.app`, `app.asar` SHA-256 `8b956c792bfdc08a0663bc952a883fe77d4010a2dcb7de5c95ce5cd523c38253` | Installed package inspected; exact source commit not embedded |

Read-only control-plane snapshot at `2026-07-24T15:26:39Z`:

- total requests: `176`; active requests: `0`;
- total runs: `144`; active runs: `0`;
- active device locks: `0`;
- runtime root: correct;
- dispatcher process count: `1`;
- heartbeat publisher process count: `1`.

The heartbeat service was operational at checkpoint capture. Two registered
phones were observed: one online and one ADB-unauthorized. This device condition
is an operational limitation, not a Worker release mismatch.

## Historical checkpoints

- [`FOLLOW_WARMUP_ACTIVE_SAST_DAYS_V1`](./checkpoints/2026-07-23-follow-warmup-active-sast-days-v1.md)
  remains the canonical warmup policy carried by the Golden Worker.
- [`JULY_16_PRODUCTION_BASELINE`](./checkpoints/2026-07-16-production-baseline-cross-repo.md)
  remains the historical physical comparison point for Golden Follow, Welcome,
  Unfollow, incidents and counters.

Never infer current production from an older checkpoint without checking the
Golden release, tag and active symlink first.
