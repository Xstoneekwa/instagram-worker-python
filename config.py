"""Minimal config for the experimental uiautomator2 worker."""

import os


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(str(raw).strip())
    except ValueError:
        return int(default)


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return str(default)
    value = str(raw).strip()
    return value if value else str(default)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(str(raw).strip())
    except ValueError:
        return float(default)

# Target account to open (exact match under Accounts tab).
# Controlled real-send test: prefer empty / new thread only (no prior DM history with this account).
TARGET_USERNAME = "mythyllus"

INSTAGRAM_PACKAGE = "com.instagram.android"

# None = use default device from adb / uiautomator2
DEVICE_SERIAL = None

# Entry 2C-3 assignment dispatch is opt-in and read-only by default.
ACCOUNT_ASSIGNMENT_DISPATCH_ENABLED = _env_bool("ACCOUNT_ASSIGNMENT_DISPATCH_ENABLED", False)
ACCOUNT_ASSIGNMENT_DISPATCH_REQUIRE_ASSIGNMENT = _env_bool(
    "ACCOUNT_ASSIGNMENT_DISPATCH_REQUIRE_ASSIGNMENT",
    False,
)
ACCOUNT_ASSIGNMENT_DISPATCH_ENFORCE_WINDOW = _env_bool(
    "ACCOUNT_ASSIGNMENT_DISPATCH_ENFORCE_WINDOW",
    False,
)
ACCOUNT_ASSIGNMENT_DISPATCH_RUN_TYPES = os.getenv(
    "ACCOUNT_ASSIGNMENT_DISPATCH_RUN_TYPES",
    "outreach_session",
)
ACCOUNT_ASSIGNMENT_DISPATCH_LOG_SENSITIVE = _env_bool(
    "ACCOUNT_ASSIGNMENT_DISPATCH_LOG_SENSITIVE",
    False,
)

# ORF-2 runtime telemetry is opt-in and fail-open by default.
RUNTIME_EVENTS_ENABLED = _env_bool("RUNTIME_EVENTS_ENABLED", False)
RUNTIME_HEARTBEATS_ENABLED = _env_bool("RUNTIME_HEARTBEATS_ENABLED", False)
RUNTIME_EVENTS_FAIL_OPEN = _env_bool("RUNTIME_EVENTS_FAIL_OPEN", True)
RUNTIME_HEARTBEAT_INTERVAL_SECONDS = _env_int("RUNTIME_HEARTBEAT_INTERVAL_SECONDS", 30)
RUNTIME_EVENTS_LOG_LOCAL_FALLBACK = _env_bool("RUNTIME_EVENTS_LOG_LOCAL_FALLBACK", True)
RUNTIME_EVENTS_INCLUDE_DEBUG = _env_bool("RUNTIME_EVENTS_INCLUDE_DEBUG", False)

# ORF-3B-2 account incidents are opt-in and fail-open by default (no runtime hooks yet).
RUNTIME_INCIDENTS_ENABLED = _env_bool("RUNTIME_INCIDENTS_ENABLED", False)
RUNTIME_INCIDENTS_FAIL_OPEN = _env_bool("RUNTIME_INCIDENTS_FAIL_OPEN", True)
RUNTIME_INCIDENTS_LOG_LOCAL_FALLBACK = _env_bool("RUNTIME_INCIDENTS_LOG_LOCAL_FALLBACK", True)
RUNTIME_INCIDENTS_INCLUDE_DEBUG_METADATA = _env_bool(
    "RUNTIME_INCIDENTS_INCLUDE_DEBUG_METADATA",
    False,
)

# Entry 2F-2 incident -> dashboard action reconciliation is opt-in.
INCIDENT_DASHBOARD_SYNC_ENABLED = _env_bool("INCIDENT_DASHBOARD_SYNC_ENABLED", False)
INCIDENT_DASHBOARD_SYNC_LIMIT = _env_int("INCIDENT_DASHBOARD_SYNC_LIMIT", 50)
INCIDENT_DASHBOARD_SYNC_FAIL_OPEN = _env_bool("INCIDENT_DASHBOARD_SYNC_FAIL_OPEN", True)

