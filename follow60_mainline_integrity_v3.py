"""Fail-closed runtime integrity gate for FOLLOW60_V2_MAINLINE.

The manifest is approved out-of-band.  This module never writes or updates it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from follow60_lock_v3 import verify_runtime
from runtime_artifacts import follow60_certification_paths


MANIFEST_RELATIVE_PATH = "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.json"
EXPECTED_SCHEMA = "FOLLOW60_MAINLINE_LOCK_V3"
EXPECTED_ENGINE = "FOLLOW60_V2_MAINLINE_V1"
EXPECTED_RUNTIME_MODE = "mainline"
EXPECTED_BINDING_KIND = "mainline"
SIGNATURE_RELATIVE_PATH = "docs/governance/FOLLOW60_MAINLINE_LOCK_V3.sig"
PUBLIC_KEY_RELATIVE_PATH = "docs/governance/FOLLOW60_MAINLINE_LOCK_V3_1_PUBLIC_KEY.pem"


def verify_runtime_integrity(
    root: Path,
    *,
    runtime_mode: str,
    binding_kind: str,
    engine: str,
    manifest_path: Path | None = None,
    signature_path: Path | None = None,
) -> dict[str, Any]:
    """Verify the canonical signed manifest before any Follow60 UI action."""
    root = Path(root).resolve()
    if manifest_path is None and signature_path is None:
        path, signature = follow60_certification_paths(root)
    else:
        path = Path(manifest_path or (root / MANIFEST_RELATIVE_PATH)).resolve()
        signature = Path(signature_path or (root / SIGNATURE_RELATIVE_PATH)).resolve()
    return verify_runtime(
        root,
        runtime_mode=runtime_mode,
        binding_kind=binding_kind,
        engine=engine,
        manifest_path=path,
        signature_path=signature,
        public_key_path=root / PUBLIC_KEY_RELATIVE_PATH,
    )
