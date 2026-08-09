# Follow60 V2 mainline post-promotion regression matrix

No result below is inferred only from the Rex canary. PASS means a fixture,
integration test, static carrier assertion, or read-only production projection
was exercised on the consolidated lineage.

| Historical V1 regression | V2 Mainline test | Result | Evidence |
|---|---|---|---|
| Business-session binding | true mainline three-candidate session | PASS | `tests/test_follow60_v2_mainline_v1.py` |
| Followers false positive | unchanged CT, loading, slow and wrong surface rejected | PASS | followers transition/continuation suites |
| Scheduled natural start | scheduler-to-dispatch fixtures without canary control | PASS | scheduler and request-consumer suites |
| Live Follow count | monotone receipt/revision and replay | PASS | persistence replay and run-count suites |
| Partial Mute | either missing axis keeps cycle incomplete | PASS | partial-Mute contract suites |
| See More | primary rows first, one-shot expansion, bounded failure | PASS | `tests/test_instagram_list_continuation_contract.py` |
| Incident resume | resolved non-security incident creates distinct one-shot resume | PASS | Incident Resume V2 contract tests |
| Manual/scheduled consistency | assignment mode governs eligibility, engine is identical | PASS | assignment/scheduler resolver suites |
| Startup skip | only shared runtime token path accepted | PASS | V2 lock and promotion dry-run |
| Historical receipts | active binding identity excludes unrelated history | PASS | outbox/binding replay suites |
| True mainline carrier | no control, allowlist, lease or ten-cycle barrier | PASS | `test_follow60_v2_mainline_v1.py` |
| Follow to Unfollow | authoritative handoff, caps and protection lists | PASS | account-session handoff suites |
| Package caps | effective minimum remains authoritative | PASS | commercial-policy/package suites |
| Multi-account scheduler | manual, scheduled, blocked, exhausted and device cases | PASS | auto-restart/scheduler suites |
| Stop recovery | only acknowledged stages are persisted | PASS | stop/outbox/session suites |
| Future account inheritance | no per-account engine override or canary artifact | PASS | onboarding and mainline default assertions |
| Existing account inheritance | global mainline source independent of account flags | PASS | read-only account projection plus mainline tests |
| Golden residual | one micro-revalidation then Golden on persistent ambiguity | PASS | proof-closure and Golden suites |
| Performance | canonical alert baselines recorded, no duplicate stages added | PASS | `docs/follow60-v2-performance-baseline-v1.md` |

Account modes, schedules, incidents and quota still govern whether a specific
account may start. They do not change its selected Follow engine. A blocked
account is therefore not a canary dependency.
