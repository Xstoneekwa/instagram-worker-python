# Architecture

## System boundary

The Phone Farm is a distributed control system, not a single script. The Worker
owns Android execution; the Backend/Supabase plane owns business state and
queues; BotApp and web dashboards are operator surfaces.

```text
┌──────────────────────────────────────────────────────────┐
│ Web client · Admin dashboard · BotApp                   │
│ account onboarding, status, controls, incidents, stats  │
└──────────────────────────┬───────────────────────────────┘
                           │ authenticated API / relay
┌──────────────────────────▼───────────────────────────────┐
│ Backend + Supabase                                      │
│ accounts · entitlements · packages · schedules          │
│ requests · runs · assignments · app instances · locks   │
│ incidents · notifications · snapshots · counters        │
└──────────────────────────┬───────────────────────────────┘
                           │ account_run_requests
┌──────────────────────────▼───────────────────────────────┐
│ Mac runtime                                             │
│ launchd → phonefarm-runtimectl → durable dispatcher     │
│ embedded scheduler tick · heartbeat · notifier          │
└──────────────────────────┬───────────────────────────────┘
                           │ bound device/package/clone
┌──────────────────────────▼───────────────────────────────┐
│ Worker orchestrators                                    │
│ Auto Login · Welcome · Follow · Like/Mute/Return CT     │
│ Unfollow · Outreach · recovery · incident publication  │
└──────────────────────────┬───────────────────────────────┘
                           │ UI automation with state proof
┌──────────────────────────▼───────────────────────────────┐
│ Physical Android phones and cloned Instagram packages   │
└──────────────────────────────────────────────────────────┘
```

## Repositories

| Repository | Responsibility | Golden authority |
|---|---|---|
| `instagram-worker-python` | dispatcher consumer, runner, UI engines, orchestrators, runtime services | Exact commit `e7f54a9` and this tag |
| `boost-my-businesses-frontend` | Next.js frontend/backend routes, Supabase migrations, commercial and scheduler APIs | Live Vercel artifact recorded, exact Git provenance unresolved |
| `phone-farm-botapp` | Electron operations UI and authenticated relay client | Installed app hash recorded, exact Git provenance unresolved |

Do not assume a similarly named local checkout is active. Runtime provenance is
the immutable release plus symlink; web provenance is the Vercel deployment;
BotApp provenance is the installed bundle.

## Worker layering

- XML/accessibility supplies fast structured signals.
- Vision provides corroboration and fallback for fragile/stale surfaces.
- Navigation state machines make the final screen decision.
- Recovery engines use bounded attempts and explicit terminal reasons.
- Orchestrators own business-phase order and cancellation boundaries.
- `supabase_client.py` and backend RPCs persist control/evidence state.

Critical actions require a confirmed package, screen state and identity. Unknown
or ambiguous state fails safely rather than permitting exploratory navigation.

## Dispatcher, scheduler and runtime

Production ownership chain:

```text
launchd com.boost.phonefarm.dispatcher
→ /Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher serve
→ active-release/scripts/run_control_dispatcher_service.sh
→ account_run_request_consumer.py
```

The active release is resolved on every invocation from
`/Users/admin/phonefarm-worker-current`. Logs, env, PID and lock files live under
`/Users/admin/phonefarm-runtime`, outside releases.

The scheduler is embedded in the dispatcher and calls the backend tick route at
most once per minute. Backend rules own selection, schedule windows,
entitlements and `manual_only`. BotApp never becomes launch authority.

## Phones, packages and clones

The canonical binding is:

```text
account
→ assignment
→ physical device
→ app_instance_id
→ package_name
→ clone_index / binding_version
```

Known certified examples on Samsung A16-01 at this checkpoint:

- primary clone 0 uses `com.instagram.android` for the established primary
  account;
- assigned clone 2 uses `com.instagram.androif` for the newly connected account.

AppCloner suffixes, visual labels and suggested usernames are not sufficient to
derive identity. The Worker must launch the bound package, verify it is
foreground and prove the active canonical username. There is no fallback to
`com.instagram.android` when another package is bound.

## Auto Login

`login_provisioning` is dispatched to `historical_auto_login_07ee_adapter.py`,
which mechanically invokes the isolated 07ee CLI with account, request, run,
device, package and app-instance arguments. The engine routes login surfaces,
password/email challenge flows, logout/recovery and identity publication.

The isolated engine contains one certified overlay: after first
`connected_home`, a short bounded stabilization/reprobe window allows its
existing Samsung Pass/Instagram Save-login-info handlers to process a late
popup. No new selector or login retry was introduced.

## Follow and navigation

The Golden action path remains:

```text
candidate → verified Follow → Mute → post → verified Like → Return CT
```

The f93c501 navigation layer adds:

- viewport-relative soft scrolling;
- positional suffix-to-prefix continuity proof;
- bounded semantic `See more` expansion;
- fresh confirmation of the Suggestions boundary;
- CT rotation only after true exhaustion;
- one shared classifier for Follow, Welcome and Unfollow observation.

Welcome keeps its own job and outbound-bubble proof. Unfollow keeps its own J+3,
protected-row, cap and time rules. Sharing a classifier does not share business
actions.

## Welcome, Unfollow and Outreach

- Welcome scans true followers, excludes Suggestions and persists a sent result
  only after proof of a new outbound bubble.
- Unfollow acts only on eligible historical interactions after J+3, excluding
  protected rows and respecting effective caps/time.
- Outreach is a separate orchestrated run type with its own entry and proof
  contract. Its current physical certification is not part of this checkpoint.

## Incidents and notifications

Structured failures publish stable machine reasons and correlation identifiers.
An incident can create an operator-review action without changing the original
reason. Slack and Discord deliveries are idempotent and share the canonical
Incidents/Actions destination. The renderer never receives privileged database
credentials.

## Warmup, commercial packages and Stripe

Warmup progresses on distinct active SAST days with verified Follow evidence:
10, 20, 40, then configured/package limits. Effective caps are always the
minimum of configured account values, package maxima, warmup, ops hard caps and
remaining quota.

Commercial packages, entitlements, upgrades, downgrades, cancellation and Stripe
billing live in the Backend/frontend repository. They gate eligibility but are
outside the Worker tag. This Golden checkpoint records the boundary; it does not
claim final Frontend/Stripe lifecycle certification.

## Dashboards

- Client dashboard: onboarding, account state, statistics, targeting and
  connection CTA.
- Admin dashboard: profiles, assignments, settings, runs, incidents and health.
- BotApp: local operations projection and relay-backed commands.

All write actions cross authenticated backend boundaries. Readiness badges must
not be treated as substitutes for queue/runtime evidence.
