# Worker promotion transactional recovery V1

This contract changes release machinery only. It does not change Follow,
Mute, Like, CT Resume, scheduler cadence, restriction handling, Target
Lifecycle, phone or ADB behavior.

The durable order is:

1. acquire the external deployment transaction;
2. acquire `worker_promotion_active` in `phonefarm_runtime_barriers`;
3. let existing work drain and prove the exact zero gate;
4. pin old/new release identities and the Worker registry pre-state;
5. pause dispatcher, heartbeat and notifier through their verified launchd user
   domain so `KeepAlive` cannot respawn old code;
6. switch the execution symlink while admission stays fenced;
7. start all three services and verify exactly one process per service by
   resolved module root and full release SHA;
8. prove the dispatcher consumed a token bound to transaction, phase, release
   SHA and dispatcher module root;
9. durably publish commit evidence, then atomically publish the Worker
   generation `ACTIVE=NEW, ROLLBACK=OLD`;
10. reconcile storage projections, independently reverify terminal state,
    finish the external transaction while fenced, then release the fence and
    publish immutable finalization evidence (see V2).

Before step 9, any error restores the old symlink and all three old services,
using a distinct rollback token. The Worker registry stays on the old
generation. Admission opens only after the complete old state is verified.
Rollback failure leaves both the durable journal and admission fence active
with `PROTECTED_RECOVERY_REQUIRED`.

After step 9, recovery preserves the new generation. It retries projection,
receipt and lock finalization idempotently. It does not roll back a committed
generation because a later projection failed.

The journal is canonical JSON published by atomic rename with file and
directory `fsync`. Every transition contains the previous event hash. A hard
crash therefore leaves either the previous complete generation or the next
complete generation, never a partially written successful phase.

`recover-promotion <expected-lock-sha256>` is the only recovery entry point. It
requires the exact immutable external lock hash, proves the recorded owner is
dead, re-owns the same lock without archiving it, loads exactly one matching
nonterminal journal, and chooses rollback or forward finalization from the
canonical Worker generation.

If a prior controller already archived the exact external lock after the
runtime generation committed, recovery accepts only the archive whose bytes
match the operator-supplied SHA-256 and whose candidate identity matches the
single journal. It then performs terminal verification and journal
finalization without opening a second deployment transaction. A mismatched or
ambiguous archive fails closed.

The Supabase fence is persistent (`expires_at = infinity`) and restricted to
`service_role`. It blocks request insert/claim transitions, run creation,
device-lock acquisition and tick-lock start. Existing terminal updates remain
allowed so natural drain can complete. Only the exact transaction ID may
release it with `PROMOTED` or `ROLLED_BACK`.

Startup-token consumption and pre-consumption cancellation both create a
durable receipt before the mutable token is removed. Receipts bind the token
ID, transaction, phase, release SHA, module root and consuming service where
applicable. Rollback always creates a separate token.

Storage bootstrap V3 discovers the active symlink, canonical rollback,
stable-controller symlink target, pending Approval worktrees and all parallel
Git worktrees. Every unknown release remains `UNKNOWN_KEEP`; the classifier has
no delete path. Projection updates replace the prior active, rollback and
controller authorities instead of accumulating historical runtime releases;
approval and parallel-task protections are preserved.

This document is authoritative for Worker promotion sequencing and recovery.
It supersedes the nontransactional `switch-release` ordering described by
`EXTERNAL_PHYSICAL_DEPLOYMENT_LOCK_V2.md` and the standalone token lifecycle in
`STARTUP_TICK_SKIP_CONSUMPTION_V1.md`; their physical-lock and token-validation
requirements still apply where incorporated above.
