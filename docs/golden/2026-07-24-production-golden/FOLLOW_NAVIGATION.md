# Follow navigation — f93c501 on the Golden baseline

## Provenance

- source patch: `f93c501c334b6c5b87ab04b72759bc60a452f5a8`;
- destination baseline: `abf0ebf90bcccd063355b41505d4d6541e870047`;
- consolidated commit: `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f`;
- source/destination stable patch ID:
  `a73d7a2be77ab917088f84d692fb572e694d3e52`.

All nine destination blobs are identical to the source patch blobs. No
reimplementation or Auto Login adaptation was required.

## Six locked features

### 1. Smooth Follow scroll

Follow uses a viewport-relative gesture: approximately 70% to 46% vertical,
0.32 seconds, followed by 0.45 seconds settle. It replaces coarse multi-step
scrolling on the nominal continuation path.

### 2. Viewport continuity

Forward progress requires both new rows and positional continuity: a suffix of
the previous viewport must equal the prefix of the next. Arbitrary username
overlap is insufficient. Fingerprints are truncated SHA-256 values; raw
usernames/XML are not emitted.

### 3. `See more`

An actionable `See more` before Suggestions means the primary list can continue.
Semantic selectors, click attempts and settle probes are bounded. It is not
target exhaustion.

### 4. True Suggestions boundary

`SUGGESTIONS_BOUNDARY_CONFIRMED` requires:

- no unprocessed primary rows;
- no actionable `See more`;
- no loading or ambiguous surface;
- Suggestions visible;
- a second fresh confirmation probe.

### 5. CT rotation

Terminal navigation outcomes are evaluated before completed-follow totals. A
true boundary can rotate to the next CT while preserving already verified
follows. `See more`, ambiguity or loading cannot rotate.

### 6. Shared contract

`instagram_list_continuation.py` supplies the classifier to:

- Follow for executable continuation/rotation;
- Welcome DM for follower-vs-Suggestions classification;
- Unfollow for read-only continuation observation.

Welcome and Unfollow business policies, caps and action decisions remain
independent.

## Contract states

- `PRIMARY_ROWS_AVAILABLE`
- `EXPAND_PRIMARY_LIST_AVAILABLE`
- `SUGGESTIONS_BOUNDARY_CONFIRMED`
- `NO_PROGRESS`
- `AMBIGUOUS_SURFACE`

Excessive movement permits one corrective backstep, then fails safely. Loading
after valid rows is ambiguity, not exhaustion.

## Files in the consolidation

- `account_session_orchestrator.py`
- `instagram_list_continuation.py`
- `instagram_navigation.py`
- `runner.py`
- redacted continuation fixture and contract test
- Welcome Suggestions boundary test
- `unfollow_session_orchestrator.py`
- `welcome_scan_producer.py`

No historical Auto Login, dispatcher, preflight, config, scheduler, warmup,
quota or runtime-control file changed.

## Certification

- patch-specific suites: `224/224`;
- Follow: `490/490`;
- Welcome: `125/125`;
- Unfollow: `49/49`;
- runner: `8/8`;
- navigation: `38/38`;
- Golden Follow: `8/8`;
- full Worker: `1850/1850`.

The behavior is active in production. No physical navigation replay was
performed during this mission; that remains a separate canary decision.
