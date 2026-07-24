"""Render DM templates before freezing ig_dm_jobs.message_body."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


SUPPORTED_VARIABLES = {"username", "name", "account_username"}
TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}")


@dataclass(frozen=True)
class RenderResult:
    rendered_body: str
    used_variables: list[str] = field(default_factory=list)
    missing_variables: list[str] = field(default_factory=list)
    fallbacks_used: list[str] = field(default_factory=list)
    unresolved_tokens: list[str] = field(default_factory=list)
    ok: bool = True
    reason: str | None = None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def find_template_tokens(body: str) -> list[str]:
    """Return unique token names found in {var} or {{var}} placeholders."""
    seen: set[str] = set()
    out: list[str] = []
    for match in TOKEN_RE.finditer(str(body or "")):
        name = str(match.group(1) or match.group(2) or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def has_unresolved_template_tokens(body: str) -> bool:
    return bool(TOKEN_RE.search(str(body or "")))


def render_dm_template(template_body: str, context: dict[str, Any]) -> RenderResult:
    """Render supported DM variables with safe fallbacks.

    Supported variables:
    - username: recipient_username
    - name: recipient_name/display_name/full_name, fallback recipient_username
    - account_username: sender Instagram username
    """
    body = str(template_body or "")
    tokens = find_template_tokens(body)
    unknown = [name for name in tokens if name not in SUPPORTED_VARIABLES]
    if unknown:
        return RenderResult(
            rendered_body=body,
            used_variables=[],
            unresolved_tokens=unknown,
            ok=False,
            reason="unsupported_template_variable",
        )

    recipient_username = _clean(
        context.get("recipient_username")
        or context.get("username")
        or context.get("follower_username")
    )
    recipient_name = _clean(
        context.get("recipient_name")
        or context.get("display_name")
        or context.get("full_name")
        or context.get("name")
    )
    account_username = _clean(context.get("account_username"))

    values = {
        "username": recipient_username,
        "name": recipient_name or recipient_username,
        "account_username": account_username,
    }
    fallbacks: list[str] = []
    missing: list[str] = []
    if "name" in tokens and not recipient_name and recipient_username:
        fallbacks.append("name:recipient_username")
    for name in tokens:
        if not values.get(name):
            missing.append(name)

    if missing:
        return RenderResult(
            rendered_body=body,
            used_variables=[name for name in tokens if values.get(name)],
            missing_variables=missing,
            fallbacks_used=fallbacks,
            unresolved_tokens=missing,
            ok=False,
            reason="missing_template_variable",
        )

    used: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = str(match.group(1) or match.group(2) or "").strip()
        if name not in used:
            used.append(name)
        return values.get(name, "")

    rendered = TOKEN_RE.sub(replace, body)
    unresolved = find_template_tokens(rendered)
    ok = not unresolved
    return RenderResult(
        rendered_body=rendered,
        used_variables=used,
        missing_variables=[],
        fallbacks_used=fallbacks,
        unresolved_tokens=unresolved,
        ok=ok,
        reason=None if ok else "unresolved_template_token",
    )
