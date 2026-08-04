# Follow60 Mainline rollback

Emergency rollback reference: Worker `ff99db6d7de48d75ede439c704e770feaaec6b7c`
at `/Users/admin/phonefarm-worker-releases/ff99db6-follow-persistence-rpc-v1`.
This is the measured Golden85 legacy baseline (85.226 s), not the default
engine.

`scripts/rollback-follow60-to-golden85.sh` is dry-run by default. An actual
rollback additionally requires:

1. an explicit operator GO;
2. zero active requests, runs and device/tick locks;
3. a prepared startup-skip token;
4. atomic `switch-release` to the exact immutable path;
5. exactly one canonical dispatcher restart;
6. post-switch root/SHA/process/gate verification.

The procedure never creates a run or tick and never performs ADB. DB history,
outbox receipts, completed-cycle ledgers and canary history are preserved.

