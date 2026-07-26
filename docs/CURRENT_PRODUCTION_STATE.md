# Current Production State

## Prepared addendum — account protection lists V1

The 2026-07-26 checkpoint adds one canonical account-scoped protection snapshot loaded once before device access for account/outreach sessions. Interaction blacklist and Unfollow whitelist have separate action semantics; active requests keep an immutable snapshot. Backend failure blocks safely and legacy list fields are not fallbacks. No run or phone action belongs to deployment. See the [scoped checkpoint](./checkpoints/2026-07-26-account-protection-lists-v1.md).

## Prepared addendum — package runtime contract V1

The 2026-07-26 checkpoint makes the Supabase package/runtime contract a
fail-closed Worker preflight before device lock or subprocess creation. It also
distinguishes known transient Android login overlays from a persistent package
mismatch without weakening the strict package guard or Identity Guard. The
active release, dispatcher PID and zero-queue evidence are recorded at rollout.
See the [scoped checkpoint](./checkpoints/2026-07-26-package-runtime-contract-v1.md).

## Golden addendum — Unfollow handoff budget and request terminalization

The 2026-07-25 checkpoint moves the complete Unfollow time calculation to the
Follow-to-Unfollow handoff (or the direct Unfollow entry point). The scheduler
business-action deadline is propagated through the request consumer, runner and
account-session orchestrator. A six-hour fallback is used only when that real
deadline is absent. During Unfollow, only a monotonic check every five minutes
or 25 verified actions remains; the full plan is immutable until the optional
Outreach boundary is re-evaluated.

For real runner sessions, the run terminal status is now published after
session cleanup; direct status-helper semantics used by the established Follow
deferred-persistence contract remain unchanged. The request consumer
terminalizes the request only after the subprocess has returned and its device
lease has been released. Follow, Outreach, Auto Restart, Incidents,
T-10 and Mythyl V2 policy are otherwise unchanged; V2 enforce remains disabled.
No phone or Instagram action is part of this checkpoint. See the
[scoped checkpoint](./checkpoints/2026-07-25-unfollow-handoff-budget-v1.md).

## Golden addendum — T-10 safe skip, Follow 7+1 and V2 natural shadow evidence

The 2026-07-25 intermediate checkpoint records three bounded facts: connected
and ready accounts are safely skipped by T-10 before any phone action; legacy
Follow continuation now prefers seven fully visible new rows plus one verified
overlap while preserving all safe fallbacks; and Mythyl naturally exercised V2
shadow fail-open with enforce disabled. No physical validation of the new
scroll geometry belongs to this addendum.

Full evidence and rollback are in
[the scoped checkpoint](./checkpoints/2026-07-25-t10-follow-scroll-v2-runtime.md).

## Golden addendum — Follow adaptive scroll startup-safe activation

The `7d2797e` adaptive Follow scroll, bounded recovery and safe CT rotation
checkpoint is extended by one local deployment guard. The guard does not change
Follow behavior: it only consumes a private runtime token during dispatcher
initialization and advances the embedded Auto Restart cadence marker so the
startup call is skipped once.

The production Auto Restart policy remains enabled. Subsequent natural ticks,
backend candidate selection, `manual_only`, manual-stop, cooldown, idempotency
and maximum-restart gates remain authoritative and unchanged. Target Followers
Resume V2 stays Mythyl-only shadow with enforce disabled. No physical run is
part of this Golden addendum.

## Golden addendum — Target Followers Resume V2

The current rollout is based on Worker `d605736` and preserves Golden
navigation `e7f54a9`, Follow `ff99db6`, historical Auto Login `07ee49b`, T-10
and Follow source 30/4. V2 is a passive observer only. Its production target is
Mythyl UUID `0d299d1e-46ee-49d2-8a84-4f928f2bb182`; all other accounts remain
outside the builder gate. No history is backfilled and the first checkpoint is
zero.

This addendum becomes active only after the recorded off-state release,
production migration, Mythyl-only canonical environment activation and zero-run
checks. A natural Mythyl observation requires a separate Liam GO.

## Prepared checkpoint — 2026-07-23

`FOLLOW_WARMUP_ACTIVE_SAST_DAYS_V1` is documented at
[this checkpoint](./checkpoints/2026-07-23-follow-warmup-active-sast-days-v1.md).
Worker code `51278eadf1613f80349a7930e17420c8d8dd1e64` is pushed. The
runtime/deployment state must be confirmed by the rollout evidence before this
prepared checkpoint is called active production.

Current checkpoint: [JULY_16_PRODUCTION_BASELINE](./checkpoints/2026-07-16-production-baseline-cross-repo.md).

| Surface | Reference |
|---|---|
| Worker business SHA | `5a59ef3677c59b5e65e6b69462f3756fd96cda78` |
| Worker active release | `5a59ef3-follow-caps-like-evidence` |
| Backend business SHA | `6f0f3b028379e029f09f66086372bf39798fe193` |
| Backend deployment | `dpl_EQeojpstgH173NGAtjY1tWFAHLGq` (`READY`) |
| BotApp business SHA | `fa427ace11b294d3765f4a6e0dda2522770ed70e` |
| Official BotApp | `/Applications/BotApp.app` |

At checkpoint preparation the dispatcher was idle and healthy, business queue,
runs, locks and nonterminal preflights were empty, both canonical device
heartbeats were fresh and no business subprocess was active.

The three open performance paths are Pre-Follow, Post-Mute to post open, and CT
stable to next candidate. Runtime validity and pending physical observations are
defined in the linked checkpoint, not inferred from test results.
