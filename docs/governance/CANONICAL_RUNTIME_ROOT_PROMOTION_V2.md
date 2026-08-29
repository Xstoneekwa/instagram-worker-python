# Canonical runtime-root promotion V2

This protocol is a deployment-only invariant. It does not alter Follow,
Unfollow, scheduling, candidate selection, or any device action.

## State machine and ordering

The only successful ordering is:

1. certify and physically seal the exact candidate (`SEALED`);
2. prove the canonical four-table production zero-gate;
3. stage the pending receipt and atomically prepare the shared startup-skip
   token with exact service ownership/mode evidence;
4. fail in `SEALED` if that permission/ownership gate does not pass;
5. atomically replace `PHONEFARM_RUNTIME_CURRENT_LINK`;
6. publish an immutable `SWITCHED_PENDING_SERVICE_VERIFICATION` receipt;
7. compare-and-swap the canonical Worker registry to the candidate and bind it
   to that pending receipt;
8. prove the same zero-gate again immediately before service restart;
9. ask the current-link-resolved wrappers to restart, in order, dispatcher,
   heartbeat, notifier;
10. poll for a bounded period until every service has exactly one PID and its
   CWD, resolved module root, and Git SHA equal the active candidate;
11. prove the dispatcher consumed and cleaned the exact prepared token inode;
12. stage the final receipt, compare-and-swap the registry binding to the staged
   receipt's exact bytes, publish the immutable `PROMOTED` receipt, and archive
   the deployment lock.

The registry may therefore describe the intended/current candidate while the
promotion is pending. A `PROMOTED` receipt is impossible until active symlink,
registry SHA/root, all three runtime service identities, token consumption and
token cleanup have converged.

## Failure and recovery

Failure to establish exact startup-token ownership/mode occurs before the
symlink change, removes preparation artifacts and leaves the release `SEALED`.
Failure before the symlink change leaves the old release active. Failure to
publish the pending receipt rolls the symlink back. Any later failure leaves
the candidate explicitly in `SWITCHED_PENDING_SERVICE_VERIFICATION`; it never
creates a final promotion receipt and never archives the deployment lock.

Unexpected, duplicate, missing, or old-root service processes are reported and
are not silently killed. Recovery uses the existing root-only stale-owner
proof, followed by operator-reviewed retry or rollback. A controller crash has
the same fail-closed result because the immutable pending receipt and external
deployment lock survive the process.

The controller pre-arms the shared token before switching roots. The dispatcher
wrapper calls the same idempotent helper after its zero-gate, validating the
existing inode instead of creating a second token. This preserves suppression
exactly once, introduces no startup business tick, and leaves the next natural
cadence eligible.

## Signed status-trust completeness

The protected scope and manifest generator require the status-trust cache,
status verifier, cache installer, runtime controller, and production lineage
gate. Generation fails if one is missing. The status verifier binds the exact
bytes of those files to the signed manifest; no post-signature scope patch is
accepted.
