# BotApp manual stop → natural continuation: canonical lineage

Backend commit `24a2a6cde8f3000fd031a80734e5f897cd0c0ab9` established that
a canonical BotApp stop ends the current run, without permanently excluding
the account from natural ticks. It permits one fresh-boundary continuation,
including when the stopped source was already at attempt 3. Its tests used
FakeSupabase and asserted a new attempt 1 / retry 0 with an `operator-stop:`
business-session label.

Worker commit `4477be77532b0e4f5f3d3f7c10b3dfde7103977e` later added real SQL
lineage for ordinary failure retries. It required source attempt + 1 and a UUID
root, but did not distinguish that manual-stop contract. Both isolated suites
passed while their composition rejected enqueue with
`auto_restart_request_lineage_mismatch`. No removal of the original Backend
policy was necessary to cause the regression.

## Correction

The forward migration replaces only `apply_ordinary_auto_restart_lineage_v1`
and adds one partial unique index. The ordinary retry branch, existing V4
human authorization, restriction preflight and armed first-attempt branches
retain their contracts. All executable Worker and Backend source is unchanged.

For a manual-stop continuation the trigger requires the exact same-account
source request and run, a canceled request with a recorded
`botapp_manual_stop`, a stopped finished run, valid source SQL lineage and no
newer account-session request/run. Both metadata projections must agree on
the source, attempt, stop reason and fresh-boundary contract. Exact viewport
replay is rejected. A caller flag alone never authorizes retry-budget reset.

The new request owns a new UUID root at attempt 1 / retry 0. Its exact stopped
source/root/attempt remain recorded as ancestry. The top-level and embedded
metadata receive the canonical tuple before immutable-lineage defaults,
atomic admission, capsule creation and Worker claim. The existing terminal
store verifies that same tuple. Neither historical source rows nor quotas or
counters are rewritten. Opaque Unfollow Daily Plan checkpoints stay intact.

The existing Fleet enqueue idempotency contract handles identical repeats.
The partial unique index also prevents two canonical continuations from the
same stopped source under different keys. A later request/run supersedes the
old stop. Windows, quotas, account/incident eligibility, assignment and device
leases, security markers and the promotion fence remain mandatory. An actual
subsequent manual stop has its own source identity.

## Regression qualification

Run the isolated harness with the read-only Backend at
`e8419c560f3623a50cbdefdb9c3a9b56235725ea`:

```sh
python3 -B scripts/test-operator-stop-natural-tick-lineage-v1.py \
  --backend-root /path/to/exact/backend \
  --output /new/external/evidence/path
```

The harness replays the existing schema in disposable PG17/PostgREST. The
TypeScript fixture calls the actual unchanged Backend authorization policy
and payload serializer. It is not a full dispatcher tick invocation: the
candidate is synthetic and there are no phone actions. The original trigger
must reject that payload before the migration; the same payload must succeed
afterward through real enqueue, claim, admission and terminal persistence.

The suite checks stopped source attempts 1 and 3, Follow/Unfollow phase and
quota transport, checkpoint preservation, duplicate/concurrent calls,
different keys, stale and forged sources, invalid projections, physical
safety markers, fence rejection, immutable identity and rollback/forward SQL.
The catalog comparison allows exactly one modified public function.

`--lineage-only` runs the inherited real ordinary retry and V4 contract suite
with this migration installed. `--restriction-only` runs the inherited
resolved-restriction/readiness suite against the same composed schema. Each
invocation uses its own temporary cluster and removes it on exit. Local test
credentials are synthetic; production credentials are never used.

## Governed activation and rollback

Base Worker is `826edb7943bcd05abb9cfb67aaaab0273e9ded5e`. Approval 1 permits
only scoped local implementation/testing and a normal-hook frozen commit.
Approval 2 binds the exact resulting commit/tree/diff and evidence. No release
may be deployed from this checkout.

Promotion uses the standard V2 transaction and certified controller, exact
sealed release, fresh zero gate and deployment-lock proof, preserved parallel
registry fields, service convergence, token consumption and durable finish.
Apply the SQL only in the governed admission-closed window after confirming
the exact predecessor function digest. Never unpause admission on an unknown
mixed state. No forced natural tick or device run is part of qualification.

The active predecessor Worker uses identical runtime code and accepts the new
canonical tuple; this is exercised by the real-path suite. Keep the additive
SQL correction when rolling the Worker back to that exact base. Restoring the
old SQL deliberately restores the original rejection for new stop requests;
if required, do it only under the governed gate/fence after draining work,
using the captured exact definition. Historical created tuples remain valid
and immutable. Never reopen the older ordinary-retry terminalization bug.
