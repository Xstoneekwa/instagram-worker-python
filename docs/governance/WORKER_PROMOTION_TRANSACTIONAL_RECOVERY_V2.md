# Transactional promotion V2: compatibility baseline qualification

This change is release machinery only. No production operation is authorized by
this document. The current active OLD remains a482446d0c6c72c59fcfecad91992a43cf7191b0.

## Two separately governed stages

The rollback worktree is the V2-compatible baseline: a482446 product semantics
plus release machinery, without the 416dfad business delta. The forward worktree
retains the entire 416dfad business delta. Operator authorization on 2026-09-11
requires a separate baseline promotion task and certification before a subsequent
business-candidate promotion. Only the then-active exact baseline SHA can be OLD
and automatic rollback. A staged compatibility tree is never substituted for OLD.

The V2 controller rejects an old active runtime lacking V2 token consumption
before acquiring the transaction/fence. Bootstrapping the compatibility baseline
from V1 requires a separate promotion review; this patch does not grant a bypass
or silently select a legacy activation path.

## Lifecycle and finalization

The invoking certified candidate owns promotion_service_control_v2.py, included
in its import graph. Dispatcher, heartbeat and notifier wrappers delegate their
pause/resume entry points to this one helper before old wrapper initialization.
The parent selects the canonical non-root runtime user. Disable precedes bootout;
absence of the launchd job is checked; process convergence is independently
checked by the adapter. Resume uses enable/bootstrap. No gui/0 is permitted.

After service/token verification and Worker generation publication, projection
and terminal verification occur with the fence ON. External finish validates
owned active-fence evidence, publishes immutable terminal evidence and archives
the external lease. Only then does the state machine release the fence and publish
an immutable finalization receipt, followed by its terminal journal state.

A finish failure retains the fence. An unfence or final receipt failure after
commit retains NEW and is COMMITTED_FINALIZATION_REQUIRED. Protected recovery is
not terminal: it reinspects authority and chooses rollback or forward completion.
A rollback already finished resumes finalization instead of quiescing unfenced
services. A drain timeout never permits quiescing still-active work.

## Qualification boundaries

Local PostgreSQL tests execute the candidate migration against a socket-only
throwaway database. They cover function identities, privileges, fence ownership,
creation/claim triggers, and safe renewal of an unexpired device lease without
changing identity. They are not evidence that production has the migration or
that PostgREST exposes it. Production metadata remained unreadable through the
connected MCP during preparation; no migration was applied.

The historical 416dfad pending receipt must be retained. An authoritative
superseding receipt requires fresh runtime, service, registry, admin lock and
fence evidence. This patch does not fabricate such a production receipt.

Approval 1 permits scoped work and a normal-hook commit after exact freeze.
Approval 2 and all release gates are still required for a sealed release.
No production switch, service restart, ADB, forced run or incident mutation occurs
in this qualification task.
