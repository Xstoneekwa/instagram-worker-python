# Rollback

Official rollback target:

- release: `/Users/admin/phonefarm-worker-releases/ff99db6-follow-persistence-rpc-v1`
- SHA: `ff99db6d7de48d75ede439c704e770feaaec6b7c`

The verifier only prints the operator plan:

```bash
python3 scripts/verify_follow_85s_golden.py rollback-plan
```

Before any real switch, require an empty queue, no active request/run, no
business subprocess, and no active device lock or UI lease. Record the current
symlink target, switch atomically, restart only the dispatcher if its working
directory still points to the previous release, then verify one dispatcher and
an idle runtime. This document does not authorize or execute a rollback.
