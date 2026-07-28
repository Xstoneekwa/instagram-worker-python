# Target Availability observation contract — dormant Phase 8B.1

## Boundary

`target_availability_observation.py` is a pure contract module. It is intentionally not imported by `runner.py`, `instagram_navigation.py`, `account_session_orchestrator.py`, `supabase_client.py` or the request consumer.

It performs no navigation, logging, HTTP, Supabase write, lifecycle decision, rename, archive, replacement, email, notification or activation.

## Payload

The immutable payload is versioned as `target-availability-observation-v1` and carries:

- tenant/account/target/username scope;
- optional stable platform user ID;
- run/device/version correlation;
- lookup and profile-found result;
- verified badge observation;
- Followers surface and accessible-profile count;
- terminal end and repeated-first-profile signals;
- retry, timeout and recovery results;
- UI, network and session ambiguity;
- stable reason codes and redacted `evidence_safe`.

The idempotency key is SHA-256 over schema version, tenant, account, target, run and caller-provided event key. Re-emitting the same runtime event produces the same key. Another run, account, target or event produces a different key.

## Proposed budgets

Defaults are 2.5 seconds lookup, 4 seconds Followers entry, one retry, 8 seconds navigation and 10 seconds total Availability. They are model-only and do not modify the active Worker configuration.

## Stable identity

The Instagram UI path does not currently expose a reliable stable numeric ID. The Worker accepts one only when a future certified source provides it. Username similarity is never identity proof and cannot authorize a rename.

## Verified restriction

The contract records `verified_badge` and `followers_surface` independently. It never turns either observation into a lifecycle outcome. Cross-run aggregation and confidence belong to the Backend pure model.

## Future integration gate

A dedicated Worker phase must map existing hybrid XML/vision/navigation-state evidence into this contract, add a non-blocking writer adapter and guarantee `skip -> next target`. Until that phase, this module remains test-only and cannot affect production.
