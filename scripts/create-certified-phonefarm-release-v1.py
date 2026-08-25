#!/usr/bin/env python3
"""Create one release only after the exact-candidate Follow60 gate passes."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from follow60_deployment_gate_v1 import (  # noqa: E402
    MANIFEST_RELATIVE, SIGNATURE_RELATIVE, verify_deployment_candidate,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--signature", required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve(strict=True)
    release = Path(args.release).resolve()
    releases_root = Path("/Users/admin/phonefarm-worker-releases").resolve()
    try:
        release.relative_to(releases_root)
    except ValueError:
        raise SystemExit("release_outside_canonical_root")
    if release.exists() or release.is_symlink():
        raise SystemExit("release_target_already_exists")
    if _git(source, "status", "--porcelain"):
        raise SystemExit("candidate_worktree_not_clean")
    admission = verify_deployment_candidate(
        source,
        manifest_path=Path(args.manifest),
        signature_path=Path(args.signature),
    )
    if not admission.get("ok"):
        raise SystemExit(f"pre_release_follow60_gate_failed:{admission.get('reason')}")
    subprocess.check_call(["git", "clone", "--no-hardlinks", str(source), str(release)])
    destination_manifest = release / MANIFEST_RELATIVE
    destination_signature = release / SIGNATURE_RELATIVE
    destination_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(args.manifest), destination_manifest)
    shutil.copyfile(Path(args.signature), destination_signature)
    post = verify_deployment_candidate(release)
    if not post.get("ok"):
        raise SystemExit(f"created_release_follow60_gate_failed:{post.get('reason')}")
    print(f"CERTIFIED_RELEASE_CREATED={release} SHA={post['candidate_sha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

