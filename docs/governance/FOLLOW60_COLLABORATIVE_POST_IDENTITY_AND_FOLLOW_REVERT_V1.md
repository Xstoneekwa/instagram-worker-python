# Follow60 collaborative identity and Follow-revert restriction V1

This scoped change preserves exact candidate identity on Instagram collaborative
post headers without accepting arbitrary substring matches. A candidate is
accepted only as an exact standalone owner token inside a strict collaborative
owner label.

A preventive restriction is eligible only after three consecutive, distinct
candidates each satisfy the same fail-closed evidence contract: exact candidate
profile, physical Follow tap, fresh post-tap state still `Follow`, verification
failure code 34, no Follow persistence, no navigation drift, and exact recovery
of the originating Followers surface.

The third eligible outcome stops the session only after recovery and reuses the
canonical incident-only 48-hour restriction path. Successful Likes remain
independent business truth and are persisted before another candidate is
allowed. Follow counters, backlog receipts, CT Resume, Unfollow, profiles/live,
commercial policy, and Notification Router V2 semantics are unchanged.
