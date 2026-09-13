# Auto Restart terminal lineage V1

An ordinary continuation could finish its business actions and then fail to
persist its terminal plan. The enqueue RPC created a new canonical root at
attempt 1 / retry 0, while its metadata described attempt 2 / retry 1. Atomic
admission copied the SQL tuple into the capsule; terminalization correctly
rejected the conflicting runtime tuple. This change fixes identity upstream.
It does not relax the terminal store or replay historical actions.

## Creation and claim contract

The new forward migration adds `account_run_request_01_auto_restart_lineage_v1`
after the existing human-authorization trigger and before the immutable lineage
default. Ordinary account-session Auto Restart inserts must reference an exact,
terminal source request and its run, both belonging to the same account. The
source SQL row supplies the business root and next attempt. Attempts 2 and 3
are allowed; attempt 4, a missing source, cross-account source and contradictory
projections are rejected atomically. Existing unique root/attempt, immutable
lineage, eligibility, admission fence and Fleet device lease guards remain.

Top-level metadata and embedded plan receive the same canonical tuple. A legacy
`scheduled:` label is not UUID authority and is replaced in those projections.
The Fleet wrapper's `fleet_atomic_enqueue_payload` remains its original input
snapshot for exact idempotent replay; it is not used as terminal identity.
The existing human V4 authorization path is left intact, including its frozen
plan and single-consumption semantics. Existing restriction verification and
explicit first-attempt canary contracts retain their own SQL handling.
Outreach retains its existing independent request and policy contract.

`validate_claimed_auto_restart_request` is the mandatory dispatcher boundary.
It checks the full claimed SQL row against both metadata projections before
policy reads, atomic admission and runner spawn. Missing, malformed or
contradictory identity fails closed. The existing metadata policy validator
remains available to its callers; it alone does not establish claim authority.
The dispatcher calls the stricter boundary and passes the real claimed row.

The runtime envelope and JSON environment transport carry the exact canonical
root, attempt and retry. `source_request_id` describes ancestry; the terminal
`parent_request_id` is the **current request**, matching the admission capsule.
Frozen business phases, quotas, checkpoints and authorization plans are not
rewritten by that transport. The existing terminal store checks and confirms
the real persistence RPC, with its idempotent digest and readback unchanged.

## Isolated qualification

The harness replays the real Backend schema at
`e8419c560f3623a50cbdefdb9c3a9b56235725ea`, required Worker migrations and the
new migration into a disposable PostgreSQL 17 cluster. PostgREST and the Worker
RPC client communicate only over localhost using a synthetic local token.
No production credentials, dispatcher, phone or business engine are used.

```sh
/usr/bin/python3 -B scripts/test-auto-restart-terminal-lineage-v1.py \
  --backend-root /Users/admin/Projects/backend-archive-convergence-reconsolidated-v1 \
  --output /Users/admin/Projects/worker-auto-restart-terminal-lineage-v1-approval1/pg17-final
```

The output path must be new and external to the candidate. Evidence includes
exact migration hashes, logs, real enqueue/claim/admission/capsule/terminal RPC
results for retries 2 and 3, concurrent duplicate replay, attempt 4 rejection,
cross-account and conflicting-identity rejection, immutable lineage checks,
terminal-store negative controls and real V4 authorization consumption.
Verified action counts in the terminal fixture remain 72 through persistence
and replay. These are synthetic actions, not another execution of the incident.
The temporary cluster is stopped and removed at the end.

## Governance and activation boundary

Base Worker: `320de84755eddcba5110c4e5ee18d28f5632b7c9`.
Approval 1 authorizes only the eight closed paths and local tests/freeze/commit.
Final Approval 2 binds the resulting commit, tree, diff and certification.
All inherited V2 control-plane, Target Lifecycle and archive-convergence code
outside the explicit claim call remains unchanged.

No production migration, release, switch, service restart, historical repair or
forced run is part of this change preparation. Future activation must coordinate
the migration and Worker under the normal admission fence/zero gate: migration
alone does not fix the old Worker's runtime envelope; Worker alone rejects
inconsistent requests produced by the old SQL path. Both components must be
verified before new admission resumes. Keep the SQL change additive on rollback;
an old Worker must not resume ordinary continuations with an incompatible
envelope. Qualify that rollback constraint in the separate activation review.

Existing malformed historical requests are preserved as evidence and rejected
before further business execution. Correcting their presentation or incident
state requires a separate explicit reconciliation; no counters are reset here.
