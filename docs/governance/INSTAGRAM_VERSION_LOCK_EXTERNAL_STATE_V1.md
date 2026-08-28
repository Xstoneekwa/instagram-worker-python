# Instagram version lock — external mutable state V1

Worker-only candidate based on `8750273c62402f2e4d5fae645469de2f111cacf6`.
No live migration, device command, runtime switch or production write is performed
by this implementation/test pass. Consolidated qualification is recorded in the
Approval 1 V2 report; this document does not authorize a final release signature.

## Ownership and path

`device.py::_lock_state_store` resolves the existing canonical
`phonefarm_runtime_control.runtime_paths().run_dir`, then uses:

`instagram-version-lock-v1/<sha256(host, device_serial, package)>/lock_state.json`

Typical root: `/Users/admin/phonefarm-runtime/run/instagram-version-lock-v1`.
The full scope is stored and checked in the record. No username/account exception
is used. This is Instagram version-baseline state, NOT the privileged Follow60
physical write-lock store and NOT a signed release manifest.

All old writers now use this external store. The legacy source
`<release>/lock_state.json` is read-only. No unseal, source write exception or
legacy-file rewrite is introduced.

## Read/migration authority

- A legacy baseline is accepted only with exact serial/package and a valid
  version code/name. A proved different device/clone is not a baseline for this one.
- Missing scope, malformed JSON or invalid baseline is an error, never `{}`.
- No files at either location is a distinct state; existing first-baseline
  bootstrap remains permitted but its durable write must succeed before admission.
- External authority has an exact schema/scope and positive generation.
- A migration records the SHA-256 of the legacy source. Later external changes
  can legitimately differ from that immutable source. If present legacy evidence
  changes without matching provenance, reject ambiguous dual state.
- A new compatible release without a legacy file reuses the scoped external state.
  A historical binary which only knows the old location is NOT certified as a
  compatible rollback. Never modify a sealed legacy file to accommodate rollback.

## Durability and concurrency

Directories are walked with O_DIRECTORY/O_NOFOLLOW and held directory descriptors.
Reads also open relative a validated parent descriptor. Mutable state, writer
lock and journal must be regular, current-user-owned, private files with one link.
The scoped mutable directory must be private/current-user-owned.

For a write:

1. Take nonblocking per-scope `flock` on `writer.lock`.
2. Re-read authority; validate baseline/bindings; require generation/provenance CAS.
3. Append journal intent (`journal.jsonl`) with scope, previous/next generations
   and payload hash; flush/fsync journal and directory before authority replacement.
4. Write private O_EXCL/O_NOFOLLOW temporary state; flush/fsync.
5. Revalidate path/inode; atomically replace via held directory descriptor.
6. Fsync directory; release lock. Temporary artifacts are cleaned on ordinary errors.

An abandoned journal entry is NOT authoritative state and cannot trigger automatic
replay. Pre-rename failure leaves the previous state intact. Post-rename fsync
failure is reported as failure (durability unproved), not a successful admission.

## Admission behavior

`check_instagram_version_lock` reports `lock_state_error` and
`unsafe_for_automation` on missing proof/corruption/failed persistence.
`runner` refuses admission for such errors even when version comparison strict
mode is disabled. Existing version comparison and physical lock/unlock semantics
are otherwise preserved; no lock/unlock command was executed to test migration.

## Offline coverage

15 tests cover scope isolation, read-only legacy migration, sealed-release
immutability, stale writer CAS, concurrent lock contention, contradictory dual
state, corruption, missing scope, symlink/inside-release rejection, fsync/rename
failures, journal non-authority, new-release compatibility, binding mismatch,
shared permission/hardlink rejection and real admission helper failure paths.
All device observations and writes in integration cases are fakes/mocks.

`NEW_WRITES_TO_OLD_LOCATION = NO`
`AMBIGUOUS_DUAL_STATE = FAIL_CLOSED`
`PRODUCTION_MIGRATION_PERFORMED = NO`