# Entry 2E-4A Instagram account status publisher is isolated and opt-in.
INSTAGRAM_ACCOUNT_STATUS_PUBLISH_ENABLED = _env_bool(
    "INSTAGRAM_ACCOUNT_STATUS_PUBLISH_ENABLED",
    False,
)
INSTAGRAM_ACCOUNT_STATUS_API_URL = _env_str("INSTAGRAM_ACCOUNT_STATUS_API_URL", "")
INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN = os.getenv(
    "INSTAGRAM_ACCOUNT_STATUS_INTERNAL_API_TOKEN",
    "",
).strip()
INSTAGRAM_ACCOUNT_STATUS_FAIL_OPEN = _env_bool(
    "INSTAGRAM_ACCOUNT_STATUS_FAIL_OPEN",
    True,
)
INSTAGRAM_ACCOUNT_STATUS_TIMEOUT_SECONDS = _env_float(
    "INSTAGRAM_ACCOUNT_STATUS_TIMEOUT_SECONDS",
    10.0,
)

# ORF-4B incident notifications are a separate dry-run dispatcher by default.
INCIDENT_NOTIFICATIONS_ENABLED = _env_bool("INCIDENT_NOTIFICATIONS_ENABLED", False)
INCIDENT_NOTIFICATIONS_FAIL_OPEN = _env_bool("INCIDENT_NOTIFICATIONS_FAIL_OPEN", True)
INCIDENT_NOTIFICATIONS_DRY_RUN = _env_bool("INCIDENT_NOTIFICATIONS_DRY_RUN", True)
INCIDENT_NOTIFICATIONS_CHANNELS = _env_str("INCIDENT_NOTIFICATIONS_CHANNELS", "slack")
INCIDENT_NOTIFICATIONS_SLACK_ENABLED = _env_bool(
    "INCIDENT_NOTIFICATIONS_SLACK_ENABLED",
    True,
)
INCIDENT_NOTIFICATIONS_DISCORD_ENABLED = _env_bool(
    "INCIDENT_NOTIFICATIONS_DISCORD_ENABLED",
    True,
)
INCIDENT_NOTIFICATIONS_MIN_SEVERITY = _env_str(
    "INCIDENT_NOTIFICATIONS_MIN_SEVERITY",
    "warning",
)
INCIDENT_NOTIFICATIONS_MAX_PER_RUN = _env_int("INCIDENT_NOTIFICATIONS_MAX_PER_RUN", 20)
INCIDENT_NOTIFICATIONS_COOLDOWN_MINUTES = _env_int(
    "INCIDENT_NOTIFICATIONS_COOLDOWN_MINUTES",
    60,
)
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
INCIDENT_NOTIFICATIONS_HTTP_TIMEOUT_SECONDS = _env_int(
    "INCIDENT_NOTIFICATIONS_HTTP_TIMEOUT_SECONDS",
    10,
)

# ADBKeyboard (optional). Low-latency ADB_INPUT_TEXT broadcast when installed.
FAST_IME = "com.android.adbkeyboard/.AdbIME"

