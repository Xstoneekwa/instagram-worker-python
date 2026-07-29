# Target Availability canonical tenant resolution

## Ownership contract

Target Availability keeps the domain field name `tenant_id`, but its canonical
value is currently the active `client_instagram_accounts.client_id` linked to
the exact Instagram `account_id`. Ownership, active commercial package, and an
optional commercial revision are separate concepts.

The Worker performs one bounded read when capture is eligible for the account:

```text
GET client_instagram_accounts
select=account_id,client_id,active,updated_at
account_id=eq.<account_id>
active=eq.true
limit=2
```

The request has a three-second timeout and zero retry, so a transient ownership
read cannot hold the Instagram session behind the historical client retry
budget. Failure remains local to Availability.

One row resolves ownership. Zero rows, two rows, a malformed row, an inactive
row, a timeout, or an invalid identifier fails Target Availability closed. The
Instagram run continues. There is no username, tenant-wide, package, or
commercial-revision fallback. The two-row bound exists to prove ambiguity
without loading an unbounded result.

If an optional commercial revision contains `client_id`, it must equal the
canonical ownership. A mismatch rejects Availability with
`target_availability_tenant_ownership_conflict`; neither value is selected.

Resolution occurs before CT rotation and its immutable result is reused by all
loaded/summary hooks. Hooks contain no ownership query and add no Instagram
navigation or network action. Flags OFF, kill switch ON, and non-allowlisted
accounts perform no ownership read.

## Future run payload contract (not deployed)

A future Backend version may stamp `account_run_requests.metadata_safe` with a
validated hint:

```json
{
  "target_availability_ownership": {
    "client_id": "uuid",
    "account_id": "uuid",
    "source": "client_instagram_accounts",
    "validated_at": "RFC3339 timestamp",
    "contract_version": "target-availability-ownership-v1"
  }
}
```

This is a hint only. The Worker must revalidate the active exact ownership once
at run start so queued metadata cannot become stale. No Backend or payload
change is part of Gate 4B.

## Runtime boundaries

- `_scope()` remains strict and rejects an empty tenant.
- Writer, Live Shadow, Policy Shadow, and Replacement Shadow stay disabled.
- No migration, schema change, CT mutation, notification, or email is required.
- Ownership failures are observable reason codes but never abort the Instagram
  run or prevent moving to the next target.
