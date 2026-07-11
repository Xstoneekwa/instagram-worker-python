"""Account-level follow-source rotation contract (CT 30/4) provisioning and audit.

No navigation side effects. DB writes happen only through explicit provisioning or
repair entry points — never silently during account_session runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import supabase_client
from logs import log

CONTRACT_MAX_FOLLOWS_PER_TARGET_PER_RUN = 30
CONTRACT_MAX_TARGETS_PER_RUN = 4
CONTRACT_TABLE = "account_follow_source_settings"


@dataclass(frozen=True)
class FollowSourceRotationContractAudit:
    account_id: str
    account_username: str
    settings_source: str
    max_follows_per_target_per_run: int
    max_targets_per_run: int
    contract_ok: bool
    repair_required: bool
    repair_action: str
    row_present: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "account_username": self.account_username,
            "settings_source": self.settings_source,
            "max_follows_per_target_per_run": self.max_follows_per_target_per_run,
            "max_targets_per_run": self.max_targets_per_run,
            "contract_ok": self.contract_ok,
            "repair_required": self.repair_required,
            "repair_action": self.repair_action,
            "row_present": self.row_present,
            "expected_max_follows_per_target_per_run": CONTRACT_MAX_FOLLOWS_PER_TARGET_PER_RUN,
            "expected_max_targets_per_run": CONTRACT_MAX_TARGETS_PER_RUN,
            "repair_table": CONTRACT_TABLE,
        }


def _contract_values_match(
    *,
    max_follows_per_target_per_run: int,
    max_targets_per_run: int,
) -> bool:
    return (
        int(max_follows_per_target_per_run) == CONTRACT_MAX_FOLLOWS_PER_TARGET_PER_RUN
        and int(max_targets_per_run) == CONTRACT_MAX_TARGETS_PER_RUN
    )


def audit_follow_source_rotation_contract(
    account_id: str,
    *,
    account_username: str = "",
) -> FollowSourceRotationContractAudit:
    """Read-only contract audit. Never mutates DB."""
    aid = str(account_id or "").strip()
    uname = str(account_username or "").strip()
    if not aid:
        return FollowSourceRotationContractAudit(
            account_id="",
            account_username=uname,
            settings_source="missing_account_id",
            max_follows_per_target_per_run=0,
            max_targets_per_run=0,
            contract_ok=False,
            repair_required=False,
            repair_action="skip_missing_account_id",
            row_present=False,
        )
    try:
        row = supabase_client.load_account_follow_source_settings(aid)
    except Exception as exc:
        log(
            "warning",
            "follow_source_rotation_contract_audit_load_failed",
            account_id=aid,
            account_username=uname,
            error=str(exc)[:200],
        )
        return FollowSourceRotationContractAudit(
            account_id=aid,
            account_username=uname,
            settings_source="load_failed",
            max_follows_per_target_per_run=0,
            max_targets_per_run=0,
            contract_ok=False,
            repair_required=True,
            repair_action="retry_audit_after_load_failure",
            row_present=False,
        )
    if not row:
        return FollowSourceRotationContractAudit(
            account_id=aid,
            account_username=uname,
            settings_source="default",
            max_follows_per_target_per_run=0,
            max_targets_per_run=0,
            contract_ok=False,
            repair_required=True,
            repair_action="create_row_30_4",
            row_present=False,
        )
    try:
        max_follows = int(row.get("max_follows_per_target_per_run") or 0)
    except (TypeError, ValueError):
        max_follows = 0
    try:
        max_targets = int(row.get("max_targets_per_run") or 0)
    except (TypeError, ValueError):
        max_targets = 0
    contract_ok = _contract_values_match(
        max_follows_per_target_per_run=max_follows,
        max_targets_per_run=max_targets,
    )
    repair_action = "none"
    if not contract_ok:
        repair_action = "update_row_to_30_4"
    return FollowSourceRotationContractAudit(
        account_id=aid,
        account_username=uname,
        settings_source="account",
        max_follows_per_target_per_run=max_follows,
        max_targets_per_run=max_targets,
        contract_ok=contract_ok,
        repair_required=not contract_ok,
        repair_action=repair_action,
        row_present=True,
    )


def ensure_follow_source_rotation_contract_row(
    account_id: str,
    *,
    account_username: str = "",
    context: str = "provisioning",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Idempotently create missing account_follow_source_settings row at 30/4.

    Does not update existing rows with non-contract values — use
    ``repair_follow_source_rotation_contract`` with explicit repair_go.
    """
    audit = audit_follow_source_rotation_contract(account_id, account_username=account_username)
    if audit.contract_ok:
        return {
            "ok": True,
            "action": "already_contract_ok",
            "dry_run": bool(dry_run),
            "db_mutation_performed": False,
            **audit.to_dict(),
        }
    if audit.row_present:
        log(
            "warning",
            "follow_source_rotation_contract_ensure_skipped_existing_non_contract_row",
            account_id=audit.account_id,
            account_username=audit.account_username,
            context=context,
            repair_action=audit.repair_action,
            actual_max_follows_per_target_per_run=audit.max_follows_per_target_per_run,
            actual_max_targets_per_run=audit.max_targets_per_run,
            repair_required=True,
            db_mutation_performed=False,
        )
        return {
            "ok": False,
            "action": "existing_non_contract_row_requires_explicit_repair",
            "dry_run": bool(dry_run),
            "db_mutation_performed": False,
            **audit.to_dict(),
        }
    if dry_run:
        log(
            "info",
            "follow_source_rotation_contract_ensure_dry_run",
            account_id=audit.account_id,
            account_username=audit.account_username,
            context=context,
            repair_action="create_row_30_4",
            db_mutation_performed=False,
        )
        return {
            "ok": True,
            "action": "would_create_row_30_4",
            "dry_run": True,
            "db_mutation_performed": False,
            **audit.to_dict(),
        }
    row = supabase_client.ensure_account_follow_source_settings(
        audit.account_id,
        max_follows_per_target_per_run=CONTRACT_MAX_FOLLOWS_PER_TARGET_PER_RUN,
        max_targets_per_run=CONTRACT_MAX_TARGETS_PER_RUN,
        updated_by=f"worker:{context}",
    )
    log(
        "info",
        "follow_source_rotation_contract_row_created",
        account_id=audit.account_id,
        account_username=audit.account_username,
        context=context,
        max_follows_per_target_per_run=CONTRACT_MAX_FOLLOWS_PER_TARGET_PER_RUN,
        max_targets_per_run=CONTRACT_MAX_TARGETS_PER_RUN,
        db_mutation_performed=True,
    )
    return {
        "ok": True,
        "action": "created_row_30_4",
        "dry_run": False,
        "db_mutation_performed": True,
        "row": dict(row) if isinstance(row, dict) else {},
        **audit.to_dict(),
    }


