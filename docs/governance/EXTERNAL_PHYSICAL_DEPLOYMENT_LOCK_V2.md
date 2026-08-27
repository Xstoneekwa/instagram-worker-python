# External physical seal and deployment transaction V2

This is a protocol-only candidate. No activation is authorized by this document.
The functional Patch 3.1 files remain byte-identical to `a16a02df6066e80a2992e9c052b16bb397dafa7e`.

## Broken boundary

The old installer wrote `.follow60-write-lock-v3.1.json` inside the certified
Git worktree. The V2 identity scanner correctly rejects that untracked file.
External storage is feasible and is used; no allowlist or ignore is added.

## Two different lifetimes

Canonical storage is `/Users/admin/phonefarm-runtime/run/follow60-lock-v2`.
It is not configurable via CLI or environment. Its root is root-owned,
non-group/world-writable and immutable. Guard, seals, transaction and history
paths reject symlinks. Initialization and mutation are root-only operations.

- `seals/<sha256(canonical-release-path)>.json`: permanent, root-owned,
  read-only and immutable proof of physical sealing. It binds exact commit,
  tree, canonical diff, manifest bytes, detached signature bytes, release root,
  protected files and every parent directory. The source files and directories
  themselves retain root ownership, no write bits and immutable flags. This
  seal does not expire when its installer exits; that owner is audit metadata.
- `transactions/active.json`: a temporary promotion record binding the same
  identities, the seal hash, timestamp and process owner (PID, OS process start
  and UID, boot session). A real exclusive OS flock on the fixed immutable
  `guard` file is held by the same process through promotion. A dead process's
  record remains blocking until explicit recovery; timestamps alone never
  permit takeover. PID reuse/another boot is not the same owner.
- `history/`: immutable records moved here after promotion or proven stale
  ownership. No silent deletion or overwrite. Exact record hash must match
  before archival. Permanent seal evidence remains in `seals/` after promotion.

The exact lease payload keys are `protocol_version`, `candidate_commit_sha`,
`candidate_tree_sha`, `canonical_diff_sha256`, `manifest_sha256`,
`signature_sha256`, `candidate_root`, `seal_sha256`, `created_at`, `owner`.
`owner` contains `pid`, `process_start_and_uid`, `boot_session`. No secrets.
Records are fsynced and published exclusively by atomic hard-link publication
in a root-controlled directory, then marked immutable. Existing records cannot
be overwritten. Interrupted/partial initialization fails closed.

## Order and machine enforcement

1. Verify exact committed identity, manifest, signature, protected bytes and
   admissible source worktree using the unchanged canonical V2 diff and scanner.
2. Install the physical OS seal with the canonical root-only installer; record
   its proof externally only after rechecking the exact signed identity.
3. Native `switch-release` requires `DeploymentTransaction`: validate candidate
   and persistent seal, hold the exclusive guard, acquire the external record.
4. Recheck exact commit/tree/diff/manifest/signature, clean/admissible source,
   physical permissions/flags and process ownership after acquisition.
5. Capture the natural zero-gate. No manual business tick is created.
6. Recheck the same identities and lease immediately before atomic switch.
7. Publish the immutable promotion receipt. Existing receipt-publication failure
   rollback behavior is preserved.
8. Validate receipt binding and archive the transaction record. Release the
   process flock. Leave permanent physical seal evidence in place.
9. Existing service alignment, shared startup skip, canonical registry update
   and post-promotion gates remain required and are not changed here.

The signature-only candidate gate is intentionally usable **before** a seal
exists, for pre-commit certification and packaging. It is not the final
activation gate. The native CLI cannot promote without the external transaction
and strict physical seal. No CLI test-mode or environment override reaches the
activation path; legacy physical verifier test options cannot authorize a switch.

Installed signed manifest sidecars and three existing deployment receipts keep
their existing exact exceptions. No exception is added for the old lock file,
arbitrary JSON, dotfiles, protected files or other untracked source.

## Stale recovery

Use the root-only `scripts/recover-follow60-external-lock-v2.py` with the exact
target and observed lock SHA-256. It acquires the same exclusive guard, verifies
trusted storage and record metadata, compares the expected hash, and requires
OS evidence that the owner is absent or has a different boot/process-start
identity. Unknown liveness or an alive original owner fails closed. Preserve
the record bytes in immutable history. The command does not create a replacement,
switch, restart, run or tick. Only a fresh canonical promotion can acquire a new
record, after revalidating the signed candidate and physical seal.

## Certification boundary

The old signed candidate and its signature are preserved and remain valid for
their old bytes. This repair changes protected protocol code used by native
promotion and physical verification, so its deployable candidate must have a
new SHA, complete recertification, exact-commit manifest and external signature.
Reusing the old signature for these changed bytes is forbidden. Functional
Patch 3.1 remains unchanged. No root production seal is installed by local tests.

Acceptance A–L exercises real Git and Ed25519 signatures with synthetic IDs.
Unprivileged tests explicitly model root/immutable metadata; a separate
isolated root smoke is needed to attest real macOS ownership/flags. Historical
OS permission tests remain, and native switch tests assert missing transactions
cannot reach the zero-gate or symlink switch.
