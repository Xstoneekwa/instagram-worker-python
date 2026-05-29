#!/usr/bin/env python3
"""Patch 2A HTTP smoke (Edge only).

Reads INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN from the environment only.
If sourced from .env.smoke.local, keep that file temporary and delete it after
the smoke run. This runner never prints token, password, raw responses, or
secret_ref values.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

FAKE_PASSWORD = "FakePass_DoNotUse_20260529!"
SMOKE_USERNAME = "smoke_patch2a_add_profile"
EDGE_URL = "https://zgafnshkjywfltxgbtzg.supabase.co/functions/v1/instagram-credentials"


def _safe_json_loads(text: str) -> dict[str, Any]:
    return json.loads(text) if text.strip() else {}


def _http_post(payload: dict[str, Any], token: str) -> tuple[int, str, dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(EDGE_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            text = res.read().decode("utf-8")
            return res.status, text, _safe_json_loads(text)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        try:
            parsed = _safe_json_loads(text)
        except json.JSONDecodeError:
            parsed = {"raw_error": "non_json_response"}
        return exc.code, text, parsed


def _leak_scan(text: str, forbidden: list[str]) -> list[str]:
    hits: list[str] = []
    for needle in forbidden:
        if needle and needle in text:
            hits.append("forbidden_value_leak")
    return hits


def _print_case(name: str, data: dict[str, Any]) -> None:
    print(json.dumps({"case": name, **data}, sort_keys=True))


def main() -> int:
    token = (os.environ.get("INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN") or "").strip()
    account_id = (os.environ.get("PATCH2A_SMOKE_ACCOUNT_ID") or "").strip()
    smoke_username = (
        os.environ.get("PATCH2A_SMOKE_USERNAME") or SMOKE_USERNAME
    ).strip()
    if not token:
        _print_case("token_check", {"token_present": False})
        return 2
    _print_case("token_check", {"token_present": True})
    if not account_id:
        _print_case("config", {"ok": False, "error": "PATCH2A_SMOKE_ACCOUNT_ID_missing"})
        return 2
    if len(smoke_username) > 30:
        _print_case(
            "config",
            {"ok": False, "error": "smoke_username_too_long", "username_len": len(smoke_username)},
        )
        return 2

    happy_payload = {
        "action": "submit_add_profile_credentials",
        "account_id": account_id,
        "expected_username": smoke_username,
        "password": FAKE_PASSWORD,
        "actor_type": "admin",
        "metadata_safe": {
            "flow": "add_profile",
            "smoke": True,
            "external_request_id": "smoke_patch2a_http_20260529",
        },
    }
    status, raw_text, body = _http_post(happy_payload, token)
    leaks = _leak_scan(raw_text, [FAKE_PASSWORD, token])
    _print_case(
        "happy_path",
        {
            "http_status": status,
            "ok": body.get("ok"),
            "error": body.get("error"),
            "password_status": body.get("password_status"),
            "credentials_status": body.get("credentials_status") or body.get("status"),
            "credentials_version": body.get("credentials_version"),
            "reauth_required": body.get("reauth_required"),
            "next_action": body.get("next_action"),
            "has_password_in_response": '"password"' in raw_text.lower(),
            "has_secret_ref_in_response": "secret_ref" in raw_text or "supabase_vault://" in raw_text,
            "leak_hits": leaks,
        },
    )
    if status != 200 or body.get("ok") is not True:
        return 2

    cases = [
        (
            "error_account_not_found",
            {
                "action": "submit_add_profile_credentials",
                "account_id": "00000000-0000-4000-8000-000000000099",
                "expected_username": smoke_username,
                "password": FAKE_PASSWORD,
                "actor_type": "admin",
            },
            404,
            "account_not_found",
        ),
        (
            "error_missing_password",
            {
                "action": "submit_add_profile_credentials",
                "account_id": account_id,
                "expected_username": smoke_username,
                "password": "",
                "actor_type": "admin",
            },
            400,
            "password_invalid",
        ),
        (
            "error_forbidden_metadata",
            {
                "action": "submit_add_profile_credentials",
                "account_id": account_id,
                "expected_username": smoke_username,
                "password": FAKE_PASSWORD,
                "actor_type": "admin",
                "metadata_safe": {"password": "nope"},
            },
            400,
            "metadata_safe_forbidden",
        ),
        (
            "error_username_mismatch",
            {
                "action": "submit_add_profile_credentials",
                "account_id": account_id,
                "expected_username": "different_username_smoke",
                "password": FAKE_PASSWORD,
                "actor_type": "admin",
            },
            409,
            "expected_username_mismatch",
        ),
    ]
    failed = False
    for name, payload, expected_status, expected_error_prefix in cases:
        st, raw, parsed = _http_post(payload, token)
        leaks = _leak_scan(raw, [FAKE_PASSWORD, token])
        matches = str(parsed.get("error", "")).startswith(expected_error_prefix)
        _print_case(
            name,
            {
                "http_status": st,
                "ok": parsed.get("ok"),
                "error": parsed.get("error"),
                "expected_status": expected_status,
                "error_matches": matches,
                "has_password_in_response": '"password"' in raw.lower(),
                "has_secret_ref_in_response": "secret_ref" in raw or "supabase_vault://" in raw,
                "leak_hits": leaks,
            },
        )
        if st != expected_status or not matches:
            failed = True

    return 3 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
