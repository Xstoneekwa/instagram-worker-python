# Comparison Guide

1. Preserve the candidate run log before analysis.
2. Run `compare-run` against that exact log.
3. Require balanced candidate-open, Follow-verified and Return-CT counts.
4. Compare strict cycle, Point 3, candidate-to-candidate and throughput.
5. Inspect scroll and recovery counts before attributing a timing difference to
   code.
6. Keep every identity, private-profile, Mute, Story/Facebook, Like verification
   and Return CT guard active.

```bash
python3 scripts/verify_follow_85s_golden.py compare-run --log /path/to/run.log
```

An improvement is not a new Golden merely because its mean is lower. It needs a
physical run, preserved raw evidence, unchanged safety invariants, and a new V2
manifest. A slower or incomplete run is not accepted.
