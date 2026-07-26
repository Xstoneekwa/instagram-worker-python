# Package runtime contract V1 — 2026-07-26

## Scope and lineage

Parent Worker runtime: `ada6af15f4cd3506cdd48e9e79aa3a28d4c31ec6`.
This checkpoint adds no business action and changes no Follow navigation,
Unfollow budget, Auto Restart policy, schedule or Identity Guard behavior.

## Runtime contract

The request consumer calls the service-role
`account_package_runtime_contract_status` RPC after account authorization and
before request start, device locking or subprocess creation. Missing RPC/data
or any non-ready contract fails closed with the stable backend reason. The
database remains the business source; Worker environment values remain only
kill switches or final hard caps.

Follow source rotation is strict in Supabase production mode: a missing or
invalid row raises `package_settings_incomplete`. The former 30/4 recovery path
remains explicit only for non-Supabase/local operational contexts.

The effective Follow formula remains:

`day = min(configured day, package day, warmup day, ops hard day)`

`session = min(configured session, package session, warmup day, ops hard session, remaining effective day quota)`

Persistent account caps are never replaced by warmup caps.

## Auto Login mismatch classification

The historical Auto Login adapter retains strict package equality. Known
transient Android system overlays (Credential Manager, Google Play Services and
Samsung Pass) receive one bounded Back/recheck cycle. An unknown package or a
persistent overlay still produces `login_package_mismatch`, safe-stop and the
existing incident path. Identity Guard remains unchanged.

## Validation and rollout

All tests are device-free. Release activation requires zero active requests,
runs and device locks, an immutable release directory, one atomic current-link
change and one dispatcher restart. No request, run, ADB command or phone action
is authorized by this checkpoint.

