# BotApp account status and incident canonical contract V1

Status: `ACCOUNT_STATUS_CONTRACT_LOCK_V1 = ACTIVE`.

This document records the current Phone Farm status contract. It is documentation
only: it does not create a status, change precedence, alter scheduling, or grant a
resume. Runtime and database contracts remain authoritative when a historical log
or document disagrees with this file.

## Authorities and boundaries

| Concern | Current authority | Consumer rule |
| --- | --- | --- |
| Active operational blocker | `canonical_active_operational_blockers_v1` plus Backend `operational-blocker.ts` | Profiles, scheduler pre-enqueue and start projection consume the same effective truth. |
| Final start safety | Atomic database admission | A positive UI/readiness projection never bypasses admission. |
| Actionable UI work | Active `account_dashboard_actions` rows | Terminal, stale scheduler, or incident-derived rows whose incident is no longer active cannot block. |
| Durable operational history | `account_incidents` | `resolved` and `archived` are history, not active blockers. |
| Capability/readiness | Backend readiness projection | `growth_ready`/`can_start` is a capability, never a blocker and never stronger than an operational blocker. |
| Runtime activity | `account_run_requests`, `ig_runs`, assignments and heartbeats | Runtime freshness may qualify Active/Idle/Waiting; stale runtime evidence cannot hide a critical blocker. |
| Notification delivery | Notification Router V2 business events and delivery ledger | Incident resolution queues delivery; the action route does not synchronously dispatch Slack/Discord. |

Scheduler pre-enqueue is an optimization filter. Atomic admission is the final
security authority. Neither BotApp nor a client recomputes these decisions.

## Canonical visible vocabulary

| Canonical name | Category | Meaning and entry/exit | Blocking and presentation |
| --- | --- | --- | --- |
| Connected | capability | Canonical login state is connected; exits when login/credential state is invalidated. | Secondary capability; does not override a blocker. |
| Growth ready | capability | Readiness is `ready` and effective eligibility is `can_start`; exits when a readiness component or blocker fails. | Non-blocking secondary capability. Never a primary blocker. |
| Identity verified | capability | Current identity proof is verified and username match is true. | Secondary proof; mismatch becomes a blocker. |
| Active / Running | runtime | A fresh request/run/heartbeat proves current execution. | Runtime primary only when no stronger operational blocker exists. |
| Preparing / Queued / Recovering / Starting | runtime | The canonical execution phase is non-terminal but not yet active. | Informational runtime state. |
| Idle / Stopped | runtime | No fresh active execution, or the last execution is terminal. | Informational; never clears a blocker. |
| Waiting scheduled assignment | readiness | Required capacity exists but the scheduled assignment/window is not active. | Start and scheduler remain ineligible until the window/assignment becomes ready. |
| Operational incident | operational blocker | Fallback current wording for a blocking active canonical incident not covered by a more specific presentation. | Primary; blocks scheduler/start/admission according to the canonical predicate. Manual review is required when the incident contract says so. |
| 48H pause required | operational blocker | Active `instagram_account_restriction`; `not_before` carries the earliest eligible time. Exit requires canonical resolution/expiry handling, never elapsed time inferred by BotApp. | Primary restriction status; blocks scheduler, start and atomic admission. Connected/Growth ready may remain secondary. |
| Instagram login required | operational blocker | Active `account_login_required`. | Primary login blocker; reconnect through the canonical login flow. |
| Instagram confirmation required | operational blocker | Active `instagram_human_confirmation_required`. | Primary manual-confirmation blocker. |
| Instagram identity mismatch | operational blocker | Active `active_instagram_account_mismatch`. | Primary identity blocker; manual identity review required. |
| Instagram app unavailable | operational blocker | Active `assigned_instagram_package_unavailable`. | Primary device/package blocker. |
| Operator review required | dashboard-action wording | A legitimately standalone, current `blocking_campaign=true` dashboard action requires review. | Primary only when the action is current. This wording is not the canonical generic incident label. |
| Missing credentials / Update password | login/readiness blocker | Credential state is missing or invalid. | Blocks connection and therefore start; secrets are never projected. |
| Challenge / checkpoint / action required | login/readiness blocker | Instagram requires a code, checkpoint or human confirmation. | Blocks until the canonical action/verification transition completes. |
| Device or assignment unavailable | readiness blocker | Required launchable device/app instance/assignment is absent or blocked. | Blocks readiness, scheduler and start. |
| Ready / Eligible | readiness | All required readiness components pass and no effective blocker exists. | Allows pre-enqueue; atomic admission can still reject newer state. |
| Blocked / Not eligible | readiness | A required component or effective operational blocker fails. | No start/enqueue. The reason code, not a client guess, explains the state. |
| Active / onboarding / pending / paused | commercial/lifecycle | Current administrative, customer, subscription or onboarding lifecycle is non-terminal but may constrain readiness. | `paused` and non-active commercial states cannot be promoted to Growth ready. |
| Cancelled / archived / trashed (In Bin) | commercial/lifecycle | Terminal or removed lifecycle membership. | Not eligible for normal scheduling; displayed in the appropriate historical/archive surface. |

Specific incident presentations outrank the generic `Operational incident` label.
When an incident and a standalone dashboard action coexist, Backend severity
precedence selects the effective operational blocker. Critical precedes error,
which precedes warning.

## Effective dashboard-action rule

A dashboard action blocks only when all applicable conditions hold:

1. `blocking_campaign` is true;
2. its status is not terminal (`resolved`, `completed`, `dismissed`, `canceled`,
   or `cancelled`);
