# Target Availability flags and kill switch

All flags default false:

- `target_availability_observation_capture_enabled` / `TARGET_AVAILABILITY_OBSERVATION_CAPTURE_ENABLED`;
- `target_availability_writer_enabled` / `TARGET_AVAILABILITY_WRITER_ENABLED`;
- `target_availability_shadow_enabled` / `TARGET_AVAILABILITY_SHADOW_ENABLED`;
- `target_availability_policy_shadow_enabled` / `TARGET_AVAILABILITY_POLICY_SHADOW_ENABLED`.

`TARGET_AVAILABILITY_ACCOUNT_ALLOWLIST` scopes a future pilot. `TARGET_AVAILABILITY_KILL_SWITCH=true` wins over every flag. For an immediate pilot stop without redeploy or Worker restart, `TARGET_AVAILABILITY_KILL_SWITCH_FILE` may point to an operator-owned file: flags are resolved at every target hook and the presence of that file disables capture on the next hook. In V2-1 no runtime environment is changed, no path is configured and no flag is enabled. A future operator runbook must recertify the effective runtime environment before and after any toggle.
