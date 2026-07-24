# Test certification

## Golden Worker result

Business commit: `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f`.

| Suite | Result | Duration captured |
|---|---:|---:|
| f93c501 targeted patch suites | 224/224 | original patch certification |
| Follow family | 490/490 | 6.993 s |
| Welcome family | 125/125 | 9.463 s |
| Unfollow family | 49/49 | 0.135 s |
| Runner | 8/8 | 0.160 s |
| Navigation | 38/38 | 1.985 s |
| Golden Follow | 8/8 | 0.399 s |
| Auto Login 07ee/integration | 499/499 | 248.448 s |
| Dispatcher/preflight | 96/96 | 0.582 s |
| Full Worker discovery | 1850/1850 | 269.908 s |

Suite selections overlap and must not be summed. Full discovery is the canonical
whole-repository total.

## Static/perimeter gates

- Python compilation: PASS;
- `git diff --check`: PASS;
- no-leak/secret-shaped scan: PASS;
- exact nine-file navigation allowlist: PASS;
- source and destination patch ID match: PASS;
- no historical Auto Login, dispatcher/preflight, config, scheduler, warmup,
  quota or runtime-control diff: PASS;
- remote integration branch SHA matched local commit: PASS.

## Release activation evidence

- immutable release HEAD matched `e7f54a9` and was clean;
- symlink switched atomically once;
- dispatcher restarted once for activation;
- 13 dispatcher and 13 heartbeat samples over 12 minutes 16 seconds were healthy;
- dispatcher PID was stable throughout that activation window;
- `runtimeRootOk=true`, process count one, duplicate false, queue zero;
- request totals `176 → 176`, run totals `144 → 144`, active locks zero;
- no phone, ADB, Instagram or Auto Login action during activation.

## Documentation checkpoint validation

This Golden docs commit must additionally pass:

- staged files under `docs/**` only;
- all relative Markdown links resolve;
- no secret-shaped or raw credential content;
- no source/config/migration/package diff;
- read-only request/run totals unchanged;
- runtime symlink and release commit unchanged;
- no dispatcher restart/deployment/device action.

## Physical evidence boundary

Offline tests prove deterministic code contracts, not live Instagram behavior.
The feature matrix explicitly marks which paths were physically validated,
partially observed or pending. No physical test was performed to create this
checkpoint.
