# Feature Status Matrix

Canonical source:
[`GOLDEN_PHONEFARM_PRODUCTION_2026_07_24`](./golden/2026-07-24-production-golden/FEATURE_STATUS_MATRIX.md).

| Function | Implemented | Offline tested | Deployed | Physically validated | Locked |
|---|---:|---:|---:|---:|---:|
| Historical Auto Login 07ee routing | Yes | Yes | Yes | Yes | Yes |
| Clone/app-instance/package binding | Yes | Yes | Yes | Yes | Yes |
| Late post-login popup stabilization | Yes | Yes | Yes | Partial: popup observed; post-patch replay not performed | Yes |
| Navigation f93c501 soft scroll/continuity | Yes | Yes | Yes | No | Yes |
| `See more` and Suggestions boundary | Yes | Yes | Yes | No for this release | Yes |
| CT rotation after true exhaustion | Yes | Yes | Yes | Historical flow yes; f93 replay pending | Yes |
| Shared Follow/Welcome/Unfollow contract | Yes | Yes | Yes | Partial | Yes |
| Golden Follow/Mute/Like/Return CT | Yes | Yes | Yes | Yes | Yes |
| Welcome outbound proof/handoff | Yes | Yes | Yes | Pending current-release completion | No |
| Unfollow J+3/time guards | Yes | Yes | Yes | Yes | Yes |
| Outreach session path | Yes | Yes | Yes | Not re-certified here | No |
| Active-SAST-day warmup | Yes | Yes | Yes | Prior production projection observed | Yes |
| Dispatcher/preflight/locks | Yes | Yes | Yes | Operationally observed | Yes |
| Embedded scheduler without BotApp authority | Yes | Yes | Yes | Natural launch observed historically | Yes |
| Incidents and operator review | Yes | Yes | Yes | Yes | Yes |
| Slack/Discord notification delivery | Yes | Yes | Yes | Historical production delivery | Yes |
| Backend/frontend production deployment | Yes | Separately tested | Yes | Production UI observed | External |
| Installed BotApp package | Yes | Separately tested | Yes | Production UI observed | External provenance gap |

“Partial” and “pending” are deliberate due-diligence states. They must not be
upgraded by inference from unit tests.
