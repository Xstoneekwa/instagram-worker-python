# Target Availability flags and kill switch

All flags default false:

- `target_availability_observation_capture_enabled` / `TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED`;
- `target_availability_writer_enabled` / `TARGET_AVAILABILITY_WRITER_ENABLED`;
- `target_availability_shadow_enabled` / `TARGET_AVAILABILITY_SHADOW_ENABLED`;
- `target_availability_policy_shadow_enabled` / `TARGET_AVAILABILITY_POLICY_SHADOW_ENABLED`.

Only the boolean `true` or the case-insensitive string `"true"` enables a flag. Values such as `1`, `yes`, `on`, wildcards and malformed values are OFF.

`TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST` is mandatory for capture. It accepts a comma-separated list, a JSON array or an in-process sequence of UUIDs. Every entry must be a valid UUID; one invalid entry makes the whole allowlist empty and therefore OFF. An absent or empty allowlist never means global enablement.

`TARGET_AVAILABILITY_KILL_SWITCH=true` wins over every flag. For an immediate pilot stop without redeploy or Worker restart, `TARGET_AVAILABILITY_KILL_SWITCH_FILE` may point to an operator-owned path: flags are resolved at every target hook and the presence of that path disables capture on the next hook. A configured path that cannot be inspected also fails closed. An absent file leaves the explicit flags/allowlist contract in control.

In V2-1 no runtime environment is changed, no path is configured and no flag is enabled. A future operator runbook must recertify the effective runtime environment before and after any toggle.
