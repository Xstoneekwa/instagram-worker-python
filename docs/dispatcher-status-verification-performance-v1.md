# Dispatcher status verification performance V1

Scope: diagnostic verification only. `follow60_lock_v3.py` remains a read-only
verifier. No Worker business logic, resume contract, signature algorithm,
canonical delta serialization, controller or service launcher is changed.

Approval 1: externally signed request `18f79f7c-f7f3-4932-a533-3c77788ca5e4`,
based on `b281d99b246a794b3f24c29c150d4f7cba06c3d0`. This is not a deployment
approval. The candidate needs a final commit-bound manifest, complete
recertification and a new external signature before promotion is possible.

## Full verification, no trust cache

The old verifier spawns two Git processes for every protected file (344 for
172 entries). The new verifier reads one NUL-delimited recursive tree and one
length-delimited `cat-file --batch` stream. It checks object type/identity,
body lengths and separators, rejecting incomplete or trailing output. Each
entry retains exact path, blob OID, raw-byte SHA256, size and executable/symlink
mode. Missing/non-blob paths fail comparison. The verified commit SHA, not a
moving HEAD, selects the batch. Git configuration sanitization is unchanged.

Import parsing still parses every source file. Traversal visits all statement
containers (module, statements, exception handlers, match cases where the
Python version supports them). It does not descend expression subtrees, which
cannot contain Python import statements. Import resolution and the canonical
graph/hash remain unchanged. Full graph equivalence is tested against the
frozen implementation for the protected Python files.

No signed trust result persists between calls: release, manifest, signature,
source state, protected entries, transitive graph and resume evidence are
verified on every read. There is no TTL, bypass, old-signature admission or
second implementation of the canonical diff hash. A registry mismatch remains
a diagnostic inconsistency, never proof that a process is stopped. This patch
does not introduce a new registry reader or attestation publisher.

## Validation

`python3 -m unittest tests.test_dispatcher_status_verification_performance`
covers exact binary/mode/symlink/empty/shared blobs, process count reduction,
new release reread, missing objects, malformed output, nested imports, frozen
graph equivalence, valid repeated signature checks, altered manifest/signature,
dirty source and changed commit rejection. Existing signature/commit/physical
lock and resume suites remain mandatory. Keys in tests are synthetic only.

The optional `--benchmark-root <signed-artifact> --samples 30 --output <new-file>`
mode times the complete verifier on a real signed artifact, read-only. It
labels its output `full_verifier_only_not_status_query`. Do not present this
as a complete canonical-controller query or as certification of the unsigned
modified verifier itself. No worker process is started or restarted.

Full-query target: P95 <= 3000 ms, no timeout increase. The BotApp permanent
gate requires the exact final signed candidate to be active and reads only
`dispatcher status --json` 30 times. Until that candidate is available, this
end-to-end certification is explicitly pending, not inferred from a benchmark.
