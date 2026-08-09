# FOLLOW60_V2_MAINLINE_V1

Source checkpoint: `55a039ae14a2111c6c48261094e08f3fe00def01`, parent
`e2aaa6f36469bae94459b16bc6e09505b21970a8`.

The normal Follow path now builds the V2 ordering binding directly from the
immutable business-session binding. It does not claim a canary control, inspect
an allowlist, acquire a canary lease, or apply the ten-cycle evaluation barrier.
The former behavioral canary remains isolated for explicit historical replay.

Certification:

- targeted mainline/Phase AA matrix: 158 tests PASS;
- complete Worker suite: 2849 tests PASS, 10 skipped, zero failures;
- true-mainline multi-candidate session: PASS;
- source compilation and diff check: PASS;
- Golden fail-closed fallback: preserved;
- V1 verified rollback source: `45de130d29a8658ff24f222595f1d2b9184716d9`.

Production activation, process identity, switch/restart counts and soak evidence
are recorded separately after the runtime gate. A source checkpoint alone is
not production proof.
