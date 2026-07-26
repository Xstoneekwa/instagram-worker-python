# Account protection lists runtime

Canonical source: `account_protection_list_entries`. Retired `ig_account_filters` list fields and `ig_interacted_users.whitelist_protected` are not runtime fallbacks.

## Run snapshot

For `account_session` and `outreach_session`, the dispatcher calls `get_account_protection_lists_for_run(account_id)` exactly once after account/package validation and before device access, lock acquisition, or subprocess creation. The response must contain the same account id, canonical source label, both arrays, both non-negative versions, and `loaded_at`.

The validated snapshot is serialized into `ACCOUNT_PROTECTION_LISTS_SNAPSHOT_JSON` with `ACCOUNT_PROTECTION_LISTS_REQUIRED=1`. Business modules use in-memory sets only. An active run never refreshes. A new request, restart, or resume is a new attempt and reloads once.

Missing, mismatched, unauthorized, unavailable, or generally malformed snapshots block the request as `interaction_blacklist_load_failed` before device access. A payload whose blacklist is valid but whose whitelist contract is malformed is reported as `unfollow_whitelist_load_failed`; V1 still blocks the request before device access because the two lists form one atomic run snapshot. This is stricter than allowing unrelated phases to continue and guarantees that Unfollow never assumes an empty whitelist. There is no per-candidate database query.

## Action gates

- Interaction blacklist before Follow and post-follow Like.
- Interaction blacklist before Welcome and Outreach job enqueue.
- Interaction blacklist after DM claim but before navigation, typing, or send; the job becomes terminal `skipped` with `interaction_blacklist`.
- Interaction blacklist is the mandatory guard for Comment and Story Watch when those action implementations become active. No active Comment/Story Watch sender exists in this release.
- Unfollow whitelist before both strict-plan and visible `unfollow-any` selection.
- Interaction blacklist never participates in Unfollow selection.
- Unfollow whitelist never blocks any other interaction.

An unresolved username safe-stops a protected action when the dispatcher marked enforcement required.

## Observability

Dispatcher events expose only source, load time, versions and counts. Action skip events expose action, normalized username and stable reason. Full lists are never logged. Run metadata must retain the starting versions/counts so incident review can identify the effective immutable snapshot.

## Deployment and rollback

Deploy as an immutable Worker release only after the backend RPC exists. Switch the active symlink atomically and restart the dispatcher once. Do not run ADB, start a session, or touch a phone for release verification.

Rollback switches the symlink to the previous immutable release and restarts the dispatcher once. Existing active subprocesses must not be replaced mid-run. If the canonical backend is unavailable, keep requests blocked; never restore a legacy fallback.
