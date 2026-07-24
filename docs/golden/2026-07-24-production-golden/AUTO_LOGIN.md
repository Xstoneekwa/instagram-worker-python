# Auto Login — Golden contract

## Origin and reason for restoration

The accepted historical engine originates from commit
`07ee49bba28c316e61b4fb377fe406bbd742ed2a`, reference tree
`3a9374912a01ebdfb705253cde11354afdc3c07e`. Thirty-four source files and ten
historical tests were imported as exact Git blobs into
`historical_auto_login_07ee/`.

The historical path was restored because it was the last accepted engine with
the required login form behavior. It is isolated so later Worker runtime,
dispatcher, binding and terminalization improvements can surround it without
silently rewriting its screen router.

## Isolation and adapter

`account_run_request_consumer.py` classifies `login_provisioning` and launches
`historical_auto_login_07ee_adapter.py`. The adapter is mechanical: it validates
and forwards these required values to the historical CLI:

- account ID;
- request ID and run ID;
- expected username;
- device serial;
- exact package name;
- expected app-instance ID;
- publish/json switches.

The engine root and CLI are fixed paths inside the immutable release. Runtime
does not import a mutable historical checkout.

## State machine

The router classifies login/start surfaces, credential form, password form,
email-code challenge, identity surfaces, connected home, post-login prompts and
terminal failures. Each transition is bounded and publishes a stable reason.

High-level path:

```text
binding preflight
→ launch exact package
→ verify foreground package
→ classify active screen
→ route login/challenge state
→ prove expected identity
→ connected_home
→ bounded post-login stabilization/reprobe
→ publish connected or terminal failure
```

Suggested accounts are navigation options, not active identity proof.

## Clone, package and app-instance binding

The dispatcher resolves the canonical assignment before any device action. The
Worker must launch the exact `package_name`, verify the same package is in the
foreground and carry the expected `app_instance_id`/binding version through the
run. A clone-bound request cannot fall back to the primary package.

Certified production example:

- primary: clone 0, `com.instagram.android`;
- assigned new account: clone 2, `com.instagram.androif`.

Package spelling is opaque data. Never normalize or derive it from an assumed
AppCloner suffix scheme.

## Identity guard

The final guard uses the active canonical account identity. Suggested usernames,
followers cards and discovery rows are ignored. A mismatch is a safe-stop and
incident condition; it is never repaired by clicking a suggestion or logging out
another account.

## Credentials and challenges

Credentials are obtained through privileged runtime boundaries and are never
logged or placed in dashboard/BotApp renderer payloads. Password and email-code
flows have separate executors. A challenge that requires human action
terminalizes with a precise reason and waits for explicit intervention.

No credential value, Vault identifier, email code or browser session belongs in
Golden evidence.

## Logout contract

Logout is not a generic recovery action. It is permitted only when the
historical router has confirmed the exact wrong active identity and the
account-specific policy authorizes it. The Golden integration did not change
logout selectors or policy.

## Post-login popup stabilization

The historical engine already contains handlers for Samsung Pass and Instagram
save-login-info prompts, including variants such as:

- `Save your login info?` / `Save login info`;
- `Not now`;
- localized `Enregistrer vos informations de connexion` and
  `Pas maintenant`/`Plus tard` equivalents.

The certified overlay changes reachability, not handler behavior:

```text
first connected_home
→ short bounded observation window
→ reprobe
→ existing popup handler if needed
→ reprobe
→ stable connected_home
→ terminalize connected
```

No new selector, credential retry or login retry was added.

## Backend publication and evidence

The engine publishes the canonical Instagram account status after package and
identity verification. The final successful clone-bound evidence reviewed for
this checkpoint used request `460801f9-77cd-4d21-836d-4b91b1f9844a` and linked
run `e1f2fa07-8d49-42dc-8e5e-935cb1a54a29`. The dispatcher log records binding
resolution, exact package launch, status publication, foreground-package proof
and lock release. Account/credential values are intentionally absent here.

## Tests and certification boundary

- embedded historical README: 426 tests, 423 passing, three intrinsic 07ee
  anomalies retained as historical evidence;
- consolidated Golden Auto Login/adapter regression selection: `499/499`;
- clone routing and final identity were physically validated;
- the late popup was physically observed before the stabilization patch, but no
  new post-patch physical replay was authorized.

## Locked non-regression rules

- do not edit `historical_auto_login_07ee/` without a new explicit decision;
- preserve adapter routing for `login_provisioning`;
- preserve exact package/app-instance binding and foreground proof;
- do not use suggestions as identity;
- do not add automatic retries or credential re-entry;
- do not change logout, challenges or popup selectors in navigation work.
