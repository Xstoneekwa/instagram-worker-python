# Generic restriction recovery at the natural tick

An operator resolution authorizes a fresh restriction/identity verification; it does not prove that Instagram is clear. The account remains administratively paused while its canonical restriction hold is `verification_required`. Ordinary business sessions remain inadmissible in that state.

Previously the Backend generated the correct zero-action preflight, but both atomic admission overloads rejected it as `account_not_active`. That consumed the one-shot human authorization before any Worker or device action and left later ticks unable to recover.

The forward migration aligns both admission overloads with the existing Backend and Worker contract. The exception requires the exact canonical incident, consumed authorization, frozen phase plan, source request/run, assignment, device and app instance. All three phases must be boolean false and all five quotas numeric zero. Missing, malformed, cross-account, expired or inconsistent evidence fails closed. The account's prior lifecycle must have been active, its runtime settings must permit scheduling, and no independent hold or canonical blocker may remain. Existing lease, fence, package, schedule and execution-attempt guards still run. No account name, production identifier or date selects a behavior.

The unchanged Worker runs fresh Identity Guard and restriction detection, then calls the existing hold-release RPC only on success, without business actions in that preflight. A failed physical check leaves the hold in place and requires further operator intervention. A successful check restores the previous lifecycle and allows the normal scheduler eligibility checks to pass, subject to the remaining schedule/runtime gates.

## Recovery of a consumed pre-admission authorization

The reconciliation already called by Auto Restart now tries to restore one verification enqueue credit when the exact consumed request is terminal `blocked/account_not_active`, has no run, startup timestamp, capsule, later run or later request, and retains a valid canonical verification-only authorization. Cancellation, independent pauses, active device leases, expired windows and changed incident/hold/identity evidence prevent restoration.

The account/assignment/device/request/authorization/plan lock order matches atomic enqueue. Concurrent calls serialize on the account; a durable `restriction_admission_credit_v1` record makes restoration at most once per authorization. It records the original request and frozen plan, increments the existing retry generation, and retains the original authorization expiry and window. The terminal historical request and its lineage are never rewritten. Enqueue remains the responsibility of the next natural tick and the existing atomic consume RPC; there is no second scheduler, forced run or automatic physical retry loop.

The migration executes no recovery or enqueue on installation. Its three predecessor-function hashes reject a changed parallel implementation rather than overwrite it; an exact replay remains valid. New internal routines are executable only by the service role; existing admission/reconciliation privileges remain unchanged.

## Qualification

`scripts/test-restriction-resume-natural-tick-v1.py` creates a disposable PostgreSQL 17 cluster on a private Unix socket and a loopback-only PostgREST. It replays the pinned Backend schema plus required Worker migrations, including the production admission fence. No production environment or key is inherited.

Tests exercise the before/after rejection using the same canonical request, the actual dispatcher admission function, both SQL overloads, the real unchanged Backend natural tick and eligibility implementation, bounded concurrent credit recovery, negative authority/binding cases, the fence, exact predecessor-drift rejection and two independent synthetic accounts. The physical success/failure outcome is a local fixture only: no phone, Instagram or ADB is used. A passing fixture is not natural-field certification.

Existing restriction, human authorization, terminal lineage and control-plane regression evidence accompanies the governed candidate. Production activation requires the final signed manifest and separate activation authorization. No scheduler, timezone, reservation, Follow/Mute/Like, CT Resume, BotApp, Backend source or Target Lifecycle file changes in this patch.
