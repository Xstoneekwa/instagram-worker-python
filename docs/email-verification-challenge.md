# Email Verification Challenge Flow

Production flow for Instagram post-password email verification and generic unsupported login challenges.

## Detection

- `email_code_challenge` maps to `verification_pending` / `email_verification_code_required`.
- Other post-submit challenges map to `unsupported_post_submit_challenge` with dashboard action `review_login_challenge`.
- No password resubmit, no auto-click on `Get a new code` / `Try another way`.

## Dashboard Actions

- `enter_email_verification_code`: client/admin popup to submit the code.
- `review_login_challenge`: human review only, no automatic code field.

Worker publishes actions through `login_dashboard_action_publisher.py` (fail-open).

## Code Transport

- Dashboard/API stores the code in Vault via `submit_account_verification_code`.
- Worker prod resume uses `--resume-email-code-from-action` and consumes the secret once.
- Operator terrain path uses `--resume-email-code-stdin` only.

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
