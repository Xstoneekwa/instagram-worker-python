# Follow60 Mainline change and activation protocol V1

## Human contract

The nominal path requires exactly two external Liam signatures. Approval 1
binds `CHANGE_ID`, `BASE_SHA`, exact authorized paths, dependency scope, nonce
and expiry. It permits repeated local edits only inside that scope. It never
permits commit, push, package, promotion or activation. Approval 2 binds the
frozen diff, candidate manifest and protected-scope hashes and permits the
single final certification path. Its signed payload is the final V3.1 manifest
itself: the same detached Ed25519 signature is both approval 2 and the manifest
signature, so the nominal path has no third signing step.

The Liam private key remains outside the repository, CI and runtime at
`$HOME/Library/Application Support/BotApp/follow60-lock-v3.1/liam-private.pem`.
Only deterministic Python Ed25519 verification is authoritative.

## Canonical states

`LOCKED_READ_ONLY → CHANGE_AUTHORIZED → WORK_IN_PROGRESS → CANDIDATE_FROZEN →
FINAL_APPROVAL_PENDING → RELOCKING → LOCKED_CERTIFIED → PROMOTABLE →
ACTIVE_CERTIFIED`.

Every other transition fails closed. A byte change after freeze invalidates
approval 2. Read, audit and tests remain allowed while locked. Physical writes,
create, delete and rename are forbidden. During work, only approval-1 paths may
be writable; the rest of the protected scope remains root-owned and immutable.

## Shared gates

`follow60_change_protocol_v1.evaluate_gate` is the single policy decision for
local edit, commit, push, CI, package, release, runtime switch and Follow60 run
start. Existing lock verification, detached signature verification and the
physical lock are mandatory evidence for `LOCKED_CERTIFIED` and later states.

## Quick runbook

1. From a certified locked base, generate approval request 1 with exact paths.
2. Liam signs that JSON locally; verify with the repository public key.
3. Unlock only the signed paths and work/test freely before expiry.
4. Freeze: make the scope read-only and emit exact file, diff, manifest and
   protected-scope hashes.
5. Liam signs the exact final manifest/approval JSON once.
6. Verify approval 2, relock physically, install/verify that final manifest,
   consume approval 1, run all gates, commit/push/package, then promote once.
7. Publish full SHA, release path, manifest hash, scope hash, signature status,
   `runtimeRootOk` and lock version in the runtime attestation.

Recovery is fail-closed: an expired authorization, out-of-scope path, changed
freeze, invalid signature or unexpected state returns to `LOCKED_READ_ONLY`.
Rollback selects only a previously immutable, signature-valid release and does
not reuse either approval. The audit ledger records every state transition,
hash, verifier result and activation identity without secrets.
