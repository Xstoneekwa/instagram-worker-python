# Verification Challenge and Post-Login Completion Flow

Production flow for Instagram post-password verification challenges (Email,
SMS, WhatsApp and Authenticator App), generic unsupported challenges and the
channel-neutral completion boundary after a code is accepted.

## Detection

- `email_code_challenge` maps to `verification_pending` / `email_verification_code_required`.
- Other post-submit challenges map to `unsupported_post_submit_challenge` with dashboard action `review_login_challenge`.
- No password resubmit, no auto-click on `Get a new code` / `Try another way`.

## Dashboard Actions

- The verification-code action records the detected channel; the code remains
  single-use and is submitted through the same secure resume contract.
- `review_login_challenge`: human review only, no automatic code field.

The client actions **Actualiser** and **Vérifier et connecter** use the same
authenticated retry endpoint. An active correlated login request is returned
idempotently; a terminal stale verification attempt is cleaned before a new
request. Neither action reads or resubmits the stored code, and neither action
branches on the verification channel.

Worker publishes actions through `login_dashboard_action_publisher.py` (fail-open).

## Code Transport

- Dashboard/API stores the code in Vault via `submit_account_verification_code`.
- Worker production resume consumes the secret once through the generic
  verification-code executor (the legacy CLI flag name is retained only for
  compatibility).
- Operator terrain path uses `--resume-email-code-stdin` only.

## Post-verification login completion

Code acceptance is never treated as connection or identity proof. Before the
Identity Guard, `instagram_post_verification_completion.py` observes a fresh
hierarchy and handles only known post-login setup surfaces: location/new-device
setup, notification onboarding/settings, save-login prompts, contact sync and
discover-people onboarding.

For these known surfaces the safe dismissal is bounded Android Back:

1. at most three recoveries;
2. a fresh hierarchy and changed fingerprint after every Back;
3. no second blind Back on an unchanged surface;
4. a stable Instagram bottom-navigation surface before Identity Guard;
5. exact own-profile username proof before publishing connected/ready.

An unknown or unchanged surface fails closed through the existing operator
assistance path. A transient empty `app_current()` result may be replaced only
by a fresh hierarchy proving the exact expected Instagram package plus its
bottom navigation and Profile tab; an explicit non-empty package mismatch is
never bypassed.

## Verification transition and navigation education

After a one-shot code submission, a still-visible non-expired code form uses the
remaining existing finite observations before it is considered still required.
Explicit invalid/expired challenges remain terminal; observation never resends
a code, reloads credentials or admits another RUN.

The reviewed `Swipe to easily access Reels and messages` education modal is
handled in the existing post-verification completion path, before any Home
surface underneath it can be admitted. Exact package, title and supporting body
are required. Its `Got it` action reuses the existing unique actionable-ancestor
resolver and live structural checks, with no coordinate fallback. An unchanged,
missing, disabled or ambiguous target fails closed; arbitrary `Got it` labels,
privacy choices and unknown screens gain no dismissal authority. The same
recovery and observation budgets apply. Own-profile identity proof remains
required after completion before publishing connected/readiness.

For a supervised existing session, a personally approved action covers only
that one exact gesture. Observe immediately afterwards and stop for owner
review before another popup or own-profile navigation. Source signatures do
not themselves authorize physical actions.

## No-Leak Rules

Never log or persist in metadata/results:

- verification code
- password / secret_ref / vault UUID
- raw XML / screenshots / tokens

## Validation

```bash
python3 -m py_compile instagram_login_*.py login_*.py
python3 -m unittest tests.test_instagram_login_ui_probe tests.test_instagram_login_password_form_executor tests.test_instagram_login_provisioner_orchestrator tests.test_instagram_login_email_code_executor tests.test_login_dashboard_action_publisher tests.test_login_challenge_runtime
```

Edge tests:

```bash
deno test supabase/functions/dashboard-actions/index.test.ts
```
