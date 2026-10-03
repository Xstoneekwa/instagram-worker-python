"""Canonical paths for mutable Phone Farm runtime artifacts.

Runtime evidence must never be written below a checked-out or sealed Worker
release.  This module is deliberately dependency-free so every Worker and
control-plane entrypoint can share the same root without import cycles.
"""

from __future__ import annotations

import os
from pathlib import Path


FOLLOW60_MANIFEST_NAME = "FOLLOW60_MAINLINE_LOCK_V3.json"
FOLLOW60_SIGNATURE_NAME = "FOLLOW60_MAINLINE_LOCK_V3.sig"


def runtime_home() -> Path:
    configured = str(
        os.environ.get("PHONEFARM_RUNTIME_ROOT")
        or os.environ.get("PHONEFARM_RUNTIME_HOME")
        or (Path.home() / "phonefarm-runtime")
    ).strip()
    result = Path(configured).expanduser().resolve()
    release_value = str(os.environ.get("WORKER_RUNTIME_ROOT") or "").strip()
    if release_value:
        release_root = Path(release_value).expanduser().resolve()
        try:
            result.relative_to(release_root)
        except ValueError:
            pass
        else:
            raise RuntimeError("runtime_home_inside_immutable_release")
    return result


def runtime_logs_root() -> Path:
    return runtime_home() / "logs"


def runtime_artifact_path(*parts: str) -> Path:
    """Return a path below the canonical runtime logs root, fail-closed.

    Components must be relative single path elements.  This rejects absolute
    paths, traversal, and ambiguous empty components before any directory or
    artifact is created.
    """
    if not parts:
        raise ValueError("runtime_artifact_path_missing_component")
    clean: list[str] = []
    for raw in parts:
        value = str(raw or "")
        candidate = Path(value)
        if (
            not value
            or candidate.is_absolute()
            or len(candidate.parts) != 1
            or value in {".", ".."}
        ):
            raise ValueError("runtime_artifact_path_invalid_component")
        clean.append(value)
    root = runtime_logs_root().resolve()
    result = root.joinpath(*clean).resolve()
    try:
        result.relative_to(root)
    except ValueError as exc:
        raise ValueError("runtime_artifact_path_outside_runtime_root") from exc
    return result


def deployment_evidence_dir(candidate_sha: str) -> Path:
    sha = str(candidate_sha or "").strip().lower()
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError("deployment_evidence_candidate_sha_invalid")
    root = (runtime_home() / "deployment-evidence").resolve()
    result = (root / sha).resolve()
    try:
        result.relative_to(root)
    except ValueError as exc:
        raise ValueError("deployment_evidence_path_outside_runtime_root") from exc
    return result


def historical_auto_login_runtime_root() -> Path:
    """External mutable root used by the isolated historical login engine."""

    return (runtime_home() / "historical-auto-login").resolve()


def git_commit_sha(root: Path) -> str:
    """Resolve one checked-out commit without accepting inherited Git config."""
    import subprocess

    root = Path(root).resolve(strict=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1")
    return subprocess.check_output(
        [
            "git", "-c", "safe.directory=", f"-c", f"safe.directory={root}",
            "-C", str(root), "rev-parse", "--verify", "HEAD^{commit}",
        ],
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    ).strip()


def follow60_certification_paths(root: Path) -> tuple[Path, Path]:
    """Prefer SHA-bound external sidecars; retain legacy installed releases.

    A newly assembled sealed release contains only its committed Git tree. Its
    signed manifest and signature live below the canonical runtime home and are
    selected solely by the release's exact commit SHA. Existing releases keep
    working through the local-path fallback until replaced canonically.
    """
    root = Path(root).resolve(strict=True)
    sha = git_commit_sha(root)
    external = deployment_evidence_dir(sha)
    generation = str(os.environ.get("PHONEFARM_FOLLOW60_GENERATION") or "").strip()
    if generation:
        generation_path = Path(generation)
        if (
            generation_path.is_absolute()
            or len(generation_path.parts) != 1
            or generation in {".", ".."}
            or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for ch in generation)
        ):
            raise RuntimeError("follow60_generation_selector_invalid")
        external = (external / generation).resolve()
        try:
            external.relative_to(deployment_evidence_dir(sha))
        except ValueError as exc:
            raise RuntimeError("follow60_generation_selector_outside_sha") from exc
        if not external.is_dir():
            raise RuntimeError("follow60_generation_missing")
        manifest = external / FOLLOW60_MANIFEST_NAME
        signature = external / FOLLOW60_SIGNATURE_NAME
        if not manifest.is_file() or not signature.is_file():
            raise RuntimeError("follow60_generation_pair_incomplete")
        return manifest.resolve(), signature.resolve()
    manifest = external / FOLLOW60_MANIFEST_NAME
    signature = external / FOLLOW60_SIGNATURE_NAME
    # Once any SHA-bound external evidence exists, it is the sole authority.
    # An incomplete/corrupt publication must fail verification; it must never
    # silently fall back to a stale release-local sidecar.
    if external.exists() or manifest.exists() or signature.exists():
        return manifest, signature
    governance = root / "docs" / "governance"
    return governance / FOLLOW60_MANIFEST_NAME, governance / FOLLOW60_SIGNATURE_NAME
