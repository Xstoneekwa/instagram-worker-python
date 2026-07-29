# Target Availability runtime integration — dormant

V2-1 adds two least-risk, lazy-loaded hooks to `account_session_orchestrator.py`. With capture OFF, the Availability runtime module is not imported:

- target loaded: emits `target_username_lookup_started` from the already selected CT;
- target summary: maps only explicit facts already returned by the Followers engine.

The hooks never call Instagram, ADB, a device helper or recovery. Missing profile, identity, badge, Followers, pagination, network or session facts remain unknown. Ambiguity never becomes terminal proof. Each hook catches its own failure and returns control so the rotation can skip/continue to the next target.

The existing `runtime_error_non_exhaustion` classifier remains unchanged and correctly excludes runtime/navigation failures from CT exhaustion. No Golden Flow gesture was changed.

## Instrumentation map

| Existing path point | Raw observation | Added device action | Cost/risk | Golden Flow |
|---|---|---:|---|---:|
| CT load | scope and existing stable ID when present | none | constant / low | before navigation |
| Username search | lookup started | none | constant / low | hook immediately before existing engine call |
| Profile resolution | found, missing or ambiguous when returned | none | constant / low | summary only |
| Identity validation | observed username, stable ID, mismatch/conflict | none | constant / low | summary only |
| Profile open | existing profile-found fact | none | constant / low | summary only |
| Verified badge | existing badge fact | none | constant / low | summary only |
| Followers entry | entered/failed and surface kind | none | constant / low | summary only |
| First page | accessible count when returned | none | constant / low | summary only |
| Pagination | terminal, stalled, repeated-first signals | none | constant / low | summary only |
| End of list | terminal end fact | none | constant / low | summary only |
| Repetition | repeated-first fact | none | constant / low | summary only |
| Return to CT | existing recovery outcome | none | constant / low | summary only |
| Rotation next | current target summary completes before historical next-target logic | none | constant / low | fail-open |
| Recovery | attempted/succeeded/failed when already returned | none | constant / low | no new recovery |
| Terminal exception | timeout/retry-exhausted/ambiguity when already returned | none | constant / low | no exception propagation |

Unsupported facts stay `unknown`; the adapter never fabricates a lookup result or identity proof.
