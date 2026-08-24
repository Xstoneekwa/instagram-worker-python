# Wrong-password and recovery surface corpus V1

This source-only checkpoint records the generic Worker classification contract.
The supplied historical screenshots are reference material only: no historical
account, credential, phone, or Instagram session was accessed or mutated.

## Exact credential rejection

The Worker emits internal reason `instagram_wrong_password` only when the UI
contains an explicit credential-rejection statement such as `Incorrect
password` or `The password you entered is incorrect`. Persistence maps this to
the client-safe `instagram_credentials_rejected` reason and publishes the
blocking `update_instagram_password` action. It does not retry the rejected
credential and does not publish connected, ready, or scheduler-eligible state.

If a phone-call or other recovery option is visible together with an explicit
wrong-password statement, the credential rejection remains the root cause and
the secondary surface is retained only as safe diagnostic metadata.

## Distinct recovery/challenge families

The following surfaces are not classified as wrong password without the exact
credential-rejection evidence:

- security lock / `Choose a way to recover`;
- disconnected login information / `Recover your account`;
- account confirmation choices such as Email, Password, SMS, or WhatsApp;
- phone-call confirmation;
- ordinary Email, SMS, WhatsApp, or Authenticator challenges.

They remain bounded challenge/recovery outcomes and therefore cannot open the
password-correction UI falsely.

## Resume boundary

The existing opaque credential writer and Vault contract remain authoritative.
A corrected credential creates a new active revision and supersedes the old
one. A later Auto Login must be separately authorized; the Worker selects only
the active latest revision. Home, challenge completion, or credential submit is
not success. Connected state is allowed only after the common exact-account
post-auth handoff reaches own profile, verifies the normalized assigned
username, and persists Identity Guard success.

No account-specific logic is present. This applies to all current and future
accounts and does not start Auto Login, a run, a tick, ADB, or a device action.

