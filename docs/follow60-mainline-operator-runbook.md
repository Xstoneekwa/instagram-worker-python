# Follow60 Mainline operator runbook

Normal operation requires no canary control. Before a release switch: require a
zero global gate, verify the certified tag, prepare startup-skip, switch once,
restart once and verify root/SHA/process/gate health. Never create a test run or
manual tick as part of deployment. Resolve only non-security incidents through
their canonical reviewed/resolved path; security ambiguity remains fail-closed.

Canary experiments require an explicit account-scoped control and separate GO.
Rollback uses `scripts/rollback-follow60-to-golden85.sh --dry-run` first.

