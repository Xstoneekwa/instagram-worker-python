# Follow60 V2 redacted reference evidence

This directory contains mechanically selected JSONL excerpts for seven Rex
reference runs, including the canonical final run. The archive script removes
credentials, XML/hierarchy payloads, screenshots and user-facing identities.
`SHA256SUMS.json` binds every redacted artifact. Originals remain untouched in
the runtime log store and are not part of Git.

Rebuild with `scripts/archive-follow60-v2-reference-runs.py` and explicit
`--run-id` values. Never broaden selection to the full dispatcher log.
