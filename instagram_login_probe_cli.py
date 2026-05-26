"""Isolated real-device Instagram login UI probe CLI.

Entry 2E-5C is probe-only: connect, perform one hierarchy dump, classify the
screen, and print a safe summary with timings. It does not start/stop apps,
tap, type credentials, read Vault secrets, or publish unless explicitly asked.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable, Optional

from instagram_login_provisioner import publish_classified_login_status
from instagram_login_status_classifier import (
    LoginProbeOutcome,
    classify_login_probe_outcome,
    clean_login_probe_metadata,
)
from instagram_login_ui_probe import probe_login_ui_from_hierarchy

ConnectFunc = Callable[[Optional[str], float], Any]
Publisher = Callable[..., dict]
Timer = Callable[[], float]

DUMP_WARNING_MS = 2000
TOTAL_WARNING_MS = 3000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe the current Instagram login UI state without actions.",
    )
    parser.add_argument("--device-serial", default=None, help="Optional explicit adb/uiautomator2 serial.")
    parser.add_argument("--account-id", default=None, help="Required only with --publish.")
    parser.add_argument("--expected-username", default=None, help="Optional expected username hint.")
    parser.add_argument("--publish", action="store_true", help="Publish classified status when enabled.")
    parser.add_argument("--no-publish", action="store_false", dest="publish", help="Force probe-only mode.")
    parser.add_argument("--json", action="store_true", help="Print JSON safe summary.")
    parser.add_argument("--timeout-seconds", type=float, default=5.0, help="Connect timeout hint.")
    parser.set_defaults(publish=False)
    return parser


def run_probe_command(
    args: argparse.Namespace,
    *,
    connect_func: ConnectFunc | None = None,
    publisher: Publisher | None = None,
    timer: Timer | None = None,
) -> tuple[int, dict[str, Any]]:
    timer = timer or time.perf_counter
    timings: dict[str, int] = {
        "connect_ms": 0,
        "dump_hierarchy_ms": 0,
        "classify_ms": 0,
        "total_ms": 0,
    }
    warnings: list[str] = []
    total_start = timer()

    if bool(getattr(args, "publish", False)) and not str(getattr(args, "account_id", "") or "").strip():
        summary = _base_summary(
            outcome=LoginProbeOutcome.UNKNOWN,
            probe_reason="validation_error",
            should_publish=False,
            published=False,
            publish_reason="account_id_required",
            timings=timings,
            warnings=warnings,
            args=args,
        )
        summary["ok"] = False
        summary["error"] = "account_id_required"
        summary["timings_ms"]["total_ms"] = _elapsed_ms(total_start, timer())
        return 2, summary

    connect = connect_func or _connect_uiautomator2

    try:
        start = timer()
        device = connect(str(getattr(args, "device_serial", "") or "") or None, _timeout_seconds(args))
        timings["connect_ms"] = _elapsed_ms(start, timer())
    except Exception as exc:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        summary = _base_summary(
            outcome=LoginProbeOutcome.UNKNOWN,
            probe_reason="connect_failed",
            should_publish=False,
            published=False,
            publish_reason="not_requested",
            timings=timings,
            warnings=warnings,
            args=args,
        )
        summary["ok"] = False
        summary["error"] = "connect_failed"
        summary["error_type"] = type(exc).__name__
        _add_timing_warnings(summary["timings_ms"], warnings)
        return 1, summary

    try:
        start = timer()
        try:
            hierarchy_xml = device.dump_hierarchy(compressed=False)
        except TypeError:
            hierarchy_xml = device.dump_hierarchy()
        timings["dump_hierarchy_ms"] = _elapsed_ms(start, timer())
    except Exception as exc:
        timings["total_ms"] = _elapsed_ms(total_start, timer())
        _add_timing_warnings(timings, warnings)
        summary = _base_summary(
            outcome=LoginProbeOutcome.UNKNOWN,
            probe_reason="dump_hierarchy_failed",
            should_publish=False,
            published=False,
            publish_reason="not_requested",
            timings=timings,
            warnings=warnings,
            args=args,
        )
        summary["ok"] = False
        summary["error"] = "dump_hierarchy_failed"
        summary["error_type"] = type(exc).__name__
        return 1, summary

    start = timer()
    probe_result = probe_login_ui_from_hierarchy(str(hierarchy_xml or ""), stage="login_ui_probe_cli")
    classification = classify_login_probe_outcome(
        probe_result.outcome,
        metadata={
            **probe_result.metadata,
            "cli": "instagram_login_probe_cli",
            "expected_username_present": bool(getattr(args, "expected_username", None)),
        },
    )
    timings["classify_ms"] = _elapsed_ms(start, timer())
    timings["total_ms"] = _elapsed_ms(total_start, timer())
    _add_timing_warnings(timings, warnings)

    publish_result = {"published": False, "reason": "not_requested"}
    if bool(getattr(args, "publish", False)):
        publish_result = publish_classified_login_status(
            account_id=str(getattr(args, "account_id", "") or ""),
            classification=classification,
            external_request_id="login_ui_probe_cli",
            metadata=_safe_publish_metadata(classification.metadata),
            publisher=publisher,
        )

    summary = _base_summary(
        outcome=probe_result.outcome,
        probe_reason=probe_result.reason,
        should_publish=classification.should_publish,
        published=bool(publish_result.get("published", False)),
        publish_reason=str(publish_result.get("reason", "")),
        timings=timings,
        warnings=warnings,
        args=args,
    )
    summary.update(
        {
            "ok": True,
            "login_status": classification.login_status,
            "provisioning_status": classification.provisioning_status,
            "onboarding_status": classification.onboarding_status,
            "reauth_required": classification.reauth_required,
            "reauth_reason": classification.reauth_reason,
            "classification_reason": classification.reason,
            "status_code": publish_result.get("status_code"),
            "safe_message": _safe_message(probe_result.outcome, publish_result),
        }
    )
    return 0, summary


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code, summary = run_probe_command(args)
    if args.json:
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    else:
        _print_human_summary(summary)
    return exit_code


def _connect_uiautomator2(serial: str | None, timeout_seconds: float) -> Any:
    import uiautomator2 as u2

    if serial:
        return u2.connect(serial)
    return u2.connect()


def _timeout_seconds(args: argparse.Namespace) -> float:
    try:
        return max(0.1, float(getattr(args, "timeout_seconds", 5.0)))
    except (TypeError, ValueError):
        return 5.0


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int(round((end - start) * 1000)))


def _add_timing_warnings(timings: dict[str, int], warnings: list[str]) -> None:
    if int(timings.get("dump_hierarchy_ms", 0)) > DUMP_WARNING_MS:
        warnings.append("slow_dump_hierarchy")
    if int(timings.get("total_ms", 0)) > TOTAL_WARNING_MS:
        warnings.append("slow_total_probe")


def _safe_publish_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return clean_login_probe_metadata(
        {
            **metadata,
            "cli": "instagram_login_probe_cli",
        }
    )


def _base_summary(
    *,
    outcome: LoginProbeOutcome,
    probe_reason: str,
    should_publish: bool,
    published: bool,
    publish_reason: str,
    timings: dict[str, int],
    warnings: list[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "ok": False,
        "outcome": outcome.value,
        "probe_reason": probe_reason,
        "should_publish": should_publish,
        "publish_requested": bool(getattr(args, "publish", False)),
        "published": published,
        "publish_reason": publish_reason,
        "timings_ms": dict(timings),
        "warnings": list(warnings),
        "device_serial_provided": bool(getattr(args, "device_serial", None)),
        "expected_username_present": bool(getattr(args, "expected_username", None)),
        "safe_message": "probe completed without login actions",
    }


def _safe_message(outcome: LoginProbeOutcome, publish_result: dict[str, Any]) -> str:
    if publish_result.get("published"):
        return f"probe outcome {outcome.value}; status published"
    return f"probe outcome {outcome.value}; no login action performed"


def _print_human_summary(summary: dict[str, Any]) -> None:
    print(f"outcome={summary.get('outcome')}")
    print(f"probe_reason={summary.get('probe_reason')}")
    print(f"login_status={summary.get('login_status')}")
    print(f"should_publish={summary.get('should_publish')}")
    print(f"published={summary.get('published')} reason={summary.get('publish_reason')}")
    timings = summary.get("timings_ms") or {}
    print(
        "timings_ms="
        f"connect:{timings.get('connect_ms', 0)} "
        f"dump:{timings.get('dump_hierarchy_ms', 0)} "
        f"classify:{timings.get('classify_ms', 0)} "
        f"total:{timings.get('total_ms', 0)}"
    )
    warnings = summary.get("warnings") or []
    if warnings:
        print("warnings=" + ",".join(str(item) for item in warnings))
    print(str(summary.get("safe_message") or "probe completed"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
