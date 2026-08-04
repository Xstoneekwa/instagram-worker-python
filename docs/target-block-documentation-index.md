# Future Target block documentation index

The next Target block must inherit, without reimplementation:

1. `docs/follow60-mainline-v1.md` for the official Follow execution engine;
2. `FOLLOW60_MAINLINE_MANIFEST_V1.json` for immutable source provenance;
3. `docs/ct-resume-v4.md` for continuation and checkpoint authority;
4. `docs/target-followers-progressive-resume-v2.md` for the historical design;
5. `docs/follow60-mainline-rollback.md` for emergency rollback boundaries.

Target selection may provide candidates. It may not bypass Follow60 safety,
invent a second Follow engine, or treat CT Resume persisted depth as UI truth.

