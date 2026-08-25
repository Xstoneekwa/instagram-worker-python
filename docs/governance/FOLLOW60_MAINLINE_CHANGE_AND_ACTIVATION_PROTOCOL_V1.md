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

## Follow60 protected delta rule

Any byte or import-graph change to a path covered by Follow60 V3.1
automatically invalidates the previous certification.  The final candidate
must receive a newly generated manifest, the full historical regression
recertification and a detached Liam signature.  The signed manifest carries
`certified_candidate_sha`; it must equal both the candidate Git SHA and the
release Git SHA.  A manifest certified for SHA A can never authorize a later
commit B or C.

`follow60_deployment_gate_v1.verify_deployment_candidate` is the mandatory,
fail-closed decision used before release creation and runtime switch and at
dispatcher, heartbeat and notifier boot.  It verifies the future release with
the same protected-byte/import/signature check used at runtime.  There is no
environment variable, command flag or Codex bypass.  A protected tree change
with a stale manifest, or a regenerated manifest without PASS recertification
and signature, is a hard NO-GO.

Because a Git-tracked file cannot contain the SHA of the commit that contains
it without a self-reference, the exact final manifest and detached signature
are generated after the clean candidate commit and installed as immutable
release certification material by
`scripts/create-certified-phonefarm-release-v1.py`.  The source candidate
stays clean, while the release gate proves that the signed manifest binds its
exact HEAD before the release can be created or selected.

Canonical order:

`final candidate → protected diff scan → manifest regeneration → full
recertification → signature → remote parity → clean worktree → certified
release creation → zero gate → runtime switch → aligned services → one shared
startup-tick skip → post-activation gate`.

Incident example: release `563f7e6` changed protected login-classification
dependencies but retained the `8f9bc99` manifest.  The dispatcher correctly
refused startup with `FOLLOW60_MAINLINE_INTEGRITY_MISMATCH`; the 06:00 session
was never scheduled and no device/business mutation occurred.  This was a
deployment-governance failure detected correctly by the runtime integrity
guard, not a dispatcher defect.

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

## Protected navigation recovery invariant

Any Follow60 change affecting candidate-open or Followers recovery must prove
that recovery is driven by the freshly classified current surface, never by
the depth expected after the attempted action. The certified matrix must cover
Followers already present, source CT, exact candidate profile, post surface,
Explore/Search and unknown. It must also prove `MAX_UNOBSERVED_BACK_CHAIN=1`,
strict candidate identity before Follow, fail-closed unknown handling,
`target_completed=false` on recovery failure, and preservation of the first
causal reason. These checks are part of the frozen Approval 2 candidate and
cannot be weakened during relock or promotion.
# Stale viewport evidence invariant

The protected Follow60 scope must not dispatch a candidate-row tap from selection-time
coordinates. Actionability requires a current-generation, exact-identity `RowActionToken`.
All UI mutations revoke outstanding row tokens atomically. Changes to this invariant remain
subject to the two-signature Follow60 Lock V3.1 protocol.
