# Target Availability fail-open writer

`target_availability_writer.py` owns only `ct_target_availability_observations`.

- bounded queue: 256 default, 2,000 hard maximum;
- batch: 20 default, 100 hard maximum;
- timeout: 1.5 seconds default, 3 seconds hard maximum;
- retry: at most one;
- duplicate suppression in memory plus database idempotency;
- three failures open a 30-second circuit by default;
- counters: accepted, duplicates, flushed, failures, dropped, circuit open.

Enqueue is fail-open and nonblocking. A daemon flush thread is created only when capture and writer flags are both enabled. PostgREST uses the service-role key and `resolution=ignore-duplicates`; no other table is writable by this adapter.

Direct synchronous writes were rejected because they block actions. A sidecar was rejected for rollout complexity. A disk outbox was rejected for replay and lifecycle risk. The bounded in-memory buffer is the smallest dormant design.
