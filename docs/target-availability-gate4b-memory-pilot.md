# Target Availability Gate 4B — bounded memory pilot

## Boundary

Gate 4B observes the two existing CT rotation hooks without adding an Instagram
gesture, navigation, retry, timeout, thread, queue, network call or database
write. It is valid only when capture is allowlisted to one account and the
writer, Live Shadow and Policy Shadow are all OFF.

The production baseline for this candidate is Worker
`9bc958b2f8c7d46306aebf030aeb9dff4d0af175`. Its Golden Flow and Unfollow
successor changes remain inherited unchanged.

## Probe contract

`TargetAvailabilityMemoryProbe`:

- validates the observation's local JSON serialization;
- retains no observation and has payload retention capacity zero;
- keeps one aggregate summary for the current run and one for the immediately
  previous run;
- resets the current summary when the run/account boundary changes;
- hashes the run identifier and never exposes usernames, target identifiers,
  stable Instagram identifiers, evidence or payloads;
- writes a maximum 4 KiB JSON operator snapshot atomically with mode `0600`;
- fails open for the historical run if instrumentation fails;
- has no thread, queue, logger, HTTP, Supabase or device dependency.

The snapshot is
`/private/tmp/phonefarm-target-availability-gate4b-status.json`. It reports:

- capture attempts, created/valid/rejected/error observations;
- retained payload count (structurally zero);
- cumulative and maximum hook duration;
- aggregate memory bytes and snapshot bytes;
- last hashed run ID, account UUID, stage, technical reason and error code.

The final atomic status-file flush is operator instrumentation. It is not a
Target Availability writer and never contains a business observation.

## Exact pilot configuration

The dispatcher configuration is limited to:

```text
TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED=true
TARGET_AVAILABILITY_WRITER_ENABLED=false
TARGET_AVAILABILITY_SHADOW_ENABLED=false
TARGET_AVAILABILITY_POLICY_SHADOW_ENABLED=false
TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST=<one UUID>
TARGET_AVAILABILITY_KILL_SWITCH_FILE=/Users/admin/phonefarm-runtime/control/target-availability-kill-switch
TARGET_AVAILABILITY_MEMORY_PROBE_ENABLED=true
```

The default status path is compiled into the probe, so no extra runtime entry
is required. Writer credentials and transport configuration must remain absent.

The existing kill-switch contract is presence-based and read at every hook:

- file present: capture OFF;
- file absent: the strict capture flag plus UUID allowlist decide;
- inspection error: capture OFF (fail closed).

## Operator surface

Create the explicit private zero-state snapshot before arming the pilot. This
does not create an observation and does not contact a device, network or
database:

```text
/usr/bin/python3 -m target_availability_memory_probe initialize
```

Read the bounded snapshot:

```text
/usr/bin/python3 -m target_availability_memory_probe status
```

Reset/remove it after Gate 4B:

```text
/usr/bin/python3 -m target_availability_memory_probe reset
```

If a running process previously wrote the snapshot, removal is also a reset
signal: its next captured hook resets both fixed summaries before writing.

## Rollback

Owner: Dieumerci Ekwa Ntende.

Immediate stop requires no restart: create the configured kill-switch file.
Then set capture OFF and clear the allowlist in the dispatcher configuration.
Before any run, a defective RC may be rolled back through the canonical runtime
controller to the immutable `9bc958b` release, followed by exactly one approved
dispatcher restart. No database rollback exists for Gate 4B.

## Certification gates

- flags OFF: no probe import from the orchestrator and no snapshot;
- capture ON without one valid allowlisted UUID: no probe;
- capture-only pilot: local serialization and fixed counters only;
- writer/Shadow/policy Shadow ON: probe refuses to activate;
- kill switch: capture changes on the next hook without restart;
- all code paths remain fail-open to `skip -> next target`;
- `payload_retained_count` and `payload_retention_capacity` remain zero;
- snapshot size stays at or below 4,096 bytes for arbitrarily many targets;
- full Worker suite and exact baseline comparison are mandatory before release.

Phones being deliberately disconnected is an operator state, not a failure and
does not authorize ADB, recovery, Auto Login or a manual run.
