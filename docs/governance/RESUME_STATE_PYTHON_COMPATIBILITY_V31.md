# Resume state Python compatibility — Patch 3.1

Approval 1 only. Base Worker `2c34a10cdedc5eba65aab87b046be1b12bffd6bd`.
Backend and BotApp are unchanged. No migration, runtime switch or business action.

## Broken boundary and minimal repair

`035f3a343d93f9a77f8a071b63931dcd5cdcd84d` introduced the public store
symbol `RESUME_STATE_RESUME_REQUESTED = "resume_requested"`.
It still existed at `3bb4d6bfc725139b8b7646bfc07f61c5ea41b6c9` and was
removed by `40d1c29296e79a788151e8b8f6257bb83dfce20e`.
The one runtime importer remained in
`auto_restart_runtime._validate_human_confirmed_resume_at_claim`, reached by
`validate_auto_restart_request_at_claim` from the dispatcher.

The new definition belongs only to `account_session_resume_state_contract`.
The human reader imports it directly. The store re-exports the same symbol
for backward compatibility; it does not define a second value. All uses of
this lifecycle value inside the canonical module reference the constant.
No semantic difference exists between old and current `resume_requested`.
The business outcome `partial_resumable` is deliberately NOT restored as a
SQL lifecycle state.

## State consumer inventory

All eight states are defined in `DB_ALLOWED_RESUME_STATES` in the canonical
Python contract, accepted by the Control Plane SQL CHECK, and present in the
backend canonical list. Worker writes below distinguish Python store writes
from control-plane RPC writes. “Read/reject” is an explicit fail-closed result,
not missing state support. Passing the live-proof check is not itself permission
to schedule: quotas, lineage, windows and other safety gates still apply.

| STATE | DEFINED | WORKER_WRITES | DB_ACCEPTS | AUTO_RESTART_READS | HUMAN_RESUME_READS | BACKEND_READS |
| --- | --- | --- | --- | --- | --- | --- |
| run_active | canonical set + store export | early plan | yes | requires live run/request/lease proof | preauthorization rejects; claim rejects | explicit live-proof guard |
| pre_device_stopped | canonical set | control-plane RPC | yes | zero-work recovery contract | preauthorization rejects; claim rejects | understood; not run_active |
| recovery_enqueued | canonical set | control-plane RPC | yes | recovery capsule/request lineage | preauthorization rejects; claim rejects | understood; not run_active |
| awaiting_human_resume_authorization | canonical set + store export | terminal incident / failed human resume | yes | human authorization branch | preauthorization eligible; claim rejects | human recovery projection |
| resume_requested | canonical set + single symbol | safe partial terminal mapping; authorization RPC | yes | canonical frozen resume plan | preauthorization rejects; authorized claim accepts | canonical plan / recovery projection |
| resume_succeeded | canonical set + store export | successful human/automatic resume | yes | terminal / no automatic new work from state alone | preauthorization rejects; claim rejects | terminal recovery projection |
| not_recoverable | canonical set + store export | unsafe/unconfirmed verdict; exhausted recovery RPC | yes | fail closed | preauthorization rejects; claim rejects | terminal recovery projection |
| completed | canonical set + store export | terminal completed session | yes | no work from state alone | preauthorization rejects; claim rejects | completed projection |

Consumer entry points:

- Writer: `account_session_resume_plan_store.create_early_resume_plan`,
  `record_terminal_failure`, `record_end_of_session`,
  `record_automatic_retry_terminal_state`, `mark_automatic_retry_success`,
  `mark_resume_outcome`.
- Dispatcher: `account_run_request_consumer` calls the canonical claim
  validator; its terminal hooks call the store. It does not invent another
  resume-state enum. `account_session_orchestrator` checks the confirmed end verdict.
- DB: `20260826021814_control_plane_reliability_v1.sql` CHECK and recovery RPCs;
  `20260826222059_resume_plan_contract_v1.sql` terminal persistence and bounded
  stale reconciliation. No SQL change in Patch 3.1.
- Backend: `resume-state-contract.ts` (`resolveRunActiveProof`,
  `backendUnderstandsEveryDbResumeState`), `auto-restart-data.ts` canonical row
  projection, `auto-restart-candidate-policy.ts` safety decision,
  `incident-resume-authorization.ts` (`evaluateReadyToResume`) and
  `auto-restart-tick.ts` (`processHumanConfirmedResumes`).
- Tests: existing schema, store, canonical mapping, Auto Restart and human
  suites plus the new Python API and E2E suites. Generic IDs cover five new
  accounts, all eight states, two invalid states and S1/S2/S3/no-S4.

## Why the old gate missed the defect

The old gate verified `WORKER_EMITTABLE_RESUME_STATES ⊆ DB_ALLOWED_RESUME_STATES`
and SQL safety assertions. A Python export can disappear while those sets
remain identical. The human import is lazy, so importing the top-level module
also passed. The new fixture explicitly enters that path and resolves every
runtime `RESUME_STATE_*` import. It fails on the exact broken baseline.

## Permanent E2E gate

Run `scripts/verify-resume-state-end-to-end-contract.py` with an explicit clean
backend checkout, Node 22 supporting TypeScript stripping, a clean environment
without database credentials and a new output path. The script runs the real
Python fixtures, forbids Python network access, and executes the backend pure
state, safety-policy and human-authorization functions with read-only fakes.
Source integration assertions supplement executable checks; no production
tick or database call is used. Missing backend input or any failure is fatal.

The receipt binds tested Worker files and backend source hashes. Its compact
`resume_state_contract` block must be included in the newly signed manifest,
with the receipt SHA-256, backend SHA and three PASS gate results. The tested
source hashes must equal protected manifest entries. Both `verify_repository`
and `verify_deployment_candidate` reject missing, failed or mismatched evidence.
CI also reruns Python/schema/human fixtures. Tests prove that a signed generic
recertification PASS cannot override any failed resume contract gate.

This intentionally invalidates older evidence for a candidate containing
Patch 3.1. No production verifier or existing release is replaced in Approval 1.
Historical rollback packages retain their original verifier and certification.

## Preserved behavior / promotion boundary

`partial_resumable → resume_requested → schedule_resume` remains unchanged.
Terminal persistence is authoritative with idempotent RPC plus exact reread.
Stale `run_active` requires reconciliation at the natural tick; no manual repair.
Attempt identity comes from the canonical claimed request, with maximum 3.
This validation step returns a policy and does not create or increment attempts.

After freeze: Approval 2, exact final candidate commit, manifest regeneration,
complete recertification, new detached signature, machine lineage/registry
alignment, zero-gate and immutable activation receipt remain mandatory.
No candidate is promotable based solely on this document or an unsigned receipt.
