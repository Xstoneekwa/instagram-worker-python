# Phone Farm final stability closure — Approval 1 design

## Certified production identities

- Worker: `a794da764c57a50187bd94c9411f6566519ad3ca`, rooted at `/Users/admin/phonefarm-worker-releases/a794da7-follow60-production-stability-closure-v1`.
- Backend: `530802780b2f3de6b0a1046c21ca4f6bde77bbb9`, Vercel deployment `dpl_RGDhvQ14mcTmkn9KPuGsRcS61wF2`.
- BotApp: installed artifact identity is not cryptographically verifiable; the lineage gate must fail closed until artifact provenance embeds a full source SHA.

## Machine-enforced lineage gate

`scripts/production_lineage_gate_v1.py` is the fail-closed pre-deploy entry point. It requires a clean worktree, exact 40-character production and candidate SHAs, candidate ancestry from registered production, inclusion of every active canonical delta, candidate-side presence of required migration paths, parity with applied production migration evidence, and verifiable artifact provenance where required. Missing registry, unknown production identity, dirty worktree, stale branch, missing delta, missing migration or unverifiable artifact all block. The normal CLI has no dirty-worktree or ancestry bypass.

`docs/governance/PRODUCTION_CANONICAL_DELTA_REGISTRY_V1.json` is the initial multi-component canonical registry. Each delta records repository, introducing SHA, scope, activation timestamp, required flag, supersession link, migration dependencies, tests, notes and status. The gate hashes the registry deterministically and can emit a durable pre-deploy receipt whose deployment fields remain empty until post-deploy verification.

The historical fixture reproduces the exact Backend rollback lineage: production `2349a8e299ec28563e87423e43bcf891684304b7`, stale candidate `ee677adc93a2dca71fdb5029b60e5a6e14931c27`, and lost Manual Stop delta. The gate rejects it because the candidate is not a descendant of production.

## Real Mythyl call graph and first broken boundary

Field run `951e218a-4b31-4a93-b816-0cc2d319284e` proves this sequence:

1. `runner.py` physically verifies Follow for `rimmaabadjan` at `20:50:56.627Z` and persists the Follow intent.
2. Mute searches begin at `20:51:00.468Z`. The exact `Following` control is found at `cy=584`, but `_mute_engine_v2_following_cta_structure_ok()` rejects ownership at `20:51:02.078Z`, `20:51:09.588Z`, `20:51:17.379Z`, and `20:51:28.653Z`. The first broken boundary is structural CTA ownership, not candidate identity and not absence of the text.
3. The Like was already completed before Mute and is reused after Mute at `20:51:33.294Z`; there is no second post open and no second Like tap.
4. Return CT is exactly journaled at `20:51:47.177Z`.
5. `post_follow_stage_outbox` retains a durable target-local partial: Follow, Like and Return CT are preserved; only Mute Posts/Stories are missing; Follow retap is forbidden; safe next step is Unfollow handoff.
6. `runner.py` returns exit 53 with `partial_resumable`.
7. `account_session_orchestrator.py::_follow_target_local_failure_contract()` incorrectly requires `follows_completed_count == 0`. Because the durable Follow count is 1, this second legacy decision source downgrades the result to `partial_not_resumable` and ends the session. This is the A794DA contradiction.

The existing Like-before-Mute behavior is structural: the runner can complete the configured post Like before the Follow/Mute post-action sequence. The field trace confirms reuse, not a new Like after the Mute failure. The closure must make the ordering explicit and stable: Follow mutation proof, then required Mute verification or bounded target-local recovery, while a precompleted Like receipt remains idempotent and must never be repeated.

## Proposed single authoritative decision source

After Liam Approval 1, introduce one immutable `FollowTerminationDecision` at the runner/orchestrator boundary. It is built once from durable stage receipts, first causal reason, current UI safety proof and global safety signals. It owns phase status, failure scope, safe boundary, next step, retap policy and Unfollow eligibility. `_follow_target_local_failure_contract`, `_follow_exit_handoff_gate`, `_evaluate_h3_follow_exit_code_gate`, restart eligibility and final phase projection must consume this decision without independently reinterpreting counters or exit codes.

Retire the legacy `follows_completed_count == 0` target-local gate. A nonzero durable Follow is evidence to preserve, not a reason to invalidate a target-local partial. Critical account mismatch, challenge, restriction, crash, ambiguous mutation, missing durable Follow proof or unsafe UI boundary remain global fail-closed blockers.

