# Login Verification Challenge Flow

Production contract for Instagram verification after password submission. The
filename is retained for compatibility; the runtime is generic and supports
email, SMS, WhatsApp and authenticator-app challenges.

The canonical product flow is documented Backend-side in
`docs/client-connect-challenge.md`. This document defines the Worker boundary.

## Detection and channel identity

The Worker classifies the visible Instagram challenge before accepting any
resume payload:

- email -> `verification_channel=email`;
- SMS -> `verification_channel=sms`;
- WhatsApp -> `verification_channel=whatsapp`;
- authentication application -> `verification_channel=authenticator_app`.

`enter_email_verification_code` remains a historical action identifier only.
It must never force the channel to email. The channel published by the initial
provisioning is preserved through action submission, resume-plan creation,
dispatcher handoff and the Worker executor.

An old action without a channel may adopt only a channel proved by the current
Instagram UI. A mismatch between the persisted channel and the current UI is a
safe terminal mismatch: no secret read, no code entry and no alternate-channel
guess.

No path auto-clicks **Get a new code** or **Try another way**.

## Dashboard actions and resume contract

The Worker publishes challenge actions through
`login_dashboard_action_publisher.py` without including secrets.

Supported submission flow:

```text
human code submission
-> Vault write-only storage
-> canonical action transition to code_submitted
-> idempotent resume of the existing login_provisioning
-> dispatcher
-> assigned device and app instance
-> channel verification
-> one bounded code entry
```

The production resume consumes the secret exactly once from the canonical
action. The stdin resume path remains operator-only and is not a client product
path.

The resume must preserve the originating account, assignment, device,
app-instance, provisioning lineage and verification channel. It is not an
`account_session`, does not run Growth actions and cannot migrate to another
clone.

## Post-code Instagram setup surface

Instagram may accept the code and display:

```text
Set up on new device
To use Location services, allow Instagram to access your location
```

This screen is a supported post-code transition, not a new verification
challenge and not a connected proof by itself.

The Worker:

1. matches the Instagram package/activity and the setup/location copy;
2. sends exactly one Android Back action;
3. never taps **Continue** and never grants location permission;
4. waits for a supported Instagram surface;
5. resumes the exact-identity verification.

The guard exists in both the current orchestrator and the historical 07ee
adapter used by the dispatched provisioning engine. Unknown setup screens stay
fail-closed.

Reference commits:

- `2382113` — post-code dismissal;
- `ce79c15` — dismissal before a connected exit;
- `bed6e63` — parity in the dispatched engine.

## Exact identity and canonical publication

Opening Instagram successfully is not enough. Before publishing connected, the
Worker requires:

- `profile_opened=true`;
- the expected Instagram username and detected active username to match after
  canonical normalization;
- `expected_identity_verified=true`;
- `identity_verification_status=verified`;
- a safe `run_id` provenance.

The `already_connected_expected` route must publish this proof. It may not
return a local success with `should_publish_status=false`.

The publisher writes the canonical transition:

```text
client_instagram_accounts.login_status       = connected
client_instagram_accounts.provisioning_status = ready
client_instagram_accounts.onboarding_status   = ready
```

It also writes the identity proof, clears `reauth_required` through the
canonical RPC and lets the Backend synchronize runtime/dashboard projections.
`ig_accounts.status` is legacy lifecycle data and is never mutated to simulate
login success.

If the publisher rejects the metadata or the RPC fails, the Worker does not
claim a globally connected/ready result.

The generic publication fix is commit
`9859d66097e5c51463a17a08bb006912a23dd723`.

## Client restart and retry semantics

The Worker accepts only canonical resume requests. Client buttons decide
whether a missing resume must be recreated; they cannot directly invoke ADB.

- **Actualiser** can cause the Backend to recreate one missing bounded resume
  after a submitted code.
- **Vérifier et connecter** can continue an existing provisioning instead of
  starting a competing login.
- an already active request is reused;
- no manual/full Growth run is created by these paths.

## No-leak rules

Never log, export or persist in safe metadata/results:

- verification code;
- password, secret reference or Vault UUID;
- raw XML or screenshots;
- tokens, device serials or private challenge destination values.

The documentation itself must never contain a real verification code.

## Production baseline — 11 August 2026

- Worker SHA: `9859d66097e5c51463a17a08bb006912a23dd723`;
- immutable release:
  `/Users/admin/phonefarm-worker-releases/9859d66-login-ready-publish-v1`;
- dispatcher certified unique from this release;
- Backend SHA: `05dd12e29e174415f548d761e8f1fba4ff215db1`;
- terrain proof: `nab_youss`, natural provisioning run
  `f38924b5-2b54-4860-b1a4-7cad0175286d`, request
  `6f53889d-af3e-459f-b5e3-9fdddf8a3384`;
- final canonical state: seven accounts out of seven
  `connected / ready / ready`;
- final runtime gate: zero active requests, runs and device locks.

The terrain reconciliation reused the natural run's exact identity proof. It
did not replay a code, create a run, trigger a manual tick or perform an
operator ADB action.

## Validation

Focused Worker suites cover:

- all four channel classifiers and mismatch handling;
- resume handoff channel preservation;
- post-code location setup dismissal;
- exact active-profile publication in current and historical orchestrators;
- publisher metadata and CLI adapter behavior;
- no secret leakage.

Canonical commands:

```bash
/Library/Developer/CommandLineTools/usr/bin/python3 -m unittest \
  tests.test_login_verification_channels \
  tests.test_instagram_login_provisioner_orchestrator \
  tests.test_instagram_account_status_publisher \
  tests.test_instagram_login_provisioner_cli \
  tests.test_historical_auto_login_07ee_adapter

(cd historical_auto_login_07ee && \
  /Library/Developer/CommandLineTools/usr/bin/python3 -m unittest \
    tests.test_instagram_login_provisioner_orchestrator \
    tests.test_instagram_account_status_publisher \
    tests.test_instagram_login_provisioner_cli)
```

## Final invariants

```text
VERIFICATION_CHANNEL_IS_NEVER_SILENTLY_REPLACED=YES
POST_CODE_LOCATION_PERMISSION_IS_NEVER_ACCEPTED=YES
CONNECTED_REQUIRES_EXACT_IDENTITY_PROOF=YES
LOCAL_SUCCESS_WITHOUT_CANONICAL_PUBLICATION_IS_FORBIDDEN=YES
CURRENT_AND_HISTORICAL_DISPATCHED_ENGINES_SHARE_THE_CONTRACT=YES
CLIENT_ADMIN_AND_BOTAPP_READ_THE_CANONICAL_DB_PROJECTION=YES
```