# Navigation / polling (seconds)
APP_START_WAIT_S = 2.5
# Short pause when reusing warm Instagram session (no force-stop)
WARM_SESSION_MICRO_WAIT_S = 0.08
SEARCH_FIELD_WAIT_S = 5.0
# Follow CT only: short EditText poll when global Search was just confirmed (seconds)
FOLLOW_CT_SEARCH_FIELD_WAIT_S = 0.4
# Follow CT: confirm typed handle via search field text only (SERP row scan deferred to tap)
FOLLOW_CT_TYPING_CONFIRM_MAX_S = 0.45
# Follow CT: skip redundant type_search precheck after open_search strict OK (seconds)
FOLLOW_CT_OPEN_SEARCH_STRICT_RECENT_TTL_S = 45.0
# Per-attempt timeout when probing bottom-nav Search tab resource ids (seconds)
OPEN_SEARCH_TAB_PROBE_TIMEOUT_S = 0.06
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
# V4.5-B Welcome list-native: slow modal DM threads (poll early-exit until composer or cap).
WELCOME_DM_THREAD_DETECT_MAX_S = 9.0
WELCOME_DM_FORENSICS_DEBUG = False
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
ENABLE_REAL_DM_SEND = False
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
# Follow SAFE V1 — real profile follow (single tap, bounded verify). Default off for existing deploys.
ENABLE_REAL_FOLLOW = True
# After a successful follow, open DM from the same profile (True) or return to search and exit (False).
FOLLOW_THEN_DM = False
FOLLOW_COOLDOWN_HOURS = 999999
FOLLOW_MAX_PER_RUN = 2
FOLLOW_VERIFY_TIMEOUT_MS = 4000
# Max wait to locate a tappable Follow / Suivre control on profile header (seconds).
FOLLOW_BUTTON_WAIT_S = 4.0
# Followers list engine V1 — source profile → followers list → follower profiles → FOLLOW SAFE (off by default).
# TEST minimal: 2 follows réels, pas de DM (voir aussi ENABLE_REAL_DM_SEND / FOLLOW_THEN_DM).
ENABLE_FOLLOWERS_LIST_ENGINE = True
# Source profile handle to open (empty = first CLI / queue target username is the source profile only for this mode).
FOLLOWERS_SOURCE_USERNAME = "mythyllus"
FOLLOWERS_LIST_MAX_ITERATIONS_PER_RUN = 5
FOLLOWERS_LIST_OPEN_WAIT_S = 4.0
FOLLOWERS_LIST_RETURN_MAX_RETRIES = 2
# Followers Entry Engine V2: hybrid followers-stat candidates, no coordinate fallback drift; force-stop on hard failure.
ENABLE_FOLLOWERS_ENTRY_ENGINE_V2 = True
FOLLOWERS_ENTRY_V2_MIN_CONFIDENCE = 0.62
# When True: allow geometry-only followers column tap if no text/XML candidates and profile gates match.
FOLLOWERS_ENTRY_V2_ALLOW_GEOMETRY_TAP = False
FOLLOWERS_LIST_SCROLL_MAX_PER_SESSION = 25
# Adaptive followers exploration V1 (skip-streak acceleration, stagnation guards, telemetry).
FOLLOWERS_EXPLORATION_V1_ENABLED = True
FOLLOWERS_EXPLORATION_V1_SKIP_STREAK_ACCEL_THRESHOLD = 3
FOLLOWERS_EXPLORATION_V1_SCROLL_SOFT_MAX_PER_SESSION = 120
FOLLOWERS_EXPLORATION_V1_SCROLL_ABSOLUTE_MAX = 200
FOLLOWERS_EXPLORATION_V1_NO_NEW_VISUAL_PROGRESS_MAX = 5
FOLLOWERS_EXPLORATION_V1_NO_ACTIONABLE_SCROLL_MAX = 15
FOLLOWERS_EXPLORATION_V1_PROGRESSIVE_MAX_PASSES = 12
FOLLOWERS_EXPLORATION_V1_SESSION_BUDGET_S = 0
FOLLOWERS_EXPLORATION_V1_SPARSE_ZERO_BUTTON_SCROLL_MAX = 8
FOLLOWERS_EXPLORATION_V1_ACCEL_RECYCLER_STEPS = 10
# Après visual_fallback avec forte confiance, ignorera XML stale pendant N itérations de boucle.
FOLLOWERS_VISUAL_XML_STALE_GRACE_ITERATIONS = 3
# Followers list: bounded progressive soft exploration (zero_follow_spans_soft; no fling).
FOLLOWERS_LIST_PROGRESSIVE_EXPLORATION_MAX_PASSES = 3
FOLLOWERS_LIST_PROGRESSIVE_SOFT_SCROLL_STEPS = 4
# Segment B: injection screenshot freshness (V3.2-B2).
FOLLOWERS_INJECTION_EVIDENCE_MAX_AGE_MS = 5000
FOLLOWERS_COMMITTED_SKIP_FULL_DETECT_MAX_AGE_MS = 30000
FOLLOWERS_POST_RETURN_PROMOTED_EVIDENCE_MAX_AGE_MS = 5000
# When XML/accessibility tree is stale after opening followers, use screenshot heuristics (no clicks).
ENABLE_FOLLOWERS_VISUAL_FALLBACK = True
FOLLOWERS_VISUAL_FALLBACK_MIN_CONFIDENCE = 0.65
FOLLOWERS_VISUAL_MIN_FOLLOW_BUTTONS = 3
# Read-only screenshot row layout probe when XML stays stale after visual_fallback (no taps).
ENABLE_FOLLOWERS_VISUAL_CANDIDATE_DIAGNOSTIC = True
# Visual row picker from screenshot when XML candidates are empty (V1: no taps / follow / scroll).
ENABLE_VISUAL_FOLLOWERS_CANDIDATE_PICKER = True
# False requis pour ne pas terminer le followers engine sur exit 45 (picker dry-run only).
VISUAL_FOLLOWERS_PICKER_DRY_RUN = False
VISUAL_FOLLOWERS_MAX_CANDIDATES_PER_SCREEN = 5
# Tap one visual row (username zone) to open follower profile; dry-run stops after verify (no follow/DM).
ENABLE_VISUAL_FOLLOWERS_CANDIDATE_OPEN = True
# False = ouverture réelle des lignes followers (validation device réel followers/mute).
VISUAL_FOLLOWERS_OPEN_DRY_RUN = False
VISUAL_FOLLOWERS_OPEN_MAX_PER_RUN = 1
# Logged-in worker handle: excluded from visual follower-row open (self row on another profile's followers list).
VISUAL_FOLLOWERS_ACTION_ACCOUNT_USERNAME = ""
# Open grid post + like zone dry-run from an already-open profile (no real like tap).
ENABLE_VISUAL_POST_LIKE_FLOW = True
# Real controlled like test: True tap + verify (pair with ENABLE_REAL_VISUAL_POST_LIKE).
VISUAL_POST_LIKE_DRY_RUN = False
VISUAL_POST_MAX_LIKES_PER_PROFILE = 1
# Real like from visual post viewer (single tap, verify after); does not enable follow/DM/mute.
ENABLE_REAL_VISUAL_POST_LIKE = True
VISUAL_POST_LIKE_VERIFY_AFTER_TAP = True
# Wall budget for post-tap verify (UI + optional hierarchy/screenshot). Default 3.2s:
#  ~2.5s historically could expire mid-first-pass; +0.7s allows one fast post-tap image reuse
#  and one short repoll without turning verify into a long poll loop (see MAX_ATTEMPTS).
VISUAL_POST_LIKE_VERIFY_TIMEOUT_S = 3.2
# Max polling rounds within the timeout (hard cap). Typical successful path exits on 1st round
# (UI or post_tap PNG reuse); worst-case ~4 rounds only if time remains.
VISUAL_POST_LIKE_VERIFY_MAX_ATTEMPTS = 4
# Separate from pre-tap already_liked red heuristic; post-tap filled heart often reads slightly lower.
VISUAL_POST_LIKE_VERIFY_RED_RATIO_STRONG = 0.14
# Follow + mute sheet dry-run on open profile (no real follow/mute taps).
ENABLE_VISUAL_FOLLOW_MUTE_FLOW = True
# False = follow/mute réels après ouverture profil (validation pending + mute).
VISUAL_FOLLOW_MUTE_DRY_RUN = False
# Real visual Follow tap + verify (mute remains controlled by VISUAL_FOLLOW_MUTE_DRY_RUN).
ENABLE_REAL_VISUAL_FOLLOW = True
VISUAL_FOLLOW_VERIFY_AFTER_TAP = True
VISUAL_FOLLOW_VERIFY_TIMEOUT_S = 3.0
VISUAL_MUTE_POSTS_AFTER_FOLLOW = True
VISUAL_MUTE_STORIES_AFTER_FOLLOW = True
# Real mute after visual follow: Following → Mute menu → Posts/Stories toggles (V1).
ENABLE_REAL_VISUAL_MUTE_AFTER_FOLLOW = True
VISUAL_MUTE_VERIFY_AFTER_TAP = True
VISUAL_MUTE_VERIFY_TIMEOUT_S = 3.0
# Post-follow likes on candidate profile (after mute, before return CT). Master switch off until validated.
POST_FOLLOW_POST_LIKES_ENABLED = True
POST_FOLLOW_POST_LIKES_COUNT_RANGE = "1-1"
POST_FOLLOW_POST_LIKES_PERCENTAGE = 100
POST_FOLLOW_TOTAL_LIKES_LIMIT = 150
POST_FOLLOW_POST_LIKES_BUDGET_S = 10.0
# Min remaining seconds inside grid-prep (after initial probe) to realistically run micro→reprobe→long→reprobe.
# Below this, first swipe is promoted to long when grid is partial + Suggested overlay (see ensure_post_grid_visible_for_post_follow_likes).
POST_FOLLOW_LIKES_GRID_SECOND_PASS_RESERVE_S = 5.5
POST_FOLLOW_POST_LIKES_VERIFY_AFTER_TAP = True
# Vision Validation Layer: screenshot + context heuristics (no taps); gates followers/mute drift.
ENABLE_VISION_VALIDATION_LAYER = True
VISION_VALIDATION_STRICT_MODE = True
# Post-follow return CT: stop blind back/recovery after this many consecutive bad nav observations.
POST_FOLLOW_RETURN_CT_DRIFT_ABORT_STREAK = 4
# Max wall time per post-follow CT return round (no deep recovery / exploratory navigation).
POST_FOLLOW_RETURN_CT_ROUND_BUDGET_S = 10.0
# Backs delegated to return_to_followers_list within one post-follow CT round (keep low).
POST_FOLLOW_RETURN_CT_BACK_MAX_RETRIES = 1
# When True: allow hierarchy dumps + reopen followers from source profile (may tap UI). Default off for drift safety.
POST_FOLLOW_RETURN_CT_ALLOW_HIERARCHY_PROFILE_REOPEN = False
# Lock visual profile context before real Follow/Like (abort on drift; no recovery navigation).
ENABLE_VISUAL_PROFILE_CONTEXT_LOCK = True
VISUAL_PROFILE_CONTEXT_MIN_MATCH_CONFIDENCE = 0.72
# Private profiles: skip like/follow/mute when ENABLE_PRIVATE_ACCOUNT_FILTER + not FOLLOW_PRIVATE_ACCOUNTS;
# when FOLLOW_PRIVATE_ACCOUNTS True, allow real follow + Requested verification, skip post/mute until accepted.
ENABLE_PRIVATE_ACCOUNT_FILTER = True
FOLLOW_PRIVATE_ACCOUNTS = True
# --- Social memory (ig_interacted_users) — persistent anti-refollow / cooldowns ---
SOCIAL_MEMORY_ENABLED = True
# Days before we may surface the same user again for a new interaction (0 = off).
SOCIAL_MEMORY_REVISIT_COOLDOWN_DAYS = 0
SOCIAL_MEMORY_FOLLOW_REVISIT_COOLDOWN_DAYS = 0
SOCIAL_MEMORY_DM_COOLDOWN_DAYS = 0
SOCIAL_MEMORY_INTERACTION_COOLDOWN_DAYS = 0
# Max skips in one run referencing same user (followers engine); 0 = unlimited.
SOCIAL_MEMORY_MAX_SKIP_STREAK = 0
# DB row skip_count ceiling (requires column skip_count on ig_interacted_users); default high = inactive.
SOCIAL_MEMORY_MAX_SKIP_COUNT_DB = 9999
# Session quotas (0 = unlimited). Counters are in-memory per worker process.
SESSION_FOLLOW_LIMIT = 0
SESSION_TOTAL_FOLLOWS_CAP = 0
SESSION_TOTAL_UNFOLLOWS_CAP = 0
SESSION_TOTAL_LIKES_CAP = 0
SESSION_TOTAL_PM_CAP = 0
# Unfollow session real action gate. Default is probe-only; Phase 2E validation allows up to 10 real unfollows/run.
UNFOLLOW_SESSION_REAL_ACTION_ENABLED = _env_bool("UNFOLLOW_SESSION_REAL_ACTION_ENABLED", False)
UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN = max(0, min(_env_int("UNFOLLOW_SESSION_REAL_ACTION_MAX_PER_RUN", 1), 10))
UNFOLLOW_SESSION_SCROLL_MAX_PASSES = max(0, min(_env_int("UNFOLLOW_SESSION_SCROLL_MAX_PASSES", 10), 50))
UNFOLLOW_SESSION_SCROLL_V2_LITE_ENABLED = _env_bool("UNFOLLOW_SESSION_SCROLL_V2_LITE_ENABLED", False)
UNFOLLOW_SESSION_SCROLL_V2_LITE_DISTANCE_RATIO = max(
    0.30,
    min(_env_float("UNFOLLOW_SESSION_SCROLL_V2_LITE_DISTANCE_RATIO", 0.72), 0.78),
)
UNFOLLOW_SESSION_SCROLL_V2_LITE_SETTLE_S = max(
    0.20,
    min(_env_float("UNFOLLOW_SESSION_SCROLL_V2_LITE_SETTLE_S", 0.45), 1.00),
)
UNFOLLOW_SESSION_SCROLL_V2_LITE_MIN_NEW_USERNAMES = max(
    0,
    _env_int("UNFOLLOW_SESSION_SCROLL_V2_LITE_MIN_NEW_USERNAMES", 3),
)
UNFOLLOW_SESSION_SCROLL_V2_LITE_MAX_UNCHANGED_SCROLLS = max(
    1,
    _env_int("UNFOLLOW_SESSION_SCROLL_V2_LITE_MAX_UNCHANGED_SCROLLS", 3),
)
UNFOLLOW_SESSION_MAX_RECOVERABLE_ACTION_FAILURES = max(
    0,
    _env_int("UNFOLLOW_SESSION_MAX_RECOVERABLE_ACTION_FAILURES", 2),
)
# V2 exploration placeholders only. 0 = disabled; not used as stop conditions yet.
UNFOLLOW_SESSION_STOP_AFTER_SKIPPED = max(0, _env_int("UNFOLLOW_SESSION_STOP_AFTER_SKIPPED", 0))
UNFOLLOW_SESSION_MAX_MINUTES = max(0, _env_int("UNFOLLOW_SESSION_MAX_MINUTES", 0))
# Account session H2: after Follow, optionally run Unfollow in forced probe-only mode.
ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_PROBE_ENABLED = _env_bool("ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_PROBE_ENABLED", False)
# Account session H3: explicit real Follow -> Unfollow handoff, disabled by default.
ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED = _env_bool("ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_ENABLED", False)
ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS = _env_int("ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_MAX_ACTIONS", 1)
ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX = max(
    0,
    min(_env_int("ACCOUNT_SESSION_FOLLOW_TO_UNFOLLOW_REAL_HARD_MAX", 3), 10),
)
# Auto Restart V1A: passive resume-plan defaults only. No scheduler is wired here.
AUTO_RESTART_ENABLED = _env_bool("AUTO_RESTART_ENABLED", False)
AUTO_RESTART_DELAY_MINUTES = max(0, _env_int("AUTO_RESTART_DELAY_MINUTES", 20))
AUTO_RESTART_MAX_ATTEMPTS_PER_SESSION = max(
    0,
    _env_int("AUTO_RESTART_MAX_ATTEMPTS_PER_SESSION", 2),
)
AUTO_RESTART_MAX_ATTEMPTS_PER_DAY = max(
    0,
    _env_int("AUTO_RESTART_MAX_ATTEMPTS_PER_DAY", 3),
)
SESSION_TOTAL_INTERACTIONS_LIMIT = 0
SESSION_TOTAL_SUCCESSFUL_INTERACTIONS_LIMIT = 0
# When True, session exit code stays non-zero if configured quotas/phases are unmet (strict mode).
SESSION_STRICT_PHASE_COMPLETION = False
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