def repair_follow_source_rotation_contract(
    account_id: str,
    *,
    account_username: str = "",
    context: str = "repair",
    dry_run: bool = True,
    repair_go: bool = False,
) -> dict[str, Any]:
    """Controlled repair for existing non-contract rows. Requires repair_go when dry_run=False."""
    audit = audit_follow_source_rotation_contract(account_id, account_username=account_username)
    if audit.contract_ok:
        return {
            "ok": True,
            "action": "already_contract_ok",
            "dry_run": bool(dry_run),
            "repair_go": bool(repair_go),
            "db_mutation_performed": False,
            **audit.to_dict(),
        }
    if dry_run or not repair_go:
        log(
            "info",
            "follow_source_rotation_contract_repair_dry_run",
            account_id=audit.account_id,
            account_username=audit.account_username,
            context=context,
            repair_action=audit.repair_action,
            repair_required=True,
            repair_go=bool(repair_go),
            db_mutation_performed=False,
        )
        return {
            "ok": True,
            "action": f"dry_run_{audit.repair_action}",
            "dry_run": True,
            "repair_go": bool(repair_go),
            "db_mutation_performed": False,
            **audit.to_dict(),
        }
    row = supabase_client.upsert_account_follow_source_settings(
        audit.account_id,
        max_follows_per_target_per_run=CONTRACT_MAX_FOLLOWS_PER_TARGET_PER_RUN,
        max_targets_per_run=CONTRACT_MAX_TARGETS_PER_RUN,
        updated_by=f"worker:{context}",
    )
    log(
        "info",
        "follow_source_rotation_contract_repair_applied",
        account_id=audit.account_id,
        account_username=audit.account_username,
        context=context,
        max_follows_per_target_per_run=CONTRACT_MAX_FOLLOWS_PER_TARGET_PER_RUN,
        max_targets_per_run=CONTRACT_MAX_TARGETS_PER_RUN,
        db_mutation_performed=True,
    )
    return {
        "ok": True,
        "action": "repaired_row_to_30_4",
        "dry_run": False,
        "repair_go": True,
        "db_mutation_performed": True,
        "row": dict(row) if isinstance(row, dict) else {},
        **audit.to_dict(),
    }


def maybe_provision_follow_source_rotation_on_ready(
    *,
    account_id: str,
    account_username: str = "",
    final_provisioning_status: str | None,
    context: str = "login_provisioning_ready",
) -> dict[str, Any]:
    if str(final_provisioning_status or "").strip() != "ready":
        return {
            "ok": True,
            "skipped": True,
            "reason": "provisioning_not_ready",
            "db_mutation_performed": False,
        }
    return ensure_follow_source_rotation_contract_row(
        account_id,
        account_username=account_username,
        context=context,
        dry_run=False,
    )
