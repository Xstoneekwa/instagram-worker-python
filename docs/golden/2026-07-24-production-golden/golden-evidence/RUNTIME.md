# Runtime evidence

Read-only status captured on `2026-07-24`:

| Check | Result |
|---|---|
| active symlink target | expected consolidated release |
| release HEAD | `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f` |
| release checkout | clean |
| dispatcher process count | `1` |
| heartbeat publisher process count | `1` |
| runtime root | correct |
| active requests / runs / locks | `0 / 0 / 0` |

Heartbeat showed one registered phone online and one registered phone in
`ADB unauthorized` state at the observation time. This is preserved as a
known operational limitation, not hidden by the Golden label.

No phone, ADB command, Instagram package, launchd service, symlink or runtime
process was modified by this task.
