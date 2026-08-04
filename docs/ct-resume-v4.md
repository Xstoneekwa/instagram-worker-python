# CT Resume V4 certification state

CT Resume V4 source transport, checkpoint integrity, commit provenance,
idempotency, fail-closed identity and regression tests are certified on the
Follow60 lineage. It remains orthogonal to the Follow60 engine selection.

Certified:

- immutable Worker identity propagation;
- checkpoint and event V4 schema/RPC contract;
- exact target/request/attempt/release/lease continuity;
- bounded hashed anchors and no raw candidate persistence;
- safe fallback to current viewport when proof is absent or invalid;
- no coupling between CT provenance and Follow persistence `request_id`;
- full Worker regression coverage on the consolidated lineage.

Not claimed:

- a complete second-pass natural field certification across every supported
  layout and account. That remains a separate terrain checkpoint.

Follow60 Mainline must not weaken these rules. Future Target work should use
`docs/target-block-documentation-index.md` as the joint reference.

