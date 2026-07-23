# Current Production State

## Prepared checkpoint — 2026-07-23

`FOLLOW_WARMUP_ACTIVE_SAST_DAYS_V1` is documented at
[this checkpoint](./checkpoints/2026-07-23-follow-warmup-active-sast-days-v1.md).
Worker code `51278eadf1613f80349a7930e17420c8d8dd1e64` is pushed. The
runtime/deployment state must be confirmed by the rollout evidence before this
prepared checkpoint is called active production.

Current checkpoint: [JULY_16_PRODUCTION_BASELINE](./checkpoints/2026-07-16-production-baseline-cross-repo.md).

| Surface | Reference |
|---|---|
| Worker business SHA | `5a59ef3677c59b5e65e6b69462f3756fd96cda78` |
| Worker active release | `5a59ef3-follow-caps-like-evidence` |
| Backend business SHA | `6f0f3b028379e029f09f66086372bf39798fe193` |
| Backend deployment | `dpl_EQeojpstgH173NGAtjY1tWFAHLGq` (`READY`) |
| BotApp business SHA | `fa427ace11b294d3765f4a6e0dda2522770ed70e` |
| Official BotApp | `/Applications/BotApp.app` |

At checkpoint preparation the dispatcher was idle and healthy, business queue,
runs, locks and nonterminal preflights were empty, both canonical device
heartbeats were fresh and no business subprocess was active.

The three open performance paths are Pre-Follow, Post-Mute to post open, and CT
stable to next candidate. Runtime validity and pending physical observations are
defined in the linked checkpoint, not inferred from test results.
