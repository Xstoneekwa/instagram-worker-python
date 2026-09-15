# Incident system global resolution V2

## Scope and authority

Local review candidate on Worker d82e8f80abdf664948b36abb23697d683320c693 and Backend 20718305def88ae2ceb9a0c2fa3bc2b016151cf1. Approval 1 was verified before protected edits. No production migration, promotion, restart, forced run or ADB action is part of this mission. This document is not Approval 2 or deployment authorization.

## Corrected behavior

Human resolution retires the hold belonging to the resolved restriction immediately. It uses the existing superseded state, not cleared physical proof. Exact ownership of the account pause is still required to restore active lifecycle. Independent manual/commercial pauses, another incident, assignment/fleet/capacity and security authorities remain intact. Resolving the last independent incident retries restoration of a previously retired incident-owned hold. Reconciliation is write-idempotent. Legacy already-consumed preflight requests remain supported with their exact authorization and physical release proof.

A terminal third attempt marks its existing resume plan not_recoverable / execution_attempt_exhausted and expires armed authorizations for that source. Repeated ticks do not rebuild it. The existing V4 consumer still rejects a fourth business attempt. The next distinct scheduled occurrence uses the unchanged cron; no same-slot replacement or new retry system is introduced.

The request authorization_id survives claim normalization, environment serialization and preflight terminal proof. Once exact durable success is established, a downstream reconciliation, projection or terminalization failure is retained without creating a false blocking incident, regardless of its class, message, module or reason name. Suppression requires exact request/run/account bindings, successful cleanup and lock release, and either the canonical preflight terminal contract plus its hold evidence or the complete persisted four-phase account-session terminal contract. A later operation that changes the whole-run contract to non-terminal or failed still follows the existing incident path. The internal defect remains visible through the existing structured log and audit channel. Failed post-success resume-plan persistence remains a technical reconciliation failure while preserving successful run proof. Unproven, stale, foreign, timed-out, canceled and actual physical/runtime failures retain existing behavior.

Runtime exception class is bounded to an identifier and retained with the existing failure module/function and incident fields. Operational incident remains the same category. Its existing notification summary exposes that structured cause through the existing redaction path.

## Approved presentation

Pause until YYYY-MM-DD applies only to instagram_action_rate_limit and preventive_follow_restriction after the existing classifier establishes timed restriction. The exact canonical expiry timestamp remains unchanged. Calendar display uses businessDayKeyFromIso and the existing operator timezone (default Africa/Johannesburg), including UTC midnight/year boundaries. Resolved incidents do not retain this presentation as an independent blocker. The Backend JSON snapshot freezes all 32 current labels; any label change produces a reviewable diff.

## Evidence and limits

Final V5 evidence is external to both candidate checkouts:

- `/Users/admin/Projects/incident-system-v5-full-composed-3`: 17/17 composed tests PASS after replaying 126 migrations in disposable PostgreSQL 17/PostgREST. It includes the BMY same-occurrence controls and real next recurrence, the producer matrix, cross-category interactions, NAB request/claim/policy/terminal persistence, `/profiles`, `/profiles/live`, scheduler and Auto Restart projections.
- `/Users/admin/Projects/incident-system-v5-category-matrix-3`: all 32 catalog categories are classified. Every safely executable repeatable producer completes two real producer-to-resolution cycles (26/26). Six entries are explicitly non-repeatable: one superseded alias, three test/transient-only categories and one closed historical terminal category plus its documented replacement; none is fabricated through a direct incident insert.
- `/Users/admin/Projects/incident-system-v5-bmy-recurrence-6`: the exact existing cron rejects same-slot reopen and creates the next naturally distinct occurrence after only the disposable clock advances. The old request, root, occurrence key, timestamps and attempts remain unchanged; the fresh lineage starts at attempt 1.
- `/Users/admin/Projects/incident-system-v5-nab-projection-6`: production-shaped restriction preflight request, real claim normalization, admission, terminal proof and persistence, zero false incident, canonical blocker removal, scheduler/Auto Restart eligibility and both profile projections PASS. No device process, ADB or real secret is used.
- `/Users/admin/Projects/incident-system-v5-interactions-9`: timed plus operational, identity plus operational, and independent manual pause interactions PASS. Resolving A leaves B's canonical blocker; only resolving B restores incident-derived eligibility. The independent manual authority survives.
- `/Users/admin/Projects/incident-system-v5-final-evidence`: Worker affected suite 119 total / 118 PASS / 1 SKIP and Worker cross-category suite 120 total / 105 PASS / 15 SKIP. These are disjoint selections: 239 total / 223 PASS / 0 FAIL / 0 ERROR / 16 SKIP. Backend affected suite is 109/109 PASS. Skips are never counted as passes and their database boundaries are covered by the composed run.

The canonical terminal validator is authoritative whenever its proof field exists. A malformed, stale or foreign proof remains rejected and cannot enter the legacy fallback. The existing fallback is limited to the historical absent-proof serializer failure and still requires independent cleared-hold, request/run/account/authorization/plan, zero-action, cleanup and lock-release evidence. An explicit incoming failed/aborted/nonzero terminal contract cannot be masked by an older persisted success.

The BMY clock seam exists only inside the disposable database. It replaces the single `now()` clock expression of the exact production schedule-gate definition, and the assignment, seed plan and source request all begin in the same real canonical occurrence. Product scheduler code, stored history and production functions are unchanged.

The operator-approved `Pause until YYYY-MM-DD` label remains limited to `instagram_action_rate_limit` and `preventive_follow_restriction` after the existing timed classifier establishes that family. The exact expiry stays authoritative and display uses the operator business timezone rather than UTC truncation. Resolving the incident removes its presentation immediately when no independent blocker remains.

Canonical Restriction Blocker candidate `40fd52fead60d69bcca41db311d9959382c68c41` remains an ancestor of Backend base `20718305def88ae2ceb9a0c2fa3bc2b016151cf1`. Its overlapping blocker files preserve the canonical RPC and priority semantics; V5 changes no parallel candidate. CT Resume, BotApp, Follow/Mute/Like execution, Target Lifecycle and production are unchanged.

No live Instagram/device behavior is claimed by the isolated fixtures. Backend full typecheck is not newly claimed as clean: the exact V4 baseline/candidate logs contained the same 180 pre-existing diagnostics, and Backend candidate bytes did not change during V5. The affected executable Backend suite is the current 109-test authority for this change.

## Reproduction

Use scripts/test-incident-system-global-resolution-v2.py with --backend-root pointing to clean exact 2071830 and a new external --output directory. It requires PostgreSQL 17, PostgREST, installed Python project dependencies and Backend test dependencies. The baseline comparison expects the exact 2071830 source export at the evidence parent's backend-baseline-typecheck directory, with node_modules linked to the candidate dependency tree; reconstruct that export with git archive before rerunning after cleanup. No dependency installation is implied for read-only review.

The isolated harness rejects non-local HTTP destinations, uses synthetic local credentials and removes its PostgreSQL temporary cluster. Never run the candidate SQL against production as part of this review.

## Review boundary

No Follow/Mute/Like execution, CT Resume, Target Lifecycle, scheduler algorithm, attempt maximum, production control-plane source or incident type/status was added or changed. Normal governed freeze/commit and Approval 2 remain separate from this source review. Deployment requires its own fresh canonical gates.
