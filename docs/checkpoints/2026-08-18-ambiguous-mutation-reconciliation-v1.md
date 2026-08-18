# P0C — Ambiguous Mutation Reconciliation V1

Status: candidate only; production activation requires Liam approval 2.

## Scope and claim

This change adds durable, receipt-first reconciliation for Follow and Unfollow. It does not claim physical exactly-once delivery to Instagram. It guarantees that an uncertain outcome is never followed by a blind duplicate tap and that canonical persistence/counters are idempotent.

No account, username, device, Mac, package, or CT-specific branch is present. The `bmybusinesses → arnaud_blanchard74` case is a fixture, not production logic.

## Reused primitives

- Local atomic JSON intent ledger: extended from `FOLLOW_CANDIDATE_LOCAL_RECEIPT_V2` into the generic `AMBIGUOUS_MUTATION_INTENT_V1` schema.
- Deterministic action identity and canonical Follow persistence RPC: reused.
- Existing Follow canonical receipt lookup: reused.
- Existing Follow60 CT identity and strict profile verification: reused.
- Unfollow receives a matching idempotent RPC because legacy outcome persistence did not expose an action-bound canonical receipt.

## Durable intent contract

Identity binding includes action/idempotency key, action type, account, business session, original run/request/attempt, normalized candidate, source target and CT, SAST business date, worker/settings identity, and the Unfollow interaction row where applicable.

State transitions:

`prepared → physical_attempt_started → verified → persisted|ambiguous → reconciled|unresolved → terminal`

`prepared` is written only after exact candidate/surface guards pass. A second fsync transition to `physical_attempt_started` occurs immediately before the tap. A recovered `prepared` intent is abandoned without receipt lookup, UI read, persistence, or tap; it therefore cannot fabricate a physical attempt.

Terminal files are retained as forensic evidence. Cross-run recovery is allowed only inside the same business-session lineage. Legacy intents without a business-session identifier remain current-run only. Old sessions cannot block future sessions.

## Follow contract

Normal path: exact candidate and final tap bounds are proved, `prepared` is fsynced, `physical_attempt_started` is fsynced immediately before the existing safe tap, the existing verification runs, and the existing idempotent Follow RPC persists the canonical receipt.

Recovery runs before CT progress and before runtime quota/cap loading. It first reads the exact canonical receipt. A matching receipt terminalizes the local intent without touching the phone. Without a receipt it reopens the exact candidate and requires two concordant fresh `Following` observations before persistence. Any identity/state ambiguity stops new work; no blind retry is dispatched.

Original run/request/action/CT attribution is preserved across Auto Restart.

## Unfollow contract

Normal path: exact candidate, current `Following` state, action sheet, and safety guards are proved; `prepared` and then `physical_attempt_started` are fsynced before the existing tap. Verified success uses `persist_verified_unfollow_success_v1`, advisory-locked by action UUID, to update the exact interaction row and insert the canonical event once.

Recovery runs before the daily counter, cap, eligibility, and daily-plan calculations. It checks the exact receipt first. Without one it reopens the exact candidate and requires two concordant fresh not-following observations before canonical persistence. Unknown or conflicting evidence safely stops new work without retry.

This ordering makes an ACK-lost 119→120 success visible before the next quota decision. Idempotent replay returns counter delta zero.

## Interruption windows

- Before `prepared`: no intent and no permitted tap.
- After `prepared`, before attempt-start: abandoned without UI or persistence.
- After attempt-start, before tap/ACK: ambiguous; receipt-first recovery.
- After physical mutation, before verification: two fresh exact observations required.
- After verification, before RPC: recover and persist once from exact evidence.
- After RPC commit, before response/local terminalization: canonical receipt wins; no phone action.
- Auto Restart: same business session recovers original attribution before progress.

## Performance

Happy path adds two local fsync writes per supported mutation. It adds no XML dump, screenshot, Vision call, sleep, retry, profile reopen, or extra Supabase read. Follow keeps its existing canonical write. Unfollow replaces the legacy success write with one idempotent RPC rather than adding a second write.

Failure-only recovery is bounded to one receipt lookup, at most one exact profile reopen, and two fresh observations. The current integration dispatches zero physical retries even when the pure policy identifies a theoretically retryable pre-action state.

## Mute, Like, and DM audit

- Mute: duplicate risk is low because the current flow reads and verifies explicit Posts/Stories state before changing it. No new durable intent is justified in P0C.
- Like: a blind repeated double-tap could toggle or duplicate intent; the current Like flow verifies already-liked/liked state and fails closed. No durable intent is added in this narrowly approved scope.
- DM: exact attribution of one intended message to one physical attempt is not provable from the current UI/thread evidence (duplicate text, reordered/out-of-view bubbles, and delivery-state ambiguity). DM reconciliation is explicitly not implemented or claimed.

## Challenge review

1. Physical exactly-once claimed: no.
2. Intent before every supported Follow/Unfollow mutation: yes.
3. Wrong candidate can receive an intent: only if all existing exact-identity guards are themselves false; no fallback username branch exists.
4. Intent can exist without a tap: yes, in `prepared`.
5. That can falsely reconcile: no; it is abandoned without receipt/UI/persistence.
6. Fresh candidate identity re-proved: yes.
7. Stale Following can fabricate success: no; exact reopen plus two fresh concordant reads required.
8. Stale not-following can fabricate Unfollow success: same guard.
9. Receipt checked before phone: yes for attempt-started ambiguity.
10. ACK loss can cause duplicate physical action: no blind retry is dispatched.
11. Reconciliation can increment quota twice: no; action-bound RPC is advisory-locked and idempotent.
12. Old intents can poison future sessions: no; business-session scoping.
13. CT Resume progresses before reconciliation: no.
14. Auto Restart progresses before reconciliation: no within the same business session.
15. Follow→Unfollow recomputes after reconciliation: Follow caps and Unfollow counter/plan load afterward.
16. SAST business date: captured on the original intent and authoritative daily counters use the SAST window.
17. CT attribution: original target/CT binding retained.
18. Unfollow backlog state: exact interaction row is updated by the canonical RPC.
19. Recovery bounded: yes.
20. Happy-path overhead acceptable: two small local fsyncs; no UI/network observation overhead.
21. Existing primitives duplicated: no; generic ledger extends the existing one, Follow RPC reused.
22. DM excluded without proof: yes.
23. Account-specific logic: no.
24. Future Macs/devices/accounts: yes, subject to the same runtime directory durability and existing UI identity contracts.

## Rollback and activation

Rollback is the immutable production baseline `ef60be62e3578ffe1099d4cf48512e4c1a878e9d` / `/Users/admin/phonefarm-worker-releases/ef60be6-post-first-v2-mythyl-unfollow36-v1`.

No deployment, migration apply, runtime switch, restart, run, tick, or ADB action is authorized under approval 1.
