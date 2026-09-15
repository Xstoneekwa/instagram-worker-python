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

Historical V5 evidence is retained below, but its NAB certification and six-category classification were rejected. It is not V6 certification:

- `/Users/admin/Projects/incident-system-v5-full-composed-3`: 17/17 composed tests PASS after replaying 126 migrations in disposable PostgreSQL 17/PostgREST. It includes the BMY same-occurrence controls and real next recurrence, the producer matrix, cross-category interactions, NAB request/claim/policy/terminal persistence, `/profiles`, `/profiles/live`, scheduler and Auto Restart projections.
- `/Users/admin/Projects/incident-system-v5-category-matrix-3`: historical 26-category replay only. The six NON_REPEATABLE assumptions were invalidated by a real second publication after real resolution. No canonical prohibition was established; those classifications must not be used as current authority.
- `/Users/admin/Projects/incident-system-v5-bmy-recurrence-6`: the exact existing cron rejects same-slot reopen and creates the next naturally distinct occurrence after only the disposable clock advances. The old request, root, occurrence key, timestamps and attempts remain unchanged; the fresh lineage starts at attempt 1.
- `/Users/admin/Projects/incident-system-v5-nab-projection-6`: rejected as end-to-end certification: it directly released the hold, constructed terminal proof and persisted completed. V6 replaces these shortcuts with the real runner and dispatcher boundaries. Preserve this artifact solely as historical evidence.
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

## V6 local qualification — no freeze or final commit

PRE_FREEZE_INTERNAL_DEFECT = YES. The same V6 was corrected before freeze.
Final candidate identities remain unassigned: the full-suite gate is not green.

The invalid test assumption covered email_verification_code_required, orf4b_smoke_test,
orf4c_smoke_test, scheduled_early_failure_retrying, system_test_incident and
welcome_retry_final_unverified_send_surface_lost. The first still has its historical
publisher in historical_auto_login_07ee.login_challenge_runtime; the other five
are accepted by runtime_incidents.publish_account_incident and the shared
public.upsert_account_incident API. No dedicated current device producer is
claimed for those five. The generic API accepts non-empty categories and
deduplicates only active incidents, explicitly permitting a new incident after
resolution. There is no category-specific prohibition found in the current
producer contracts. Their repeatability does not imply canonical blocking status.

PREVIOUS_CLASSIFICATION = INVALID_TEST_ASSUMPTION.
BUSINESS_CONTRACT_CHANGED = NO. PRODUCER_BEHAVIOR_CHANGED = NO for the six reclassified producers.
The permanent test_previous_nonrepeatable_classification_is_refuted preserves
the 6/6 counterexample; no production prohibition was introduced.

The targeted V6 matrix at /Users/admin/Projects/incident-system-v6-positive-matrix-2
passed the initial producer/resolution replay, but its admission claim was
subsequently strengthened: evaluateRunStartEligibility is only a partial gate.
V6 now additionally requires the canonical profiles/live projection and an
actual newly queued request from runScheduleSessionCron for each cycle. These
requests are canceled through the real RPC with their exact lease released; no
device executes. The final composed replay now passes all 23 tests, including 32 categories, 64 real producer/resolution cycles and 64 newly queued scheduler requests.

Real NAB chain work exposed a direct dependency: the existing SQL release RPC
allows an exact resolution-retired superseded hold, while the Python loader and
runner formerly rejected it. The scoped V6 extension aligns only the authorized
preflight read/gate. Ordinary blocker reads and the SQL business contract remain
unchanged. No current candidate identity has been frozen or committed.


### V6 final local evidence and unresolved qualification gate

The signed incident-link extension fixes a second dependency revealed by the real
producer: login_package_mismatch published an incident but its review action had
no incident_id. The actual orchestrator now forwards that exact ID through the
existing login dashboard-action helper/RPC. Real resolution retires its linked
action. Independent actions and failed-publication fallback are unchanged.
No production category, resolution rule or SQL was changed by V6.

- `/Users/admin/Projects/incident-system-v6-full-composed-final`: 23/23 PASS,
  126 local migrations, no skips. category-matrix.json has 32 categories, each
  with two real publications, resolutions, canonical positive projections and
  actually queued cron requests. The six-category refutation remains 6/6.
- NAB uses the real runner in an isolated child, canonical hold release, cleanup,
  dispatcher terminalization, publication authority and projections. Only Android
  transport and test integrity infrastructure are fixtures; no real phone or
  signed deployment certification is claimed. The true pre-success identity
  failure remains blocking. Evidence: nab-projection-chain.json and
  nab-pre-success-failure.json in that directory.
- The proof suite covers all 64 initial/reloaded/runtime combinations, 12 explicit
  source cases and the foreign-authorization review counterexample. A present
  rejected proof never invokes the historical fallback. Strict type checks also
  reject False/0 and True/1 contradictions in physical facts.
- `/Users/admin/Projects/incident-system-v6-full-regression-final`: Backend
  affected suite 109/109 PASS; Worker broadened selection 42 modules / 776 test
  cases, 751 PASS, 22 SKIP, 2 failures and 1 test error. Two additional setUpClass
  errors came from invoking database-only suites without their required socket;
  those invocations are not passes. 38 modules individually pass (735 passing
  cases and 22 skips). No dependencies were installed.
- The wrapper self-test fails at the unchanged signed-deployment verifier, before
  its self-test. The historical scheduled-preflight fixture lacks the now-required
  device binding (one failure and one error). Both reproduce on exact V5 and V6;
  source hashes and separate baseline logs are retained. They are not silently
  excluded or counted as passes.
- `/Users/admin/Projects/incident-system-v6-full-restriction-final`: the inherited
  historical SQL suite reports 31 tests / 6 PASS / 12 failures / 13 errors. The
  same 25 named failures/errors reproduce on exact V5 using the same local harness
  and byte-identical migrations, in incident-system-v6-baseline-restriction-comparison.
  That suite contains obsolete hold-state expectations and destructive historical
  rollback tests incompatible with its current combined fixture. No production
  rule or assertion was weakened to force it green.

The full-suite gate is therefore FAIL despite the passing V6-specific evidence
and zero new failures in the compared historical selections. No staged freeze,
new final candidate identity, commit, push, deployment or independent review was
performed. Resolving the test qualification gap is required before the operator's
all-gates-green commit condition can be satisfied. Historical failures are visible
in the evidence; this document does not grant a waiver.

Reproduction of the composed suite uses the current script, exact clean Backend
schema dependency 20718305def88ae2ceb9a0c2fa3bc2b016151cf1, the candidate Backend
90fd0ef932f73d353d3d4b51c68ac2047c9c9864 and a new external --output. After a future
Backend commit, pass its exact --candidate-backend-sha explicitly. The temporary
schema clone is regenerable; all evidence directories are retained.
