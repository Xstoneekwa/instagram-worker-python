"""Domain-scoped DM real-send flag resolution."""

from __future__ import annotations

import os
from typing import Any, Mapping

import config
from logs import log


def _truthy_env(raw: str | None) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _resolve_domain_real_send_enabled(
    *,
    env_name: str,
    config_name: str,
    log_event: str,
    environ: Mapping[str, str] | None = None,
    config_module: Any = config,
    emit_log: bool = True,
) -> tuple[bool, str]:
    env = os.environ if environ is None else environ
    env_raw = env.get(env_name)
    if env_raw is not None:
        enabled = _truthy_env(env_raw)
        source = f"env:{env_name}"
    else:
        enabled = bool(getattr(config_module, config_name, False))
        source = f"config.{config_name}"
    if emit_log:
        log("info", log_event, enabled=enabled, source=source)
    return enabled, source


def resolve_welcome_dm_real_send_enabled(
    *,
    environ: Mapping[str, str] | None = None,
    config_module: Any = config,
    emit_log: bool = True,
) -> tuple[bool, str]:
    return _resolve_domain_real_send_enabled(
        env_name="WELCOME_DM_REAL_SEND_ENABLED",
        config_name="WELCOME_DM_REAL_SEND_ENABLED",
        log_event="welcome_dm_real_send_resolved",
        environ=environ,
        config_module=config_module,
        emit_log=emit_log,
    )


def resolve_outreach_dm_real_send_enabled(
    *,
    environ: Mapping[str, str] | None = None,
    config_module: Any = config,
    emit_log: bool = True,
) -> tuple[bool, str]:
    return _resolve_domain_real_send_enabled(
        env_name="OUTREACH_DM_REAL_SEND_ENABLED",
        config_name="OUTREACH_DM_REAL_SEND_ENABLED",
        log_event="outreach_dm_real_send_resolved",
        environ=environ,
        config_module=config_module,
        emit_log=emit_log,
    )
