# Isolated signature / commit protocol V2

Scope: governance tools and offline evidence only. Patch 3.1 functional bytes,
business behavior, database, production services and installed apps stay unchanged.

## Order and identity

1. Signed scoped Approval 1 permits edits and a **candidate commit only** after
   an exact staged freeze. It never grants deployment rights.
2. Freeze all changes in the index; reject unstaged/untracked changes and paths
   outside the signed scope. The freeze contains a **tree SHA**, not a commit SHA.
3. The pre-commit hook verifies the signed Approval 1, its expiry/base, and the
   exact freeze. Create the final commit without needing a future signature.
4. Only now generate the final external manifest against the checked-out clean
   commit. The committed tree/delta must equal the frozen tree/delta. Re-certify
   this exact commit, including the three frozen Patch 3.1 resume contract gates.
5. External human signing signs the exact final manifest bytes. The generator
   and verifier cannot sign; tests use ephemeral synthetic keys, never Liam's key.
6. The machine deployment gate verifies the final signature, commit, tree, diff,
   protected bytes/modes/import closure and all existing recertification gates.
   PASS means eligible for separately approved activation, not activated.

| Identity | Definition |
| --- | --- |
| `candidate_commit_sha` | Full Git object id, `cat-file -t` must be `commit` |
| `candidate_tree_sha` | Full Git object id, type must be `tree`, equal to commit's tree |
| `canonical_diff_sha256` | SHA-256 of the single serialization defined below |
| `manifest_sha256` | SHA-256 of the exact final file bytes, including newline |

`certified_candidate_sha` and `approval.head_sha` are compatibility **commit**
aliases; both must match `candidate_commit_sha`. A tree is never accepted as a
commit, including through the legacy `enforce_exact_candidate=False` path.
The manifest hash appears only in external receipts; it cannot be embedded in
its own hashed document. Approval-stripped JSON hashes are retired, not aliases.

## The only canonical diff computation

Implementation: `follow60_candidate_identity_v2.canonical_diff`.

Read both trees with `git ls-tree -r -z --full-tree`. Parse each entry into its
UTF-8 path and `{mode,type,oid}`. Reject non-UTF-8 paths. Sort the union of paths
lexicographically by Unicode codepoint. Retain only entries where the before
and after objects differ. Missing entries are JSON null. Serialize this object:

```
{"serialization":"FOLLOW60_TREE_DELTA_JSON_V2",
 "base_tree_sha":"<tree of exact base commit>",
 "candidate_tree_sha":"<frozen or committed candidate tree>",
 "changes":[{"path":"...","before":null,"after":{"mode":"100644","type":"blob","oid":"..."}}]}
```

Canonical bytes are Python `json.dumps(payload, sort_keys=True,
ensure_ascii=True, separators=(',', ':')).encode('utf-8')`, **no trailing LF**.
`canonical_diff_sha256 = SHA256(canonical_bytes).hexdigest()`.

This captures additions/deletions, binary blobs, executable modes, symlinks and
gitlinks. Rename is delete+add, with no rename detection, textconv, external diff,
attribute/filter or user-config dependency. The base commit identity is bound
separately; two commits may share a tree and delta but not a certificate.

Freeze, generator, identity verifier, deployment gate and receipts all call this
one implementation. Tests compare its output and deliberately present a raw
`git diff --binary` hash to prove rejection. Historical V1/V2 audit readers are
not valid V2 candidate admission paths. The old V3 candidate generator and
precommit-final approval path explicitly fail; no silent legacy fallback exists.

## Artifacts and hooks

Keep `approval-1.json`, `approval-1.sig`, `freeze.json`, final `manifest.json`,
`manifest.sig` and recertification receipts outside the candidate checkout.
The generator/signing tools refuse to overwrite existing final outputs.

In a standalone isolated checkout (not a shared production worktree config),
configure `core.hooksPath=.githooks`, `follow60.candidateEvidenceDir=<external dir>`
and `follow60.certificationDirectory=<external dir>`. The hook's `python3` must
have `cryptography` installed. Missing dependencies/configuration fail closed.

Use `request-follow60-protected-index-commit-v3-1.py` after staging to create the
freeze, then commit. Use `regenerate-follow60-mainline-lock-v3-1.py` with the
freeze, exact base SHA, scope, previous manifest, exact-commit recertification
receipt and resume-contract receipt. `FOLLOW60_LOCK_APPROVE_CHANGE --manifest`
validates the exact identity before and after external signing. Pass the final
sidecars explicitly to `verify-follow60-deployment-candidate-v1.py`.
CI/package promotion must be supplied the same signed sidecars; missing evidence
is a blocker, never permission to rebuild/sign/bypass the check.

The existing release installer copies the signed sidecars to fixed installed
paths `docs/governance/FOLLOW60_MAINLINE_LOCK_V3.{json,sig}`. Verification allows
only those two overlays and the three existing `.deployment` JSON receipts
(`candidate-build-receipt`, `migration-attestation`, `lineage-gate-receipt`).
They must never appear in protected **source** scope. All other source/index
changes are rejected. Sidecars still require a valid signature and exact commit
binding. Candidate generation is stricter: it requires a fully clean checkout.

No timestamp heuristic substitutes for commit existence. Early/previous manifests
fail against a later final commit even when both commits have the same tree.

## Physical deployment evidence

Physical sealing and native promotion use the external record/transaction
contract in [EXTERNAL_PHYSICAL_DEPLOYMENT_LOCK_V2.md](EXTERNAL_PHYSICAL_DEPLOYMENT_LOCK_V2.md).
No physical lock artifact is written into the Git worktree. The existing
source scanner and sidecar exceptions above remain unchanged. Candidate
signature admission precedes sealing; final activation additionally requires
the strict external physical seal and same-process transaction recheck.

## Regression proof

`tests.test_follow60_signature_commit_protocol_v2` exercises A-I, the real
pre-commit hook in a synthetic repository, actual generator/signing/CLI gate,
tampered/absent evidence, binary/delete/rename/mode serialization, and installed
sidecar compatibility. Existing resume-state and lock tests remain mandatory.

The original circular protocol is reproduced separately from its untouched
historical source: commit admission is denied before final approval, its freeze
hash differs from the staged binary diff hash, and a signed tree is confused
with a commit. No production endpoint or device is used.
