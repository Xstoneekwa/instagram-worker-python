# Disaster recovery

## Recovery objectives

The priority order is: protect accounts and credentials, stop new work, preserve
evidence, restore control-plane integrity, then restore execution. No incident
justifies blind phone actions or unreviewed database writes.

## Incident classes

### A. Worker code or navigation regression

1. prevent new scheduling/requests through the approved control plane;
2. allow current work to terminalize if safe, otherwise use cooperative stop;
3. preserve logs, request/run IDs and the current symlink target;
4. execute [Rollback](ROLLBACK.md) to the saved immutable release;
5. verify zero active objects and a stable dispatcher;
6. investigate from a separate worktree.

### B. Dispatcher or launchd failure

- Inspect `dispatcher status --json`, PID ownership and logs.
- Never run a second consumer manually.
- If PID files are stale, use the controller's verified recovery path.
- Restart once only after queue/run/lock preconditions and explicit approval.
- Treat repeated ~60/90-second PID cycling as a supervision timeout regression.

### C. Mac loss

- Quarantine/rotate credentials according to the security incident plan.
- Build a clean host using [Install new Mac](INSTALL_NEW_MAC.md).
- Restore source/tag, immutable release and external runtime directories from
  verified backups.
- Re-authorize each phone manually; do not clone private keychains blindly.
- Run offline tests and read-only gates before activation.

### D. Supabase or data loss

- Stop new requests without altering evidence rows.
- Confirm project ownership, backup timestamp and migration history.
- Restore to an isolated environment first.
- Audit RLS, grants and SECURITY DEFINER functions before reopening traffic.
- Reconcile requests, runs, locks, assignments and incidents with a reviewed,
  idempotent plan. Never infer terminal states from UI alone.

### E. Vercel/backend outage

- Record deployment ID, aliases and error window.
- Use Vercel's immutable deployment rollback/promote mechanism under separate
  approval; do not rebuild during an emergency unless required.
- Verify authenticated routes, scheduler tick behavior and relay health before
  permitting Worker work.

### F. BotApp corruption

- BotApp is not scheduler authority; leave the dispatcher untouched if healthy.
- Hash and quarantine the bad bundle, then reinstall a verified artifact.
- Confirm bundle identity, signature and asar hash.
- Do not use a UI error as authorization to repair backend state.

### G. Phone/clone failure

- Remove the account from scheduling through the approved backend operation.
- Preserve assignment and incident evidence.
- Do not log out or clear app data until identity/binding evidence is captured.
- Recreate phone/clone under a separate, account-specific GO.

## Evidence preservation

Preserve read-only copies of:

- Git/tag/release identifiers;
- runtime status JSON and process CWD;
- dispatcher/heartbeat/notifier logs;
- redacted request/run/incident identifiers;
- Vercel deployment metadata;
- installed BotApp hash/signature;
- database backup/migration inventory.

Never collect passwords, cookie values, service-role keys, raw XML containing
personal data or browser sessions into the repository.

## Recovery completion gate

Recovery is complete only when provenance is exact, offline tests pass,
cross-plane health agrees, request/run/lock state is reconciled, one dispatcher
is stable for twelve minutes and Liam explicitly authorizes business resumption.
