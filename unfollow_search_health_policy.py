"""Pure per-session circuit breaker for the Unfollow Search fallback."""

from __future__ import annotations

from dataclasses import dataclass


SEARCH_SURFACE_CONSECUTIVE_FAILURE_LIMIT = 3


@dataclass
class SearchSurfaceCircuitBreaker:
    consecutive_technical_failures: int = 0
    total_technical_failures: int = 0
    healthy_outcomes: int = 0
    opened: bool = False
    stable_reason: str = ""

    def record(self, classification: str) -> bool:
        value = str(classification or "").strip()
        if value in {"exact_result_visible", "username_not_found_confirmed"}:
            self.consecutive_technical_failures = 0
            self.healthy_outcomes += 1
            return False
        if value == "search_surface_unhealthy":
            self.total_technical_failures += 1
            self.consecutive_technical_failures += 1
            if (
                self.consecutive_technical_failures
                >= SEARCH_SURFACE_CONSECUTIVE_FAILURE_LIMIT
            ):
                self.opened = True
                self.stable_reason = (
                    "unfollow_search_surface_consecutive_failure_limit_reached"
                )
        return self.opened

    def as_dict(self) -> dict[str, object]:
        return {
            "search_surface_consecutive_technical_failures": (
                self.consecutive_technical_failures
            ),
            "search_surface_total_technical_failures": self.total_technical_failures,
            "search_surface_healthy_outcomes": self.healthy_outcomes,
            "search_surface_circuit_breaker_open": self.opened,
            "search_surface_circuit_breaker_reason": self.stable_reason,
            "search_surface_consecutive_failure_limit": (
                SEARCH_SURFACE_CONSECUTIVE_FAILURE_LIMIT
            ),
        }
