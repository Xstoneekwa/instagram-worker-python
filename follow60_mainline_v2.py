"""Canonical global Follow engine selection.

This module is intentionally account-neutral.  Account mode, schedule, package,
caps and incident gates still decide whether a run is eligible; they never
select an older Follow engine.
"""

DEFAULT_FOLLOW_ENGINE = "FOLLOW60_V2_MAINLINE_V1"
LEGACY_ROLLBACK_ENGINE = "FOLLOW60_V1_VERIFIED_LEGACY_ROLLBACK"
CANARY_HARNESS_INHERITED_BY_NORMAL_ACCOUNTS = False
NORMAL_RUN_REQUIRES_CANARY_CONTROL = False
NORMAL_RUN_HAS_TEN_CYCLE_BARRIER = False


def default_follow_engine(*, account_id: str = "", package: str = "") -> str:
    """Return the immutable global engine without account/package overrides."""

    del account_id, package
    return DEFAULT_FOLLOW_ENGINE

