# Restriction preflight terminal contract

A successful physical restriction verification is an account-session control operation with zero business actions. It must not fabricate completed Follow/Unfollow phases or satisfy the ordinary business-phase terminal gate by an unconditional exemption.

Runner emits a dedicated V1 proof bound to the exact account, request, run, incident, consumed authorization, source plan and cleared hold. Cleanup records its actual outcome before terminal persistence. Dispatcher rereads the canonical request, complete run counters and the exact cleared hold. Only persisted completed status, zero business counts, the matching physical-clear run and successful cleanup/lease release admit success. The generic active-hold reader intentionally excludes cleared holds and cannot serve as terminal proof.

The exact current plan is compare-and-set to completed, with a dedicated restriction-preflight reason, no quota/phase/checkpoint rewrite and a confirming canonical reread. A missing plan, malformed or cross-run proof, real failure, uncleared restriction, nonzero actions or failed cleanup remains fail-closed. Normal business sessions retain the phase-terminal contract. Natural Backend eligibility remains authoritative; this path never creates a business request.

Regression qualification includes actual Runner cleanup, actual dispatcher finalization after Runner has already completed its queue row, real PostgREST/PG17 hold and plan persistence, and the real Backend eligibility check. Previous tests stopped before dispatcher finalization, which allowed the contradiction to escape. Existing human-resume, restriction/growth-ready, ordinary lineage, operator-stop and canonical resume suites must remain green.

Historical false incidents require a separate bounded canonical repair supported by exact persisted physical proof. The runtime contract does not accept legacy reason strings alone and never erases independent incidents.