# --- Phone farm: freeze Instagram updates (ADB hardening, best-effort) ---
# When True, runner applies lock_instagram_update_system after device health_check.
LOCK_INSTAGRAM_AUTO_UPDATE = True
# When True, Play Store (com.android.vending) is disabled for user 0 as part of the lock.
DISABLE_PLAY_STORE_FOR_PHONE_FARM = True
# When True and lock_state.json baseline disagrees with installed IG version, runner aborts before automation.
ENABLE_STRICT_UPDATE_LOCK = True
# Change in production; used only by unlock_instagram_updates().
UPDATE_UNLOCK_CODE = "TECH_ONLY_SECRET"
# When True, lock_instagram_update_system also runs `pm suspend` on Instagram (not recommended: can block app start).
SUSPEND_INSTAGRAM_APP_FOR_LOCK = False

# --- V4.1 Welcome baseline scan (no DM send, no ig_dm_jobs) ---
# V4.1-E: bounded baseline window (multi-scroll) vs legacy visible-only smoke.
WELCOME_BASELINE_WINDOW_MODE_ENABLED = True
# Legacy lab mode: first screen only, never sets welcome_baseline_completed_at.
WELCOME_BASELINE_INITIAL_VISIBLE_ONLY = False
WELCOME_BASELINE_MAX_SCROLLS_V1 = 8
WELCOME_BASELINE_MIN_ROWS_V1 = 30
WELCOME_BASELINE_MAX_SECONDS_V1 = 120
WELCOME_BASELINE_STAGNATION_NO_NEW_ROWS_MAX = 2
WELCOME_BASELINE_POST_SCROLL_SETTLE_S = 0.65
WELCOME_BASELINE_OWN_PROFILE_SETTLE_S = 1.2
WELCOME_BASELINE_FOLLOWERS_OPEN_WAIT_S = 4.0

