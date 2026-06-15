# Golden Flow Invariants

These invariants protect the validated flow:

- No multi-like for the same candidate.
- Max 1 like per candidate.
- No like without post viewer proof.
- No next candidate before `post_follow_return_ct_success`.
- No CT success if `action_bar_title == candidate`.
- No CT success on `own_unified` alone.
- No CT success on `FOLLOWERS_LIST` alone.
- No CT success on `current_screen_guess=likely_profile` without explicit CT proof.
- `stale_candidate_action_bar_ignored_with_strong_list` is forbidden.
- Post-open must not add unnecessary slow probes; avoid `4f754ea`-style proof-building that is rejected and then falls back.
- Fallback strict path remains active if CT proof is absent, stale, ambiguous, or candidate-matching.
- Facebook/no-like is a future optimization topic; if safe-continue is possible it must not stop the run.
- No Posts Yet is a future optimization topic after this golden checkpoint.

## Required Future Modification Protocol

Any future patch touching a protected area must:

1. Stop and state which invariant might be affected.
2. Get explicit user GO.
3. Keep the patch minimal and local.
4. Add or update targeted tests.
5. Run `scripts/check_golden_flow_untouched.sh` or explicitly set `ALLOW_GOLDEN_FLOW_TOUCH=1` only after GO.
6. Compare new run metrics against the golden references.

## Tests Covering Invariants

Current targeted coverage includes:

- `tests.test_post_follow_like_samsung_fast`
  - accepts fresh explicit CT proof;
  - rejects candidate action bar;
  - rejects own_unified-only / FOLLOWERS_LIST-only / stale / ambiguous CT proof;
  - preserves fallback strict path;
  - protects like max/profile behavior and viewer proof paths.
- `tests.test_mute_engine_v2_sheet_levels`
  - protects mute sheet/dismiss proof reuse.
- `tests.test_follow_targets_runtime_p1b`
  - protects runtime candidate/return gates.
- `tests.test_runtime_caps`
  - protects runtime caps.

The string guard for `stale_candidate_action_bar_ignored_with_strong_list` is enforced by `scripts/check_golden_flow_untouched.sh`.
