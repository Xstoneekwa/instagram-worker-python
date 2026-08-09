# Follow60 Mainline operator runbook

Normal V2 operation requires no canary control, allowlist, lease or evaluation
barrier. Before a release switch: require a zero global gate, verify the
certified tag, prepare startup-skip at the shared runtime path only, switch once,
restart once and verify root/SHA/process/gate health. Never create a test run or
manual tick as part of deployment. Resolve only non-security incidents through
their canonical reviewed/resolved path; security ambiguity remains fail-closed.

Canary experiments require an explicit account-scoped control and separate GO.
V2 rollback uses `scripts/follow60-v2-mainline-rollback-dry-run.sh` first. It
targets the verified V1 source, not an ad-hoc reconstruction and not a deletion
of historical receipts.
