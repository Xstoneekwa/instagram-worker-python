from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import supabase_client


class Follow60GenericRpcContractTests(unittest.TestCase):
    def test_runner_forwards_canonical_prebind_claims(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "runner.py").read_text()
        call_start = source.index(
            "_bound_control = supabase_client.bind_follow_60s_canary_runtime_v2("
        )
        call_end = source.index("\n                    )", call_start)
        call = source[call_start:call_end]
        for claim in (
            "control_id=_binding_claim.control_id",
            "expected_worker_sha=_binding_claim.expected_worker_sha",
            "baseline_release_sha=_binding_claim.baseline_release_sha",
            "binding_version=_binding_claim.binding_version",
        ):
            self.assertIn(claim, call)

    def test_binding_payload_is_complete_and_account_neutral(self) -> None:
        accounts = (
            "10000000-0000-0000-0000-000000000001",
            "10000000-0000-0000-0000-000000000002",
            "10000000-0000-0000-0000-000000000003",
            "10000000-0000-0000-0000-000000000004",
        )
        with patch.object(
            supabase_client,
            "call_rpc",
            return_value={"ok": True, "binding_valid": True},
        ) as rpc:
            for index, account_id in enumerate(accounts, start=1):
                control_id = f"40000000-0000-0000-0000-{index:012d}"
                request_id = f"30000000-0000-0000-0000-{index:012d}"
                run_id = f"20000000-0000-0000-0000-{index:012d}"
                worker_sha = f"{index:x}" * 40
                result = supabase_client.bind_follow_60s_canary_runtime_v2(
                    control_id=control_id,
                    account_id=account_id,
                    expected_worker_sha=worker_sha,
                    baseline_release_sha=worker_sha,
                    request_id=request_id,
                    run_id=run_id,
                    attempt_id=index,
                    business_session_id=f"session-{index}",
                    binding_version="FOLLOW_60S_CANARY_BINDING_V2",
                )
                self.assertTrue(result["binding_valid"])
                name, payload = rpc.call_args.args
                self.assertEqual(name, "bind_follow_60s_canary_runtime_v2")
                self.assertEqual(
                    payload,
                    {
                        "p_control_id": control_id,
                        "p_account_id": account_id,
                        "p_expected_worker_sha": worker_sha,
                        "p_baseline_release_sha": worker_sha,
                        "p_run_request_id": request_id,
                        "p_run_id": run_id,
                        "p_attempt_id": index,
                        "p_business_session_id": f"session-{index}",
                        "p_binding_version": "FOLLOW_60S_CANARY_BINDING_V2",
                    },
                )


if __name__ == "__main__":
    unittest.main()
