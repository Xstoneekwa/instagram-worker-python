"""Minimal config for the experimental uiautomator2 worker."""

# Target account to open (exact match under Accounts tab).
TARGET_USERNAME = "xstonekwa"

INSTAGRAM_PACKAGE = "com.instagram.android"

# None = use default device from adb / uiautomator2
DEVICE_SERIAL = None

# ADBKeyboard (optional). Low-latency ADB_INPUT_TEXT broadcast when installed.
FAST_IME = "com.android.adbkeyboard/.AdbIME"

# Navigation / polling (seconds)
APP_START_WAIT_S = 2.5
# Short pause when reusing warm Instagram session (no force-stop)
WARM_SESSION_MICRO_WAIT_S = 0.12
SEARCH_FIELD_WAIT_S = 5.0
# Robust clear: max DELETE keyevents (batched) after clear_text/set_text("") fail
SEARCH_FIELD_CLEAR_MAX_DEL_EVENTS = 120
SEARCH_FIELD_CLEAR_DEL_BATCH_SIZE = 12
ACCOUNTS_RESULT_WAIT_S = 6.0
# Mixed-results row poll cap (legacy full find_real path)
MIXED_RESULTS_ROW_DETECT_MAX_S = 2.5
# FastIME + mixed_results: hot row poll (resource-id only, no ranking)
MIXED_HOT_ROW_DETECT_MAX_S = 4.0
HOT_ROW_POLL_S = 0.12
# After ADBKeyboard broadcast, max settle before fused row wait (seconds)
FAST_IME_POST_BROADCAST_SETTLE_S = 0.15
# Legacy full-header poll (unused by lightweight verify; kept for compatibility)
PROFILE_VERIFY_WAIT_S = 8.0
# After row tap: let profile chrome paint before checks (seconds)
PROFILE_POST_TAP_STABILIZE_S = 0.12
# Short fixed settle after d.click before transition poll (max ~150ms)
POST_TAP_SETTLE_S = 0.12
# Max time to wait for first profile chrome signal after tap (seconds)
PROFILE_TRANSITION_POLL_MAX_S = 4.0
# Sleep between transition polls (seconds)
PROFILE_TRANSITION_POLL_SLEEP_MIN_S = 0.08
PROFILE_TRANSITION_POLL_SLEEP_MAX_S = 0.12
# Lightweight multi-signal profile verify (seconds)
PROFILE_VERIFY_LIGHTWEIGHT_MAX_S = 1.5
PROFILE_VERIFY_POLL_S = 0.08
# Reuse search surface if recent successful open+type (same process)
SEARCH_SURFACE_CACHE_TTL_S = 600.0
# Reported when open_search navigation is skipped (observability; tune to your devices)
SEARCH_OPEN_SKIP_CREDIT_MS = 2800.0
# After back from profile, wait for search UI (seconds)
BACK_TO_SEARCH_MAX_WAIT_S = 3.0

# Early-exit UI polling (seconds)
OPEN_SEARCH_MAX_WAIT_S = 3.0
# Max settle after tapping search nav (seconds)
OPEN_SEARCH_SETTLE_S = 0.12
TYPE_SEARCH_CONFIRM_S = 2.0
# Cap for typing confirmation loop (field text OR row_search_user_username)
TYPING_CONFIRM_MAX_S = 3.0
UI_FAST_POLL_S = 0.06
# Second pass: after tapping top search bar fallback (seconds)
SEARCH_EDITTEXT_RETRY_AFTER_TAP_S = 5.0

# Fast poll interval (seconds) — balance Appium/uiautomator2 load vs latency
POLL_INTERVAL_S = 0.08

# If True, allow tapping a text-only row (e.g. recent query) when no avatar row matches.
ALLOW_TEXT_ONLY_RECENT_RESULT = False

# Layout reference: username TextView left edge on account rows (1080px-wide phone), scaled by screen width.
REFERENCE_SCREEN_WIDTH = 1080
ACCOUNT_ROW_USERNAME_LEFT_X_MIN = 60
ACCOUNT_ROW_USERNAME_LEFT_X_MAX = 520
# No-avatar rows with top above search_bottom + this are treated as top suggestion (px @ 1080 ref, scaled).
SUGGESTION_ZONE_PADDING_PX = 120
# Legacy small pad (used where suggestion zone not applicable)
TOO_CLOSE_SEARCH_TOP_PADDING_PX = 40
# Max time to find Accounts chip before mixed-results mode (seconds)
ACCOUNTS_TAB_TRY_MAX_S = 1.5
# Skip screenshot/XML on Accounts tab miss (faster)
ACCOUNTS_TAB_DEBUG_DUMP = False
# Max horizontal gap (px @ 1080) between avatar right edge and username left; avatar must overlap row ≥30% vertically.
AVATAR_TO_USERNAME_MAX_GAP_PX = 220
# Pixels (at 1080 ref) below “Recent” header treated as recent-query block
RECENT_BLOCK_DEPTH = 320
