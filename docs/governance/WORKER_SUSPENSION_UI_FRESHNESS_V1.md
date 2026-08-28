# Worker suspension freshness — V2, Approval 1 only

Base: `8750273c62402f2e4d5fae645469de2f111cacf6`.
Fresh signed Approval 1: `worker-consolidated-suspension-lockstate-20260828-lfuPOE`.
No final commit, manifest/signature or production activation in this pass.

## Detector rule and limits

At existing call/proof boundaries compare local macOS mach_continuous_time
(converted with mach timebase), Python monotonic and wall-clock deltas.
Invalidate continuity on negative deltas, continuous-vs-monotonic discrepancy
over the existing 250 ms tolerance, or wall-vs-continuous discrepancy over that
tolerance. Sampling failure also fails closed. Clock adjustment is reported as
clock discontinuity, not falsely asserted to be macOS sleep.

The old elapsed > 20 s global gate is removed. A legitimate 30 s bounded
operation with all clocks advancing by 30 s remains admitted. Simulated wall
and continuous +230 s, monotonic +6 s increments the suspension epoch.
Existing Like/UI TTLs are unchanged; an aged proof can still fail its own TTL.

Heartbeat age and elapsed time alone are not suspension proof: blocking I/O and
network loss can cause both. No heartbeat polling, watcher, wake observer,
signal-handler replacement or device call was added. Arbitrary process-only
pauses with all clocks advancing coherently are not distinguishable from a
blocking call using these samples; normal monotonic proof TTLs still apply.
Linux uses CLOCK_BOOTTIME; fallback uses wall/monotonic conservatively. These
portable adapters do not constitute field certification on other platforms.

## Epoch and admission are distinct

1. Discontinuity increments the epoch and records a monotonic invalidation floor.
2. Proof consumption and critical gestures raise UiContinuityLost (BaseException)
   outside broad candidate retry handlers. No interrupted action is replayed.
3. Fresh read-only app_current, hierarchy/screenshot and the positive JSON RPC
   read allowlist remain available. Unknown RPC and shell operations stay blocked.
   A read crossing another epoch is rejected, never returned as fresh.
4. Only canonical Identity Guard re-entry performs a bounded read-only bootstrap:
   fresh foreground package/activity → fresh own-profile XML → exact expected
   username plus selected own-profile tab and own-profile edit controls → fresh
   foreground consistency check. Known challenge/restriction/consent or ambiguous
   context fails closed. Candidate/CT identity alone cannot prove the account.
5. No gesture is allowed during this bootstrap. Failed/expired verification or a
   second interruption keeps admission blocked. The bootstrap has a 20 s maximum
   coherent observation span, not a global inactivity/suspension threshold.
6. On success, old UI caches are invalidated and admission reopens for newly
   proved work. All downstream target, exact-control, deadline and P0C guards
   remain required. Old PostOpen/Followers proofs carry old epochs; legacy UI
   proof consumers reject timestamps at/before the invalidation floor even after
   revalidation. Re-timestamping an old transported Like snapshot is rejected.

This is not automatic mid-candidate replay: interruption outside an admissible
Identity Guard entry follows the existing outer safe-stop, without cleanup
gestures. If the own profile cannot be proved without navigation, stop rather
than navigating blindly to recover. A future authorized fresh attempt still
requires the ordinary Identity Guard. No account-specific exception exists.

## Ownership and unchanged contracts

Only ephemeral UI proof authority is invalidated. Account/request/run/business
session/action IDs, P0C durable intent/receipt truth, quotas, plans, cursor and
attempt lineage are untouched. No BotApp/Backend edits, Auto Login, production
run/tick/restart or ADB action. A1 and Patch 3.1 retain their existing semantics.
The separately implemented external Instagram version-lock store is unchanged.

## Offline qualification

RequiredContinuityChallengeTests preserves the two original release-blocking
assertions: long awake reads are not suspension, and fresh reads are reachable
after suspension. Additional real-call-graph fixtures exercise canonical Identity
Guard re-entry, zero gestures during verification, old/new PostOpen intents,
wrong account/clone/activity, candidate surface, restriction, repeated epochs and
interruption during capture. No actual device is involved.

The consolidated matrix, external lock-state tests and Patch 3.1 verification
receipts are reported separately in SUSPENSION_V2_REPORT.md in the Approval 1
evidence directory. Deterministic readiness is not field certification.

100,000 local macOS detector checks: mean 0.002489 ms, P50 0.002292 ms,
P95 0.002500 ms, P99 0.004292 ms. This measures guard CPU cost only, not entire
candidate latency. Normal-path added device/XML/Vision/sleep calls: zero.
Bootstrap-only fresh observations may add latency after a real discontinuity.

FINAL_COMMIT_CREATED = NO
FINAL_SIGNATURE_CREATED = NO
PRODUCTION_CHANGED = NO
