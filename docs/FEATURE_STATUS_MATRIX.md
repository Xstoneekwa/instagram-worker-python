# Feature Status Matrix

Canonical product status source:
[JULY_16_PRODUCTION_BASELINE](./checkpoints/2026-07-16-production-baseline-cross-repo.md).
Canonical Follow performance source:
[FOLLOW_85S_PERFORMANCE_GOLDEN_V1](./golden-evidence/follow-85s/README.md).

| Feature | Status | Evidence / limitation |
|---|---|---|
| First-tick natural scheduler | Physically validated | July 16 natural launch |
| Scheduler without Electron | Test-validated | Closed-BotApp natural run pending |
| Same-account preflight lease | Test-validated | Natural contract tests |
| Multi-device bounded pool | Test-validated | Simultaneous physical runs pending |
| Welcome Suggestions boundary/recovery | Frozen / pending | Separate Welcome investigation; not covered by Follow 85s |
| Welcome outbound bubble | Frozen / pending | Separate physical proof required |
| Welcome-to-Follow handoff | Frozen / pending | Separate physical proof required |
| Golden Follow/Mute/Like/Return CT | Physically validated | 20/20 cycles, 85.226 s candidate-to-candidate on `ff99db6` |
| Follow persistence RPC | Available, OFF in Golden | Golden physical log used the legacy persistence path |
| Post-Mute fast no-post | Rejected / frozen | `d53a6b1` canary regressed; temporal evidence insufficient |
| Follow cap resolver | Test-validated | Mythyl 20-follow observation pending |
| Like evidence reuse | Test-validated | Physical latency gain pending |
| Warmup projection | Physically validated | Completed Day N visible |
| Unfollow J+3 and time guard | Physically validated | 11 Mythyl successes |
| Follow/Like live counters | Physically validated | July 16 BotApp observation |
| Incident actions / Mark reviewed | Physically validated | Human review and refresh |
| Slack/Discord shared CTA | Physically validated | Creation/resolution deliveries |
| Transactional settings | Test-validated | No checkpoint-time mutation |
