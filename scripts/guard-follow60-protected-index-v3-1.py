#!/usr/bin/env python3
"""Candidate creation only. External signed Approval 1 + exact staged freeze.
No final signature is possible/required before the commit exists.
"""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from follow60_change_protocol_v1 import verify_candidate_commit
from follow60_candidate_identity_v2 import git

def main():
    try:
        directory = Path(git(ROOT, "config", "--get", "follow60.candidateEvidenceDir").decode().strip())
        public = ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_1_PUBLIC_KEY.pem"
        result = verify_candidate_commit(
            ROOT, directory / "approval-1.json", directory / "approval-1.sig",
            public, directory / "freeze.json")
    except (ValueError, OSError, subprocess.CalledProcessError):
        result = {"ok": False, "reason": "external_candidate_evidence_missing"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 3

if __name__ == "__main__":
    raise SystemExit(main())
