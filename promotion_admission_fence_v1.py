"""Persistent Supabase admission fence used by Worker promotion transactions."""
from __future__ import annotations

import json
import urllib.request
from typing import Any


BARRIER_NAME = "worker_promotion_active"


class PromotionFenceError(RuntimeError):
    pass


class SupabasePromotionFence:
    def __init__(self, base_url: str, service_key: str, *, timeout: float = 10.0):
        self.base_url = str(base_url).rstrip("/")
        self.service_key = str(service_key)
        self.timeout = timeout
        if not self.base_url or not self.service_key:
            raise PromotionFenceError("promotion_fence_credentials_missing")

    def _rpc(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/rest/v1/rpc/{name}",
            data=body,
            method="POST",
            headers={
                "apikey": self.service_key,
                "Authorization": f"Bearer {self.service_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise PromotionFenceError(f"promotion_fence_rpc_failed:{name}:{type(exc).__name__}") from exc
        if not isinstance(value, dict):
            raise PromotionFenceError(f"promotion_fence_rpc_payload_invalid:{name}")
        return value

    def acquire(self, *, transaction_id: str, candidate_sha: str) -> dict[str, Any]:
        value = self._rpc(
            "acquire_phonefarm_worker_promotion_fence_v1",
            {"p_transaction_id": transaction_id, "p_candidate_sha": candidate_sha},
        )
        if value.get("ok") is not True or value.get("active") is not True:
            raise PromotionFenceError(str(value.get("reason") or "promotion_fence_acquire_rejected"))
        return value

    def inspect(self, *, transaction_id: str) -> dict[str, Any]:
        value = self.inspect_state(transaction_id=transaction_id)
        if (
            value.get("active") is not True
            or value.get("persistent") is not True
        ):
            raise PromotionFenceError(str(value.get("reason") or "promotion_fence_not_owned"))
        return value

    def inspect_state(self, *, transaction_id: str) -> dict[str, Any]:
        value = self._rpc(
            "inspect_phonefarm_worker_promotion_fence_v1",
            {"p_transaction_id": transaction_id},
        )
        if (
            value.get("ok") is not True
            or value.get("transaction_id") != transaction_id
        ):
            raise PromotionFenceError(str(value.get("reason") or "promotion_fence_not_owned"))
        return value

    def release(self, *, transaction_id: str, terminal_state: str) -> dict[str, Any]:
        value = self._rpc(
            "release_phonefarm_worker_promotion_fence_v1",
            {"p_transaction_id": transaction_id, "p_terminal_state": terminal_state},
        )
        if value.get("ok") is not True or value.get("active") is not False:
            raise PromotionFenceError(str(value.get("reason") or "promotion_fence_release_rejected"))
        return value
