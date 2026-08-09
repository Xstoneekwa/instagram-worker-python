# Follow60 V2 field performance baseline

Canonical terrain run: `cb7dcc07-b3bb-46cf-ac54-ae37cdfde8d7`, Worker
`6701b2dfc661ae98d97de9b2da2ff03dfc93ecce`, 10/10 Follows and 10/10 Likes.

| Metric | Canonical value | Mainline alert policy |
|---|---:|---|
| Pure V2 candidate to candidate median | 50.280 s | investigate sustained drift above 62 s |
| Overall candidate to candidate median | 50.586 s | investigate sustained drift above 62 s |
| Profile to CT median | 41.991 s | investigate sustained drift above 50 s |
| Profile to post tap median | 4.588 s | investigate sustained drift above 8 s |
| Return CT start to ACK median | 5.431 s | investigate sustained drift above 9 s |

These values are warning baselines, not hard runtime gates. Safety remains
fail-closed. Two persistent ambiguities correctly used Golden after one bounded
micro-revalidation; this residual policy is accepted and must not be removed to
chase a synthetic benchmark.
