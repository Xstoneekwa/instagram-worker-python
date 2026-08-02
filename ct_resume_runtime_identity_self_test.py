"""No-device end-to-end self-test for Worker identity and CT Resume V4.

The test deliberately crosses two subprocess boundaries and changes cwd before
the runner stage.  It exercises the real wrapper-exported identity, the real
consumer propagation helper, the real runner provenance validator, and the
real CT Resume checkpoint/lease controller with an in-memory RPC fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from worker_runtime_identity import (
    bind_worker_runtime_identity,
    export_worker_runtime_identity,
    resolve_worker_runtime_identity,
)


ACCOUNT_ID = "0d299d1e-46ee-49d2-8a84-4f928f2bb182"
TARGET_ID = "8a463af9-ec4f-4b4e-93b2-afbf4f9b8ca4"
TARGET_USERNAME = "tech_immo_services"
REQUEST_ID = "66f92055-21ee-4a7c-a042-3a857d3f8448"
RUN_ID = "9bde8e78-7955-46d5-bd29-044548d2a911"
ATTEMPT_ID = 1
HMAC_SECRET = "ct-resume-runtime-identity-self-test-v5-secret"


class _FixtureRpc:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.version = int(row["optimistic_version"])
        self.calls: list[str] = []

    def __call__(self, name: str, _params: dict[str, Any]) -> Any:
        self.calls.append(name)
        if name == "get_target_followers_resume_checkpoint_v3":
            return dict(self.row)
        if name == "claim_target_followers_resume_checkpoint_v3":
            self.version += 1
            return {
                "ok": True,
                "reason": "claimed",
                "optimistic_version": self.version,
                "lease_expires_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=10)
                ).isoformat(),
            }
        if name == "release_target_followers_resume_checkpoint_v3":
            self.version += 1
            return {
                "ok": True,
                "reason": "released",
                "optimistic_version": self.version,
            }
        return {"ok": False, "reason": "self_test_rpc_not_allowed"}


def _runtime_root() -> Path:
    return Path(__file__).resolve().parent


def _checkpoint_row(resume: Any) -> dict[str, Any]:
    visible = ["tech.anchor.one", "tech.anchor.two", "tech.anchor.three"]
    anchors = list(resume.bounded_anchor_hashes(visible, secret=HMAC_SECRET))
    return {
        "id": "e7e1e980-c872-463d-96cf-8684370634bf",
        "account_id": ACCOUNT_ID,
        "target_id": TARGET_ID,
        "surface": "followers",
        "checkpoint_version": 4,
        # This mirrors the real tech_immo_services shape that prompted the
        # audit: depth zero can still carry a valid evaluated-prefix anchor.
        "last_safe_depth": 0,
        "last_safe_anchor": anchors[-1],
        "anchor_fingerprint": resume.viewport_fingerprint(
            visible, secret=HMAC_SECRET
        ),
        "last_visible_anchor_hashes": anchors,
        "shadow_last_safe_depth": 0,
        "shadow_last_safe_anchor": anchors[-1],
        "shadow_anchor_fingerprint": resume.viewport_fingerprint(
            visible, secret=HMAC_SECRET
        ),
        "shadow_visible_anchor_hashes": anchors,
        "status": "active",
        "optimistic_version": 7,
        "last_run_id": "e8ee40ea-40ca-47d9-9616-fe8609fc0c76",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_instagram_version": "self-test",
        "invalidation_reason": "",
        "lease_owner_run_id": "",
        "lease_mode": "",
        "lease_expires_at": None,
        "lease_heartbeat_at": None,
        "lease_generation": 0,
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
        "end_reached": False,
    }


def _consumer_stage() -> int:
    import account_run_request_consumer as consumer

    identity = resolve_worker_runtime_identity(_runtime_root())
    identity = bind_worker_runtime_identity(identity, consumer_pid=os.getpid())
    consumer._CERTIFIED_RUNTIME_IDENTITY = identity
    child_env = dict(os.environ)
    child_env.update(consumer._runner_runtime_identity_env(REQUEST_ID))
    # These legacy/mutable hints are intentionally absent.  The transported
    # canonical identity must be sufficient from a cwd outside the release.
    child_env.pop("GIT_SHA", None)
    child_env.pop("PHONEFARM_ACTIVE_ROOT", None)
    child_env.pop("PHONEFARM_ACTIVE_COMMIT", None)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--stage",
        "runner",
    ]
    completed = subprocess.run(
        command,
        cwd=tempfile.gettempdir(),
        env=child_env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        if completed.stdout:
            print(completed.stdout, file=sys.stderr)
        return completed.returncode or 1
    print(completed.stdout.strip())
    return 0


def _runner_stage() -> int:
    import runner
    import target_followers_progressive_resume_v2 as resume

    identity = resolve_worker_runtime_identity(_runtime_root())
    identity = bind_worker_runtime_identity(
        identity,
        request_id=REQUEST_ID,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
    )
    runner._CERTIFIED_RUNTIME_IDENTITY = identity
    export_worker_runtime_identity(identity)
    provenance, reason, diagnostics = runner._resolve_target_followers_resume_provenance(
        target_followers_resume_source_request_id=REQUEST_ID,
        auto_restart_resume_policy={"attempt_id": ATTEMPT_ID},
        worker_runtime_identity=identity,
        run_id=RUN_ID,
    )
    if provenance is None or reason != "provenance_resolved":
        print(
            json.dumps(
                {
                    "verdict": "CT_RESUME_RUNTIME_IDENTITY_SELF_TEST_FAILED",
                    "reason": reason,
                    "diagnostics": diagnostics,
                },
                sort_keys=True,
            )
        )
        return 2

    events: list[tuple[str, dict[str, Any]]] = []
    fixture_rpc = _FixtureRpc(_checkpoint_row(resume))
    controller = resume.build_runtime_controller(
        account_id=ACCOUNT_ID,
        target_id=TARGET_ID,
        target_username=TARGET_USERNAME,
        run_id=RUN_ID,
        source_request_id=provenance["source_request_id"],
        source_attempt_id=provenance["source_attempt_id"],
        release_sha=provenance["release_sha"],
        flags=resume.ResumeFlags(False, True, (ACCOUNT_ID,)),
        rpc_call=fixture_rpc,
        hmac_secret=HMAC_SECRET,
        emit=lambda event, payload: events.append((event, dict(payload))),
    )
    if controller is None:
        return 3
    plan = controller.load_and_plan()
    lease_claimed = controller.claim()
    observation = controller.observe_viewport(
        ["tech.anchor.one", "tech.anchor.two", "tech.anchor.three", "new.row"],
        followers_surface_confirmed=True,
        expected_target_confirmed=True,
        list_moved=True,
        recoverable=True,
    )
    anchor_event = next(
        (payload for event, payload in events if event == "anchor_found"), {}
    )
    lease_released = controller.release()
    result = {
        "verdict": "CT_RESUME_RUNTIME_IDENTITY_PROPAGATED_END_TO_END",
        "worker_sha": identity.worker_sha,
        "release_head": identity.release_head,
        "runtime_root_ok": identity.runtime_root_ok,
        "identity_source": identity.source,
        "wrapper_pid": identity.wrapper_pid,
        "consumer_pid": identity.consumer_pid,
        "runner_pid": os.getpid(),
        "request_id": identity.request_id,
        "run_id": identity.run_id,
        "attempt_id": identity.attempt_id,
        "runner_cwd_outside_release": Path.cwd() != _runtime_root(),
        "legacy_identity_hints_absent": not any(
            os.environ.get(name)
            for name in ("GIT_SHA", "PHONEFARM_ACTIVE_ROOT", "PHONEFARM_ACTIVE_COMMIT")
        ),
        "checkpoint_found": controller.checkpoint is not None,
        "checkpoint_loaded": controller.checkpoint is not None,
        "checkpoint_depth_before": controller.checkpoint_depth_before,
        "proposed_resume_depth": plan.planned_depth,
        "legacy_start_depth": controller.checkpoint_depth_before,
        "anchor_overlap_count": anchor_event.get("anchor_overlap_count"),
        "anchor_observation_reason": observation.reason,
        "lease_claimed": lease_claimed,
        "lease_released": lease_released,
        "rpc_calls": fixture_rpc.calls,
        "device_actions": 0,
        "db_writes": 0,
    }
    required = (
        result["worker_sha"] == result["release_head"],
        result["runtime_root_ok"],
        result["runner_cwd_outside_release"],
        result["legacy_identity_hints_absent"],
        result["checkpoint_found"],
        result["checkpoint_loaded"],
        result["anchor_overlap_count"] == 3,
        result["lease_claimed"],
        result["lease_released"],
    )
    if not all(required):
        result["verdict"] = "CT_RESUME_RUNTIME_IDENTITY_SELF_TEST_FAILED"
        print(json.dumps(result, sort_keys=True))
        return 4
    print(json.dumps(result, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("consumer", "runner"), default="consumer")
    args = parser.parse_args()
    return _consumer_stage() if args.stage == "consumer" else _runner_stage()


if __name__ == "__main__":
    raise SystemExit(main())
