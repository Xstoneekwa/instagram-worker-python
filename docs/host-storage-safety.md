# Host storage safety

Phone Farm treats local persistence capacity as a host-wide safety dependency.
The policy is generic: every package and account uses the same volume probe and
the same fail-closed boundaries.

## Runtime contract

- Healthy: new runs and irreversible actions are allowed.
- Warning: work remains allowed and the exact capacity is observable.
- Critical: no request is claimed and no new Follow, Mute or Like tap is sent.
- A supported logger I/O failure (`ENOSPC`, `EDQUOT`, `EIO`, `EROFS`, or a
  broken output pipe) degrades only the affected output channel. It never
  buffers logs in memory or recursively logs its own failure.
- Storage pressure remains latched until an uncached volume check proves that
  the filesystem is no longer critical. Natural dispatcher polling then
  resumes without a code patch or account-specific reconciliation.
- The first terminal reason is `host_storage_critical`; downstream I/O errors
  must not replace it with `worker_exit_nonzero`.

Default thresholds are 10 GiB or 5% free for warning and 3 GiB or 2% free for
critical. They are configurable through `PHONEFARM_STORAGE_*` environment
variables. Hot-path checks use one `disk_usage`/filesystem-stat operation and
never scan directories.

## Retention

Per-run logs are pruned only when a run log is initialized: at most 500 files,
2 GiB total, and 30 days. The dispatcher log rotates atomically at 256 MiB and
retains at most eight rotations for 14 days. Values are configurable. Active
releases, rollback releases, signed governance evidence, audit ledgers and
forensic assets are outside automatic retention.

Releases are classified separately as HOT, WARM, ARCHIVE, or
PROTECTED_ROLLBACK. This change never deletes a Worker release.
