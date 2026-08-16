#!/usr/bin/env python3
"""Fail closed when a commit stages protected Follow60 bytes.

An authorized commit must carry a detached Liam signature over a fixed
authorization document.  The document binds HEAD, the exact staged binary diff
(excluding the authorization and its signature), the requested files and an
expiry.  There is no environment-variable bypass.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from follow60_change_protocol_v1 import verify_staged_final_approval  # noqa: E402
from follow60_lock_v3 import sha256_bytes, verify_detached_signature  # noqa: E402

MANIFEST = ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json"
AUTH = ROOT / "docs/governance/FOLLOW60_LOCK_COMMIT_AUTHORIZATION_V3_1.json"
SIG = ROOT / "docs/governance/FOLLOW60_LOCK_COMMIT_AUTHORIZATION_V3_1.sig"
PUBLIC_KEY = ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_1_PUBLIC_KEY.pem"
AUTH_PATHS = {
    str(AUTH.relative_to(ROOT)),
    str(SIG.relative_to(ROOT)),
}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def staged_paths() -> list[str]:
    return sorted(set(git("diff", "--cached", "--name-only", "--diff-filter=ACMRD").decode().splitlines()))


def staged_payload(paths: list[str]) -> bytes:
    included = [path for path in paths if path not in AUTH_PATHS]
    if not included:
        return b""
    return git("diff", "--cached", "--binary", "HEAD", "--", *included)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    protected = set((manifest.get("protected_entries") or {}).keys())
    staged = staged_paths()
    protected_changed = sorted(protected.intersection(staged))
    if not protected_changed:
        return 0
    manifest_approval = manifest.get("approval") or {}
    if manifest_approval.get("final_diff_hash"):
        result = verify_staged_final_approval(ROOT, MANIFEST, ROOT / "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.sig", PUBLIC_KEY)
        if not result.get("ok"):
            print(
                f"CAN_COMMIT_CHANGED_FOLLOW60=NO reason={result.get('reason')}",
                file=sys.stderr,
            )
            return 3
        print("FOLLOW60_SIGNED_FINAL_MANIFEST_COMMIT_AUTHORIZATION=PASS")
        return 0
    try:
        authorization = json.loads(AUTH.read_text(encoding="utf-8"))
    except Exception:
        print("CAN_COMMIT_CHANGED_FOLLOW60=NO reason=signed_commit_authorization_missing", file=sys.stderr)
        return 3
    signed, reason = verify_detached_signature(AUTH, SIG, PUBLIC_KEY)
    if not signed:
        print(f"CAN_COMMIT_CHANGED_FOLLOW60=NO reason={reason}", file=sys.stderr)
        return 3
    if authorization.get("schema") != "FOLLOW60_SIGNED_COMMIT_AUTHORIZATION_V3_1":
        print("CAN_COMMIT_CHANGED_FOLLOW60=NO reason=authorization_schema_invalid", file=sys.stderr)
        return 3
    if authorization.get("base_sha") != git("rev-parse", "HEAD").decode().strip():
        print("CAN_COMMIT_CHANGED_FOLLOW60=NO reason=authorization_base_mismatch", file=sys.stderr)
        return 3
    if sorted(authorization.get("files_requested") or []) != [path for path in staged if path not in AUTH_PATHS]:
        print("CAN_COMMIT_CHANGED_FOLLOW60=NO reason=authorization_scope_mismatch", file=sys.stderr)
        return 3
    if authorization.get("diff_sha256") != sha256_bytes(staged_payload(staged)):
        print("CAN_COMMIT_CHANGED_FOLLOW60=NO reason=authorization_diff_mismatch", file=sys.stderr)
        return 3
    try:
        expires = datetime.fromisoformat(str(authorization.get("expires_at") or "").replace("Z", "+00:00"))
    except ValueError:
        print("CAN_COMMIT_CHANGED_FOLLOW60=NO reason=authorization_expiry_invalid", file=sys.stderr)
        return 3
    if datetime.now(timezone.utc) > expires.astimezone(timezone.utc):
        print("CAN_COMMIT_CHANGED_FOLLOW60=NO reason=authorization_expired", file=sys.stderr)
        return 3
    print("FOLLOW60_SIGNED_COMMIT_AUTHORIZATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
