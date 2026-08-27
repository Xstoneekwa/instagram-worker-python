# Status-only trust cache V1 — isolated candidate

Approval 1: `b5d01b92-d476-4785-b9e8-8b9ced201701`, base `b281d99`.
No activation, production installation, business action or old certificate reuse.
The prior batch Git/import traversal optimization remains byte-identical.

## Two distinct decisions

`verify_deployment_candidate` remains the complete uncached gate for deployment,
promotion and service/run admission. It does not import this cache module.
The wrapper selects `verify_status_trust` **only** when its first action is
`status`. Every other action retains the original full gate. BotApp continues
to use its stable controller; no direct service, ADB or process scanner is added.

A status trust result has `diagnostic_only=true`, `deployment_authorized=false`.
It contains no PID, running/stopped, heartbeat, scheduler or cached health.
The existing status path must still obtain its own live process evidence.

## Publication and storage

The explicit root-only installer verifies the active release's full signed
admission and two matching current snapshots under the existing external store
guard. It publishes exclusively using the physical V2 atomic immutable-record
writer, in `STORE/seals/status-trust-<key>.json`. No directory is added inside
the release. No record is overwritten. The unprivileged status reader never
creates or repairs records. Installation requires separate production authority.

The record stores exactly schema, identity, key and `full_verification=PASS`.
Its authenticity comes from the root-owned, immutable canonical store and
record metadata, not from a self-asserted boolean in a user-writable file.
The threat model inherits the existing physical lock's trust in privileged
root administration. This does not defend against a malicious root authority.

## Identity and current checks

`trust_key` hashes the existing `canonical_json` serialization of the identity.
It is NOT a new canonical Git diff implementation; Git delta remains V2.
Identity includes full active commit, tree, exact manifest/signature/public-key
hashes, registry active SHA/bytes/state, resolved root/device/inode/link target,
protected scope, import graph, local source namespace, physical seal, verifier
protocol bytes, physical protocol version and Python implementation/version.

Every hit performs two current snapshots. Each rechecks:

- canonical active link and immediate release root;
- actual Git commit/tree types and signed identity binding;
- exact signature, manifest and trust-anchor bytes;
- the existing clean-source/index check;
- current registry schema, VERIFIED status, active SHA and exact root;
- trusted external seal and the same OS physical metadata checks as activation;
- executing verifier protocol files included in the signed protected scope;
- actual protected file contents, size and executable mode;
- source namespace paths and bytes, including ignored `.py` modules;
- unchanged identities across the whole read.

The shared `_verify_physical_metadata` helper is NOT an admission gate. The
original public physical verifier still obtains full uncached admission before
calling it. The status reader instead binds these checks to its signed current
snapshot and the trusted immutable attestation. No activation call accepts a
caller-supplied cached verdict.

Namespace inspection does not follow symlink directories. A symlink in a
source directory conservatively rejects status trust. Dot directories and
`__pycache__` are outside the existing `.py` source resolver. Ordinary ignored
Python files are included, so a new module cannot silently alter resolution.
Fresh bytes are read, not trusted from mtime/size alone. Unknown inputs fail closed.

## Invalidation and failure

Any identity change means no hit. Invalid live signature, root, registry,
physical metadata or protected bytes fails immediately. Missing, corrupted,
wrong-key or untrusted cache data invokes the complete uncached verifier; it
never reuses the last positive verdict. The second snapshot must still match.
A valid cold verification may return diagnostic trust with `cache=MISS`;
that is a new full verification, not a false acceptance of the bad cache.

There is no TTL-only validity, environment path override or installer fallback.
Cold performance is measured separately and is never hidden from SLO reports.

## Verification and certification limits

`tests.test_follow60_status_trust_cache_v1` uses real Git commits, exact signed
manifests and synthetic Ed25519 keys. Only root/immutable OS metadata is modelled
in those unprivileged tests. It tests rejection of real unmodelled ownership/
immutable metadata too; that does not certify an actual root-owned installation.

Required challenges cover manifest, SHA, registry, signature, root exchange,
old copied cache, corruption, absence, same-size/mtime source tamper, ignored
imports, physical permissions, metadata rejection, protocol changes and races.
Deployment admission is tested independently of the status cache.

Warm/cold isolated timings are not an installed-runtime or full-controller
certificate. The production SLO remains P95 <=3 s without a larger timeout.
This candidate still needs final commit-bound certification, external signature,
physical installation evidence and separately authorized activation. Relay and
Golden UI remain outside this change.