3. a scheduler blocker is not historical by window/success evidence; and
4. if it references an incident, that incident is still active.

Therefore:

- resolved or archived incident -> historical only;
- stale incident-derived dashboard action -> cannot block;
- active incidents only -> participate in blocker precedence;
- legitimately standalone dashboard action -> remains independent;
- Profiles, scheduler and atomic admission -> same effective blocking truth.

Incident-derived dashboard actions MUST preserve the exact incident lineage at
creation time. When the incident publisher returns an `incident_id`, that value
is propagated through the runtime publisher and persisted by the action upsert;
it is never reconstructed from a nearby incident when the exact ID is already
available. A subsequent upsert without an incident ID cannot erase an existing
link. `incident_id=NULL` is reserved for legitimately standalone actions, not as
a fallback when lineage propagation fails. Once a linked incident is resolved or
archived, its action is historical and cannot remain an effective blocker.

## Status precedence matrix

| Condition | Primary status | Secondary status | Scheduler / Start | Resolution authority |
| --- | --- | --- | --- | --- |
| Active Instagram restriction | 48H pause required | Connected; Growth ready capability may remain visible | blocked / blocked | canonical incident/restriction transition, respecting `not_before` |
| Other active blocking incident | Specific incident label, else Operational incident | capability and runtime context | blocked / blocked | incident lifecycle and any required runtime proof |
| Current standalone blocking action | Operator review required or action-specific label | capability/runtime context | blocked / blocked | dashboard-action transition |
| Login, challenge or identity failure | Login/confirmation/mismatch label | lifecycle/runtime context | blocked / blocked | credential, login or identity proof transition |
| No blocker; readiness waiting | Waiting scheduled assignment or waiting auto-login check | Connected when proven | no enqueue / no start yet | canonical readiness inputs |
| No blocker; all checks pass | Runtime state when active, otherwise Growth ready | Connected and Identity verified | eligible / atomic admission decides | no resolution required |

## Incident views

`Open` contains non-terminal active incidents. `Acknowledged` remains active and
may therefore still appear in Open. `Action required` is a subset/projection of
active work for which a current dashboard action or incident contract requires a
human action; the same incident can be in Open and Action required. `Resolved`
and `Archived` contain terminal history and cannot participate in operational
blocker precedence.

An incident leaves Open when its canonical lifecycle becomes terminal. It leaves
Action required when the actionable row becomes terminal or its linked incident
is no longer active. `manual_resolution_required`/the safe action projection
governs human-review membership. `not_before` is an eligibility boundary, not a
client-side auto-resolution instruction. A 48H restriction is primarily an
operational blocker; it appears in Action required only if the current canonical
projection actually requires a human action.

## Incidents and notifications

A session whose effective Follow/Unfollow quota is reached, whose persistence
is complete and which has no mandatory recovery or safety blocker is normally
terminal. Remaining canonical Unfollow backlog is future business work, not
mandatory work for that completed session. Terminal persistence preserves the
immutable request lineage (`request_id`, root/parent business session and
monotone attempt); a validated resume capsule wins, and `run_id` is only a
legacy fallback when no canonical request lineage exists. Genuine lineage
contradictions remain fail-closed.

`account_incidents` is durable operational truth and
`account_dashboard_actions` is the actionable UI projection. Notification Router
V2 owns five business categories (`incident`, `new_client`, `plan_change`,
`auto_login`, `ct_lifecycle`), each separated by `test`/`live` and
Slack/Discord destinations. Category ownership suppresses a generic incident
notification when a more specific producer owns the event.

Router V2 is the sole provider-delivery path for incident lifecycle events.
Covered Worker ORF-4/operator-review paths are disabled and retained only for
rollback archaeology. A resolved event projects the persisted safe resolution
note once to Slack/Discord and to the Backend timeline consumed by BotApp. It
also carries `Open Incidents/Actions`, containing only the canonical incident
UUID; Admin validates it before routing to exact incident detail and fails
safely for invalid or inaccessible identifiers.

Business events and `(event, destination)` delivery rows are idempotent.
Deliveries are claimed atomically, retried with bounded backoff, and eventually
dead-lettered. Resolving an incident changes incident/action state; notification
delivery is subsequently queued through Notification Router V2. Webhook secrets,
credentials, raw payloads and unsafe metadata are never exposed to BotApp or the
client. BotApp/Admin may display safe incident status, action requirement,
timeline and per-channel delivery state. Client surfaces receive only their
explicitly allowed safe projections.

## Naming history

Before and during BotApp Canonical Mainline V1, logs and UI-oriented documents
often used `operator review required` as a broad incident phrase. After the
canonical operational blocker work, generic active incident wording is
`Operational incident`; `Operator review required` remains valid only for the
specific current dashboard-action presentation described above. Historical
records are not rewritten, and deprecated wording must not be reintroduced as
the generic current incident label.

The Restriction promotion additionally locked `48H pause required` as the primary
status for an active Instagram action restriction. It did not change the meaning
of Connected or Growth ready.

## ACCOUNT_STATUS_CONTRACT_LOCK_V1

This lock is active. A future change to a name, wording, category, precedence,
source of truth, entry/exit condition, blocking semantics, scheduler/admission
behavior, incident-view membership or notification behavior requires, in the
same scoped change: an explicit decision, impact analysis, implementation,
tests, and an update to this document. Opportunistic nomenclature changes in an
unrelated fix are forbidden.