# --- V4.2-B Welcome scan producer (enqueue only, no DM send) ---
WELCOME_SCAN_MAX_SCROLLS_V1 = 5
WELCOME_SCAN_MAX_SECONDS_V1 = 90
WELCOME_SCAN_KNOWN_CONSECUTIVE_STOP_V1 = 5
WELCOME_SCAN_STAGNATION_NO_NEW_ROWS_MAX = 2
WELCOME_SCAN_POST_SCROLL_SETTLE_S = 0.65

# --- V4.3-B DM sender dry-run (claim job, navigate, classify, no typing/send) ---
DM_SENDER_DRY_RUN_MAX_JOBS_PER_RUN = 1
DM_SENDER_DEFAULT_DM_TYPE = "welcome"
DM_SENDER_RESERVED_BY = ""
# Override at runtime: DM_SENDER_ONLY_JOB_ID="<uuid>" python3 runner.py ...
DM_SENDER_ONLY_JOB_ID = ""
DM_SENDER_FAILED_RETRY_DELAY_SECONDS = 300
# Outreach standalone hard safety caps. These are local guardrails only; business
# quotas still belong to Supabase/orchestrator policy.
OUTREACH_HARD_MAX_PER_SESSION = max(0, _env_int("OUTREACH_HARD_MAX_PER_SESSION", 5))
OUTREACH_HARD_MAX_PER_DAY = max(0, _env_int("OUTREACH_HARD_MAX_PER_DAY", 40))
STALE_OUTREACH_JOB_MINUTES = max(1, _env_int("STALE_OUTREACH_JOB_MINUTES", 30))
# --- V4.4 Welcome session real send ---
# Override: DM_SENDER_REAL_SEND_ENABLED=true python3 runner.py --run-type dm_welcome_session_send ...
DM_SENDER_REAL_SEND_ENABLED = os.getenv(
    "DM_SENDER_REAL_SEND_ENABLED",
    "false"
).lower() == "true"
WELCOME_SESSION_SEND_MAX_JOBS = 3
# V4.5 Welcome list-native sender (Followers row → profile → DM, no global Search)
WELCOME_LIST_SENDER_MAX_SCROLL_FIND = 3
WELCOME_LIST_SENDER_SCROLL_SETTLE_S = 0.45
WELCOME_LIST_SENDER_BACK_SETTLE_S = 0.45
WELCOME_LIST_SENDER_BACK_PROFILE_POLL_ATTEMPTS = 4
WELCOME_LIST_SENDER_BACK_PROFILE_POLL_INTERVAL_S = 0.35
DM_SENDER_FOLLOWERS_EXIT_SETTLE_S = 0.45
DM_SENDER_FOLLOWERS_EXIT_HARDWARE_BACK_MAX = 3
DM_SENDER_GLOBAL_SEARCH_READY_TTL_S = 120.0
# After scan→sender or post-job prepare, skip re-verify open_search for this window (seconds).
DM_SENDER_GLOBAL_SEARCH_TRUST_MAX_AGE_S = 120.0
# DM sender post-job safety: disable blind bottom-nav percent fallback after a job teardown.
DM_SENDER_DISABLE_POST_JOB_PERCENT_FALLBACK = os.getenv(
    "DM_SENDER_DISABLE_POST_JOB_PERCENT_FALLBACK",
    "true",
).lower() == "true"
