#!/bin/zsh
set -eu

target="${1:?release path required}"
tag="follow60-mainline-production-2026-08-04"
root="$(cd "$(dirname "$0")/.." && pwd)"

/usr/bin/python3 "$root/scripts/verify-follow60-mainline-lock.py"
tag_sha="$(git -C "$root" rev-parse "$tag^{commit}")"
target_sha="$(git -C "$target" rev-parse HEAD)"
[[ "$target_sha" = "$tag_sha" ]] || {
  print -u2 "follow60_uncertified_release:$target_sha expected:$tag_sha"
  exit 2
}
print "FOLLOW60_CERTIFIED_RELEASE_OK:$target_sha"
