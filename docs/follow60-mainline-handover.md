# Follow60 Mainline handover

Official prepared reference: `FOLLOW60_V2_MAINLINE_V1`.

- Manifest: `FOLLOW60_V2_MAINLINE_MANIFEST_V1.json`
- Checkpoint: `PROJECT_CHECKPOINTS.md`
- Lock: `scripts/verify-follow60-mainline-lock-v2-1.py`
- Release verifier: `scripts/verify-follow60-certified-release.sh`
- Rollback dry-run: `scripts/follow60-v2-mainline-rollback-dry-run.sh`
- Restore dry-run: `scripts/follow60-v2-mainline-restore-dry-run.sh`
- CT Resume: `docs/ct-resume-v4.md`
- Future Target index: `docs/target-block-documentation-index.md`

Normal V2 mainline is not the former canary with flags disabled: it has a
dedicated business-session carrier and no control/allowlist/barrier dependency.
V1 stays a verified rollback; Golden stays an internal fail-closed fallback.

Do not claim CT Resume second-pass field completion until a natural run exposes
and certifies that opportunity.
