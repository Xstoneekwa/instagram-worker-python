# Resolved restriction: administrative readiness and physical admission

The inherited implementation retained `admin_lifecycle_status=paused` after a
human resolved a restriction. It restored the previous status only after a
verification-only Worker run cleared the physical hold. Its bounded pre-device
retry required the same unexpired authorization and window. Restrictions were
also absent from the generic next-window reconciliation.

This migration restores `active` immediately after canonical human resolution
when the pause is demonstrably owned by that restriction. The existing dashboard
projection can therefore display `growth ready`. The hold remains
`verification_required`: readiness is not evidence that Instagram was checked.

## Ownership and independent blockers

Restoration requires a previously active lifecycle, exact restriction pause
ownership, a resolved human-reviewed incident, and no independent manual,
commercial, security, archive, cancellation, parameter or active operational
blocker. Existing pauses require equality between the recorded pause timestamp
and the account write timestamp; proximity alone is insufficient. The restore
receipt records the incident resolution version. An explicit later lifecycle
pause, including a repeated `paused` write, invalidates automatic restoration.
Unclassified states remain unchanged.

The final incident trigger observes the canonical RPC's authority metadata write
as well as the earlier status change. Both occur in the same transaction. A
generic migration convergence applies exactly these checks to existing holds.
It does not change incident resolution, create requests or start runs.

## Natural-window authorization

The existing reconciliation entry point additionally prepares a zero-action
restriction verification in an actually open reserved assignment. It requires
the exact latest failed source run and source request. It preserves previous
authorization identities and expirations. A consumed historical authorization
is recoverable only for a proven `account_not_active` rejection before any run,
start or cancellation. A real physical attempt, revocation, supersession or
ambiguous evidence prevents automatic rearming.

Each new authorization binds its incident generation, assignment window, source
lineage and the unique fresh dispatcher heartbeat. Its metadata retains the
Worker SHA from human resolution and records the newly verified Worker SHA.
Missing, stale or ambiguous Worker evidence creates no authorization. Existing
unique indexes and account locks prevent duplicate window authorization.

The unchanged Backend constructs the canonical verification-only frozen plan
and atomically consumes the authorization. The unchanged Worker proves identity
and restriction absence before clearing the hold. Both atomic admission
overloads reject ordinary business while a physical hold remains, including
idempotent admission retries. No phase, quota, schedule, device lease, fleet
fence or assignment guard is relaxed.

## Qualification and deployment

Run `scripts/test-resolved-incident-growth-ready-v1.py` with the pinned Backend
schema dependency and a new external evidence directory. It creates a disposable
PostgreSQL 17 and PostgREST, replays actual migrations and executes the unchanged
Backend tick against that database. Network access from the application tests is
restricted to the local adapter. No phone, Instagram or ADB is used. Synthetic
physical success represents the existing trusted Worker call boundary; it is
never reported as a live physical certification.

The SQL transaction installs admission guards before convergence becomes visible.
Deployment requires the exact tested Approval 2, a fresh zero gate, admission
fence, immutable Worker release and the standard V2 controller. The old active
release is the exact rollback. SQL rollback must preserve restriction safety;
restored readiness must not be left paired with older admission predicates.
Activation evidence must include catalog equality, service convergence, token
consumption, durable receipt, transaction finish, lock/fence release and a
read-only check of account readiness and next-window eligibility. No forced run.