## Proposed bounded recovery contract

- Reacquire a fresh navigation generation and exact candidate identity.
- Prove the `Following` CTA through structural parent/sibling/resource evidence; never accept bare text.
- Permit at most 2 recovery attempts and at most 8 total seconds.
- Never retap Follow after durable Follow proof.
- Never reopen or relike a post when a Like receipt already exists.
- If Mute remains unproved but Follow is durable and Return CT is exact, emit a target-local partial and hand off to real Unfollow.
- Auxiliary log/screenshot/checkpoint persistence failure is deferred and surfaced diagnostically; it cannot rewrite a successfully persisted business mutation.
- Business stage receipts remain the authoritative Follow-to-Unfollow input. Counters are projections only.

## Mutable runtime paths

The field run also logged `run_log_file_init_failed` because runtime output targeted the immutable release tree. Runtime logs, screenshots, checkpoints, temporary XML and receipts must resolve beneath `/Users/admin/phonefarm-runtime`, never the release root. Release code and protected governance files remain immutable.

## Proposed protected changes after Approval 1

- `runner.py`: emit and propagate the authoritative termination decision; preserve precompleted Like idempotence.
- `account_session_orchestrator.py`: remove counter-based reinterpretation and consume the single decision for rotation, handoff, restart and terminal projection.
- `instagram_navigation.py`: bounded fresh-generation structural ownership recovery for compact profile action rows.
- `post_follow_stage_outbox.py`: expose the canonical decision inputs from durable receipts without altering receipt authority.
- `tests/fixtures/follow60_production_stability_closure_v1.json`: add the exact Mythyl `rimmaabadjan` incident and lineage/runtime-path cases.
- Corresponding protected Follow60 regression tests and the approval manifest/ledger required by Lock V3.1.

No protected file is modified in Phase A. Expected healthy-path overhead is zero additional UI actions, screenshots or vision probes; the bounded structural recovery runs only after the current ownership proof fails.

## Approval 1 implementation result

Approval 1 was consumed only for the minimum proved closure. The runner's exact
exit-53 receipt contract is now converted once into
`FOLLOW_TERMINATION_DECISION_V1`. When accepted, target rotation, Follow retap
and the legacy zero-mutation reclassification are forbidden. The authoritative
decision preserves verified cumulative Follow counts, remains
`partial_resumable`, and exposes `handoff_to_unfollow`; the independent H3
health, identity, deadline, persistence and Unfollow-eligibility gates remain
fail-closed.

The zero-mutation target-local classifier is retained for failures before a
durable Follow. It is bypassed only when the precise exit-53 contract proves a
durable candidate-local post-Follow partial. Tests cover both one and multiple
verified Follows, prohibit target rotation and a second Follow, and execute the
real outer target-rotation path.

The Mythyl `cy=584` rejection was caused by the structural peer query requiring
the peer node itself to be clickable. Instagram can expose `Message` or
`Contact` as a non-clickable label child inside the clickable action control.
The recovery query now admits bounded Button/TextView/ImageView label children,
then still requires a certified candidate profile plus a vertically aligned
Message/Contact action-row peer. Bare `Following`, wrong identity, unaligned
peers, and Suggested content remain rejected. The query count is unchanged.

Mute recovery uses one shared context-local budget: two attempts total and
eight Worker-controlled seconds total. It adds no fixed sleep and no healthy
path UI lookup. The field Like was a precompleted receipt reuse, not a second
tap; Like ordering and idempotence code therefore remain unchanged.

Mutable run logs and the post-Follow SQLite receipt spool now resolve under
`PHONEFARM_RUNTIME_ROOT` (or `~/phonefarm-runtime`), never beneath an immutable
release. Canonical business persistence remains fail-closed; no canonical
receipt authority was weakened.

Certification before Approval 2:

- full Worker matrix: 3,238 tests passed, 10 skipped, zero failures;
- healthy rotation microbenchmark, 2,000 iterations: baseline p50 0.062041 ms,
  p95 0.099792 ms; candidate p50 0.063208 ms, p95 0.096250 ms;
- structural recovery matcher, 10,000 iterations: p50 0.001917 ms, p95
  0.002084 ms, observed max 0.082334 ms;
- no new device RPC, XML dump, screenshot, Vision call, network RPC or sleep;
- no commit, push, package, runtime switch, run, tick, ADB action, incident
  resolution or production deployment was performed under Approval 1.
