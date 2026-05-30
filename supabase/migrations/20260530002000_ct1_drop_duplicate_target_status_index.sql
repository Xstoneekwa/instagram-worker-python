-- CT-1 - Drop duplicate target account/status index created during staging validation.

drop index if exists public.ig_targets_account_status_idx;
