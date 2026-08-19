# Follow60 Mute structural matcher and post-follow recovery V1

## Scope

Generic Follow60 correction for a verified Follow whose mandatory Mute stage
cannot find the exact `Following` CTA on a valid candidate profile. There is no
account, candidate, device, package-tier, locale, or screen-size exception.

## Proven root cause

The previous matcher used a global vertical cutoff for the profile action row.
Instagram moves that row downward when a profile header is taller. An exact
`Following` CTA could therefore be rejected solely as
`y_outside_header_cta_band`, after the Follow had already been physically
verified and durably persisted. The incomplete outbox path then deleted its
local recovery evidence and surfaced a global non-recoverable exit, preventing
the mandatory Unfollow handoff.

## Canonical contract

1. The exact current candidate username must match the intended candidate.
2. `Following` must be an exact supported state label; `Follow`, `Follow back`,
   `Message`, `Contact`, list rows and Suggested cards remain rejected.
3. Action-row ownership is proved structurally by either a wide profile CTA or
   an aligned Message/Contact/add-person peer. A global percentage cutoff is no
   longer authoritative.
4. On a narrow candidate-local matcher/budget failure, Mute receives at most
   one retry, only while Instagram is foreground and exact profile identity is
   reconfirmed. The retry never taps Follow and never changes Follow counters.
5. Verified Mute axes are persisted idempotently. An incomplete mandatory Mute
   retains its outbox evidence and is classified `partial_resumable`.
6. A safe Return CT permits the mandatory Unfollow handoff. New Follow work is
   blocked until the candidate-local post-follow recovery is completed.
7. Runtime identity, database, foreground, safety and global phase failures
   keep their existing global blocker/non-recoverable behavior.

## Performance contract

The successful happy path adds no RPC, XML dump, screenshot, vision call,
sleep, tap or retry. The single extra UI attempt exists only on the explicitly
enumerated candidate-local failure path.

## Failure classification

| Boundary | Classification | Handoff | Restart Follow |
| --- | --- | --- | --- |
| Mute succeeds | completed | normal | normal |
| Candidate-local Mute incomplete after bounded retry + Return CT exact | partial_resumable | Unfollow allowed | blocked pending recovery |
| Wrong/stale profile or structural ownership unproved | fail closed | no unsafe mutation | blocked |
| Runtime/global/safety failure | existing global failure | existing contract | existing contract |

## Governance

Protected change protocol Follow60 Lock V3.1 applies. Approval 1 authorizes the
implementation and tests only. The frozen diff, manifest and promotion require
the independent Approval 2 signature before commit, push, packaging or runtime
activation. Field certification remains pending the next natural run.
