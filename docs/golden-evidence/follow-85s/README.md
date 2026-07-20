# Follow 85s Performance Golden V1

`FOLLOW_85S_PERFORMANCE_GOLDEN_V1` locks the physically validated Follow cycle
from Mythyl run `9597985e-d357-4a44-a3d5-a6f0457ca05e` on immutable runtime
`ff99db6d7de48d75ede439c704e770feaaec6b7c`.

## Canonical result

| Measure | Result |
|---|---:|
| Verified cycles | 20 / 20 |
| Strict candidate cycle | 77.205 s mean |
| Point 3 without scroll | 6.954 s mean |
| Candidate to candidate | 85.226 s mean |
| Throughput | 42.24 follows/hour |

The candidate-to-candidate value is computed from one candidate profile open
to the next candidate profile open. It includes the strict Follow/Mute/Like/
Return CT cycle and the transition to the next candidate. Point 3 measures CT
stable to the next candidate; its canonical value excludes the two transitions
that intentionally performed a soft scroll.

## Evidence

- Raw production logs remain at their recorded immutable release paths and are
  verified by SHA-256 in [`manifest.json`](./manifest.json).
- Sanitized event extracts in [`sanitized-logs/`](./sanitized-logs/) contain
  aliased candidates, event timing, aggregate metrics, and source hashes.
- A read-only external archive is stored at
  `/Users/admin/phonefarm-golden-evidence/follow-85s-v1/`.
- Runtime-generated `__pycache__` content is excluded from every checkpoint and
  archive artifact.

The raw log was recovered, so the canonical result is direct physical evidence,
not a reconstructed substitute. The verifier still recognizes explicitly
marked reconstructed evidence and reports that lower evidence class.

## Change policy

The annotated tag is `golden-follow-85s-v1`. Changes to a protected cycle file
require explicit approval through `--allow-worker-golden-touch`, a physical
comparison against 85.226 s, and a V2 manifest if a new baseline is accepted.
V1 must never be rewritten.

Use:

```bash
python3 scripts/verify_follow_85s_golden.py verify-runtime
python3 scripts/verify_follow_85s_golden.py verify-evidence
python3 scripts/verify_follow_85s_golden.py compare-run --log /path/to/run.log
python3 scripts/verify_follow_85s_golden.py rollback-plan
```
