"""Minimal config for the experimental uiautomator2 worker."""

# Target account to open (exact match under Accounts tab).
# Controlled real-send test: prefer empty / new thread only (no prior DM history with this account).
TARGET_USERNAME = "xstonekwa_backup_acc"

INSTAGRAM_PACKAGE = "com.instagram.android"

# None = use default device from adb / uiautomator2
DEVICE_SERIAL = None

# ADBKeyboard (optional). Low-latency ADB_INPUT_TEXT broadcast when installed.
FAST_IME = "com.android.adbkeyboard/.AdbIME"

# Navigation / polling (seconds)
APP_START_WAIT_S = 2.5
# Short pause when reusing warm Instagram session (no force-stop)
WARM_SESSION_MICRO_WAIT_S = 0.08
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
# DM open (safe mode: detect only, never type/send)
DM_THREAD_DETECT_MAX_S = 2.5
DM_THREAD_POLL_S = 0.08
DM_THREAD_POST_OPEN_SETTLE_S = 0.45
DM_THREAD_STATE_MAX_WAIT_S = 2.0
DM_THREAD_STATE_POLL_S = 0.25
# Draft-only / fast DM: probe for thread history (bubbles, list, timestamps) before empty_new_thread
DM_EXISTING_THREAD_PROBE_S = 0.8
DM_EXISTING_THREAD_POLL_S = 0.1
DM_BACK_TO_PROFILE_MAX_WAIT_S = 3.0
DM_BACK_TO_PROFILE_POLL_S = 0.08
SAFE_DRAFT_MESSAGE = "Bonjour, je voulais vous contacter rapidement."
DM_DRAFT_TYPING_ENABLED = True
DM_CLEAR_DRAFT_AFTER_TEST = True
DM_VERIFY_TYPED_TEXT = True
# Real DM send — TEMP: controlled first live-send test (max 1/run, other guards unchanged).
# When False: draft, verify, precheck, log block, clear — never taps Send.
ENABLE_REAL_DM_SEND = True
SEND_DM_REQUIRE_APPROVED_TARGET = False
SEND_DM_MAX_PER_RUN = 1
SEND_DM_COOLDOWN_SECONDS = 30
SEND_DM_SKIP_EXISTING_THREAD = True
SEND_DM_SKIP_IF_PREVIOUS_DM_SENT = True
# Real send: wait for Send after draft (Instagram often reveals it only once text is present).
DM_SEND_BUTTON_WAIT_MAX_S = 1.5
DM_SEND_BUTTON_POLL_S = 0.1
# Unsafe last-resort tap to the right of composer; keep False unless debugging detection only.
ENABLE_COORDINATE_SEND_FALLBACK = False
# After real DM send: short bounded poll for a post-send UI signal before navigation.
DM_POST_SEND_SIGNAL_MAX_S = 2.0
DM_POST_SEND_SIGNAL_POLL_S = 0.12
CLOSE_APPS_AFTER_RUN = True
HOME_AFTER_RUN = True
FAST_PATH_MODE = True
FAST_SKIP_ACCOUNTS_TAB = True
FAST_DISABLE_ALREADY_ON_SEARCH_LONG_WAIT = True
FAST_DIRECT_OPEN_SEARCH_ON_NEXT_TARGET = True
FAST_BACK_TO_PROFILE_MAX_WAIT_S = 1.2
FAST_PROFILE_TO_SEARCH_MAX_WAIT_S = 1.2
FAST_PROFILE_TO_SEARCH_WAIT_BEFORE_FALLBACK_S = 0.6
FAST_SEARCH_CLEAR_MAX_S = 0.5
# FAST_PATH: legacy inner-clear wall (superseded by FAST_SEARCH_CLEAR_DECISION_CAP_S for type_search).
FAST_SEARCH_CLEAR_FAST_WALL_S = 0.5
# FAST_PATH: max wall for the clear *decision* phase (type_search); on cap, optional continue to typing.
FAST_SEARCH_CLEAR_DECISION_CAP_S = 0.4
FAST_SEARCH_CLEAR_CONTINUE_ON_CAP = True
# Skip heavy pre-focus snapshot; require EditText + Instagram foreground only.
FAST_SKIP_PRE_FOCUS_VALIDATION = True
# Defer result row/hint wait to tap_account / verify_profile (predictive + mixed loose).
FAST_SKIP_TYPE_SEARCH_RESULT_WAIT = True
# FAST_PATH: max duration (ms) for initial search EditText get_text during placeholder check; above → skip logic.
FAST_PLACEHOLDER_GETTEXT_MAX_MS = 300.0
# Before typing next target: strip previous_username from search field (wall budget, seconds).
FAST_SEARCH_PREVIOUS_USER_CLEAR_BUDGET_S = 0.6
FAST_SEARCH_EDITTEXT_WAIT_S = 1.2
FAST_SEARCH_EDITTEXT_AFTER_TAP_S = 1.5
FAST_SEARCH_EDITTEXT_POLL_S = 0.03
# Max wall time for focus poll when FAST_SEARCH_SKIP_FOCUS_PROBE is False (capped at 0.5s in code).
FAST_SEARCH_FOCUS_MAX_WAIT_S = 0.5
# Max ed.info() polls in fast focus loop when probe is disabled (avoids long stalls).
FAST_SEARCH_FOCUS_MAX_INFO_POLLS = 12
# When True: one click + short settle, no focused-bit polling (FastIME still used with FAST_IME_ON_FOCUS_TIMEOUT).
FAST_SEARCH_SKIP_FOCUS_PROBE = True
FAST_SEARCH_POST_CLICK_SETTLE_S = 0.05
# When True, still attempt FastIME after short focus-timeout (avoids set_text-only path that often breaks IG search).
FAST_IME_ON_FOCUS_TIMEOUT = True
# After FastIME broadcast into search field (cap 0.15s in fast path).
FAST_SEARCH_POST_TYPE_SETTLE_S = 0.15
# After typing: upper bound for row-hint poll in fast path (clamped by CAP below).
FAST_SEARCH_RESULT_WAIT_MAX_S = 0.8
# Hard ceiling for result-wait in FAST_PATH_MODE (row detect continues in tap / mixed loose).
FAST_SEARCH_RESULT_WAIT_CAP_S = 0.8
# Do not start ed.get_text() when remaining wall budget is lower (reduces overrun from blocking RPC).
FAST_SEARCH_RESULT_GET_TEXT_MIN_BUDGET_S = 0.04
# Predictive tap of first matching row once field text shows username (FAST_PATH_MODE only).
FAST_PREDICTIVE_ROW_TAP = True
# Non-deferred typing (set_text / send_keys): short UI confirm poll.
FAST_TYPING_CONFIRM_MAX_S = 0.3
FAST_RESET_BETWEEN_TARGETS = True
FAST_RESET_METHOD = "home_open_search"
# After tapping Message on profile: poll for composer up to this many seconds (draft-only fast path).
FAST_DM_COMPOSER_ACCEPT_S = 1.45
# Multi-target: on these exit codes, reset to search and continue remaining targets (no full-run abort).
FAST_RECOVERABLE_TARGET_EXIT_CODES = (5, 7, 8, 9, 11, 15, 16, 17, 18)
# Reuse search surface if recent successful open+type (same process)
SEARCH_SURFACE_CACHE_TTL_S = 600.0
# Reported when open_search navigation is skipped (observability; tune to your devices)
SEARCH_OPEN_SKIP_CREDIT_MS = 2800.0
# After back from profile, wait for search UI (seconds)
BACK_TO_SEARCH_MAX_WAIT_S = 2.5
PROFILE_TO_SEARCH_FAST_FALLBACK_S = 2.5

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
