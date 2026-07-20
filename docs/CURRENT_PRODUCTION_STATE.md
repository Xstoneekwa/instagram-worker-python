# Current Production State

Current worker checkpoint: [Follow 85s Performance Golden V1](./golden-evidence/follow-85s/README.md).
The cross-repository product checkpoint remains
[JULY_16_PRODUCTION_BASELINE](./checkpoints/2026-07-16-production-baseline-cross-repo.md).

| Surface | Reference |
|---|---|
| Worker business SHA | `ff99db6d7de48d75ede439c704e770feaaec6b7c` |
| Worker active release | `ff99db6-follow-persistence-rpc-v1` |
| Backend business SHA | `6f0f3b028379e029f09f66086372bf39798fe193` |
| Backend deployment | `dpl_EQeojpstgH173NGAtjY1tWFAHLGq` (`READY`) |
| BotApp business SHA | `fa427ace11b294d3765f4a6e0dda2522770ed70e` |
| Official BotApp | `/Applications/BotApp.app` |

At checkpoint preparation the dispatcher was idle and healthy, business queue,
runs, locks and nonterminal preflights were empty, both canonical device
heartbeats were fresh and no business subprocess was active.

The Follow 85s baseline is physically validated at 85.226 seconds
candidate-to-candidate across 20 verified cycles. The Follow persistence RPC is
available but was OFF in this physical baseline; the logged persistence path was
legacy. The `d53a6b1` Post-Mute canary was rejected and the active runtime was
rolled back to `ff99db6`.

Post-Mute fast no-post work is frozen pending sufficient temporal evidence.
Welcome remains a separate frozen investigation and is not runtime-validated by
this Follow checkpoint.
