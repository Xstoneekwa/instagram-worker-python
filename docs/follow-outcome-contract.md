# Follow outcome contract

`follow_outcome_v1` is the canonical semantic result shared by the Followers
engine, account-session target rotation, terminal phase accounting, and the
passive Auto Restart planner.

The contract separates verified work from phase completion. A positive
`verified_actions` count never changes a local stop into global completion.

Required fields:

- `phase_status`: `completed`, `partial_resumable`,
  `partial_not_resumable`, `blocked_critical`, or `failed_internal`;
- `scope`: `current_ct`, `follow_phase`, or `account_session`;
- `stable_reason`, `verified_actions`, `target_actions`, `remaining_actions`;
- `current_target_id`, `remaining_target_ids`, `remaining_target_count`;
- `current_ct_status`: `completed`, `exhausted`, `locally_blocked`, `unsafe`,
  or `retryable`;
- `safe_boundary`, `safe_next_step`, `suggested_next_action`,
  `last_safe_checkpoint`, and `suggested_resume_strategy`.

For `visible_window_exhausted_scroll_failed`, the current CT is locally blocked
and the result stays `partial_resumable`. A missing scroll anchor alone is not a
critical account signal. Rotation is allowed only after either the existing
surface proof is safe or the bounded CT switcher validates global Search, the
exact next profile, and its Followers list. A failed proof does not consume the
next CT and ends the current session as `partial_not_resumable`.

Follow may hand off to Unfollow only when the canonical result is
`phase_status=completed`, `scope=follow_phase`, and
`safe_next_step=end_follow_phase`. Reaching the per-run CT limit with remaining
Follow quota produces a safe resumable checkpoint, not global completion.
