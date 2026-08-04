# Follow60 Mainline architecture

The normal dispatcher creates an account run request. The Worker binds one
Follow60 runtime to that exact account/request/run tuple. Physical stages write
idempotent outbox receipts; the service-role RPC projects the verified stages;
the completed-cycle RPC acknowledges only the full durable cycle. Normal runs
continue under canonical package, quota, stop and deadline policy. An explicit
canary control selects the historical canary branch and its evaluation barrier.

PostGrid, Post viewer V5 and Return CT remain inside the shared navigation state
machine. Mainline promotion adds no alternate gesture or relaxed proof.

