# SHA evidence

Captured read-only at `2026-07-24T15:36:55Z`.

| Item | Value |
|---|---|
| Worker business baseline | `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f` |
| Baseline parent | `abf0ebf90bcccd063355b41505d4d6541e870047` |
| Baseline subject | `fix(worker): consolidate navigation patch on auto login baseline` |
| Release checkout HEAD | `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f` |
| Release checkout status | clean |

The annotated Golden tag targets the docs-only checkpoint commit, whose direct
parent is the Worker business baseline above. This preserves both facts:

1. the runtime code certified by the checkpoint is exactly `e7f54a9…`;
2. the complete Golden documentation is immutable and reachable from the tag.

No Worker source file is changed by the documentation commit.
