# Target Availability observation contract — dormant V2-1

## Boundary

`target_availability_observation.py` remains a pure contract module with no navigation, device, Supabase or runtime import. V2-1 imports the separate runtime adapter only from `account_session_orchestrator.py`; all four flags default OFF.

The pure contract performs no navigation, logging, HTTP, Supabase write, lifecycle decision, rename, archive, replacement, email, notification or activation. `evidence_safe` rejects secret/token/password/screenshot/provider-payload keys and is capped at 4 KiB.

## Payload

The immutable payload is versioned as `target-availability-observation-v1` and carries:

- tenant/account/target/username scope;
- requested, searched, observed and normalized usernames;
- optional stable platform user ID, explicit identity source and confidence;
- run/request/device/instance/Instagram-version/Worker-release correlation;
- explicit observation stage;
- lookup and profile-found result;
- verified badge observation;
- Followers surface and accessible-profile count;
- terminal end and repeated-first-profile signals;
- retry, timeout and recovery results;
- lookup start/completion, profile ambiguity and Followers entry result;
- pagination stall, recovery attempt, UI/network/session ambiguity, source mismatch and identity conflict;
- stable reason codes and redacted `evidence_safe`.

The idempotency key is SHA-256 over schema version, tenant, account, target, run and caller-provided event key. Re-emitting the same runtime event produces the same key. Another run, account, target or event produces a different key.

## Proposed budgets

Defaults are 2.5 seconds lookup, 4 seconds Followers entry, one retry, 8 seconds navigation and 10 seconds total Availability. They are model-only and do not modify the active Worker configuration.

## Stable identity

The Instagram UI path does not currently expose a reliable stable numeric ID. The Worker accepts one only when a future certified source provides it. Username similarity is never identity proof and cannot authorize a rename.

## Verified restriction

The contract records `verified_badge` and `followers_surface` independently. It never turns either observation into a lifecycle outcome. Cross-run aggregation and confidence belong to the Backend pure model.

## Deployment boundary

V2-1 locally maps only facts already returned by the existing Followers path and adds a bounded non-blocking writer adapter. Both capture and writer flags must be enabled for a pilot account before a thread or network call can start. No production release or flag was changed in Gate 1.
