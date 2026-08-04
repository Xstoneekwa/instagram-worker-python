#!/bin/zsh
set -eu

target="/Users/admin/phonefarm-worker-releases/ff99db6-follow-persistence-rpc-v1"
expected_sha="ff99db6d7de48d75ede439c704e770feaaec6b7c"
mode="${1:---dry-run}"

actual_sha="$(git -C "$target" rev-parse HEAD)"
[[ "$actual_sha" = "$expected_sha" ]] || {
  print -u2 "golden85_sha_mismatch:$actual_sha"
  exit 2
}

print "target=$target"
print "sha=$actual_sha"
print "required_gate=account_run_requests:0,ig_runs:0,device_locks:0,tick_locks:0"
print "required_actions=startup_skip,switch_release,exactly_one_canonical_restart,postcheck"

if [[ "$mode" = "--dry-run" ]]; then
  print "ROLLBACK_DRY_RUN_READY"
  exit 0
fi

[[ "$mode" = "--execute" ]] || { print -u2 "usage: $0 [--dry-run|--execute]"; exit 2; }
[[ "${FOLLOW60_GOLDEN85_ROLLBACK_GO:-}" = "EXPLICIT_GO" ]] || {
  print -u2 "explicit_rollback_go_required"
  exit 2
}
print -u2 "execution intentionally delegated to the canonical runtime runbook after live gate verification"
exit 2
