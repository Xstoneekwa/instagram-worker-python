# Follow60 Mainline restored scope and Lock V3

Status: source certified; field certification pending natural runs and a new
explicit Liam approval.

## Proven baseline

- Annotated production tag: `follow60-v2-mainline-production-2026-08-09`
- Tag commit: `32a83949f84713a4dbd1e3b4491a1ebc88745b16`
- Approved source recorded by that tag: `55a039ae14a2111c6c48261094e08f3fe00def01`
- Field-certified Worker: `6701b2dfc661ae98d97de9b2da2ff03dfc93ecce`
- Immutable checkpoint release:
  `/Users/admin/phonefarm-worker-releases/32a8394-follow60-v2-mainline-v1`
- Reference natural run: `cb7dcc07-b3bb-46cf-ac54-ae37cdfde8d7`
- Broken descendant excluded: `724699d8ce85aa66e01e3ef60b5885ff9a539a34`

The locked release proves the immutable contract and approval. The reference
run proves the physical field baseline. The new release must not be called a
new Golden until natural field evidence and another explicit approval exist.

## Post-lock delta matrix

| Commit | Purpose | Classification | Disposition | Reason |
| --- | --- | --- | --- | --- |
| `774e4c4` | separate promoted/canonical CT depth | A | KEEP | CT Resume correctness, independent of the regression |
| `1b76d0c` | apply social memory before exact profile open | A | KEEP | safety boundary with existing regression coverage |
| `9c306fe` | Unfollow diagnostic contract | A | KEEP | non-Follow behavior preserved inside shared orchestration |
| `361b3fe` | inherit CT Resume enforcement | A | KEEP | generic future-account correctness |
| `1981980` | CT viewport reuse/private Unfollow | A/B | KEEP + REIMPLEMENT fast reacquisition | existing safety retained; cached fresh XML now avoids redundant full detection |
| `ce62812` | block private follows/unsafe restarts | A | KEEP | mandatory safety correction |
| `1fbcbca` | Unfollow certified-boundary reuse | A | KEEP | non-Follow performance path preserved |
| `9af66f5` | ads consent popup fail-closed | A | KEEP | generic pre-action safety |
| `d7b5428` | exact profile identity boundary | A | KEEP | prevents cross-surface/cross-profile action |
| `724699d` | visual veto before suggestion boundary | C | REJECT | caused six false `pre_scroll_continuation_ambiguous` terminal failures |
| new approved delta | XML-positive pre-scroll decision and See More before scroll | B | REIMPLEMENT | restores positive-row continuation without blind scrolling |
| new approved delta | first safe stop reason wins | B | REIMPLEMENT | prevents CT rotation and secondary terminal reason overwrite |
| new approved delta | cached post-scroll XML CT reacquisition | B | REIMPLEMENT | zero extra UI/XML/screenshot/Vision on the safe path |

## Restored pre-scroll contract

`VALID_FOLLOWERS_LIST + PRIMARY_ROWS > 0 + NO_HARD_BOUNDARY` allows the
canonical scroll even when visual matching is inconclusive. A positive See
More/Suggested boundary blocks scrolling. Unknown or wrong surfaces remain
fail-closed. The decision reuses fresh XML and adds no happy-path screenshot or
Vision request.

## CT Resume performance gate

- Field baseline P50: 5582 ms
- Field baseline P95: 6207 ms
- Cached-XML fixture candidate P50: 0.143 ms
- Cached-XML fixture candidate P95: 0.261 ms
- Candidate improvement: 99.997%
- Extra UI actions/XML dumps/screenshots/Vision: 0/0/0/0

The candidate measurements isolate reacquisition from an already-fresh
post-scroll hierarchy. They are a source/fixture gate, not post-deployment
field certification.

## Validation

- Restored/forward-port targeted suite: 360/360 PASS
- Promotion matrix: 218/218 PASS
- Full Worker suite: 3088 PASS, 10 skipped
- Lock V3 self-tests: 7/7 PASS
- Python compile: PASS
- Diff check: PASS
- Secret scan: PASS (only environment reads, examples, and synthetic tests)
- Performance gate: PASS

## Lock V3

Lock V3 protects 39 files by exact Git blob SHA-256 at `HEAD`, binds the
manifest to a separate consumed-once approval record, runs the historical
promotion matrix, and fails closed at runtime before any Instagram device
action on a contract or disk mismatch. Candidate generation cannot approve or
promote itself.

Remote branch/tag protection must be reported separately from these local,
CI, package, and runtime gates. It is not certified until the GitHub settings
are read back successfully.
