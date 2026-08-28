# Shared startup-tick skip consumption V1

## Proven boundary on base 8529aa3

`run_forever` sent the initial control-plane recovery tick before calling
`initialize_auto_restart_tick_state`. The nonzero recovery marker then
short-circuited `recovery_tick_monotonic or initialize_auto_restart_tick_state`,
leaving the shared pending marker unconsumed. This is a startup ordering defect,
not a scheduling, quota, queue or device defect.

## Contract

The canonical runtime controller supplies `RUN_CONTROL_DISPATCHER_RUN_DIR`
outside the sealed release. The dispatcher wrapper arms and exports exactly
`<shared run directory>/auto-restart-skip-startup-tick.once`. It refuses an
alternate token path, release-local directory or environment-file redirection.
Heartbeat and notifier neither arm nor consume this marker.

After the existing storage preflight, but before any startup probe can trigger
a tick, the consumer calls the single shared consumption helper. Its decision
is cached only in this startup's caller-owned state. No module-global cache
survives a subsequent restart. A consumed marker suppresses the initial
recovery tick and initializes the existing 60-second cadence when startup
finishes. Long preflight/recovery cannot age out that suppression. No marker
preserves normal startup. Subsequent natural cadence and runtime network
recovery behavior remain unchanged. No skip IO is added to the natural tick.

## Invalid and historical state

Only the exact existing V1 marker is accepted. Validation is bounded and
rejects symlinks, nonregular files, truncated/unknown marker content, alternate
paths and inode replacement during atomic claim. Invalid state or failed
claim cleanup blocks startup before probe, tick or claim (exit 5). It is not
treated as absence. Evidence is left for canonical operator recovery; this
patch does not resolve incidents or remove production markers.

V1 has no timestamp, expiry or generation payload. No age-based authority or
TTL is invented: an aged but valid *pending* marker is conservatively consumed
once. Renamed `.consumed.*` artifacts are never read as pending state. Thus a
previously consumed skip cannot postpone another restart; malformed/obsolete
formats fail closed rather than silently enabling a startup tick. A fresh
canonical arm replaces pending state through the existing atomic wrapper.

## Machine-enforced regression

`tests/test_startup_tick_skip_contract_v1.py` executes the actual `run_forever`
with all probes, ticks, queues, heartbeats and clock waits replaced by offline
test doubles. It proves initial suppression, subsequent natural cadence,
long startup, absence, consumed-marker restart, repeated initialization,
malformed state and cleanup failure. Structural dominance checks prevent
reintroducing the initial recovery-tick short circuit. The pure existing
wrapper arm function is exercised solely under a temporary directory; no
service entrypoint is executed. Runtime-controller path propagation,
heartbeat/notifier isolation and the restart/resume zero-gate ordering are
also asserted. These fixtures are included in the new protected manifest.

## Preservation and release policy

No A1 trust/status, Patch31, suspension UI freshness, external lock_state,
Resume State, S1/S2/S3, Follow/Unfollow, business admission or quota delta is
introduced. Existing regression receipts must be rerun for the final commit.
No active release, registry, Backend or BotApp is changed by this fix.
The current registry mismatch remains a separate promotion blocker. A new
manifest and new external final signature are mandatory; Approval 1 is not a
release signature. No activation is authorized here. Field certification of
startup remains pending a separately authorized canonical promotion.
