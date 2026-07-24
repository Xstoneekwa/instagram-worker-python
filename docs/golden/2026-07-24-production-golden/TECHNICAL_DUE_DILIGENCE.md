# Technical due diligence

## Executive summary

The asset is a production-oriented Instagram Phone Farm with separated control,
execution and operator planes. Its strongest properties are immutable Worker
releases, exact device/app-instance binding, bounded UI state machines, durable
request/run ledgers, explicit incident reasons and a substantial offline test
suite. The largest acquisition risks are cross-repository artifact provenance,
physical-test coverage gaps, reliance on Instagram UI stability and operational
dependence on local macOS/Android infrastructure.

## Assets

- Worker source and 1850-test certification at exact commit `e7f54a9`;
- immutable release and atomic runtime pointer;
- historical Auto Login 07ee sanctuary plus explicit adapter;
- shared list-continuation contract and Golden Follow invariants;
- Supabase data/control model for requests, runs, assignments, locks, incidents,
  commercial settings and evidence;
- Next.js backend/client/admin surfaces deployed on Vercel;
- Electron BotApp operations surface;
- macOS launchd controller, heartbeat and notifier services;
- physical phones and cloned Instagram packages.

## Strengths

- safe-stop and bounded recovery preferred over blind UI action;
- exact machine-readable failure reasons and correlation IDs;
- renderer/service-role separation;
- no runtime dependence on mutable source checkouts;
- tests distinguish implemented, deployed and physically validated states;
- documented rollback, new-host installation and disaster recovery paths;
- package, warmup and account-cap layers are explicit rather than hardcoded per
  account.

## Material risks

| Risk | Impact | Current control | Recommended next step |
|---|---|---|---|
| Instagram UI/API changes | Broken automation or false action | XML + vision + state + bounded recovery | Maintain fixture/replay library and canary rollout |
| Live Backend Git SHA unavailable from Vercel inspection | Harder exact rollback/audit | Deployment ID and URL recorded | Embed Git SHA in deployment metadata/health endpoint |
| BotApp package lacks embedded provenance and Developer ID team | Supply-chain/audit ambiguity | Installed asar hash recorded | Embed signed provenance manifest and notarize |
| One phone ADB-unauthorized at snapshot | Reduced capacity | Heartbeat visibility and isolation | Repair manually under separate GO and re-certify |
| f93 navigation not physically replayed | Offline/production evidence gap | 224 targeted + 1850 full tests | Controlled canary run under separate GO |
| Late-popup post-patch path not physically replayed | Residual Auto Login UI risk | Existing handler + bounded tests | One controlled canary when authorized |
| Welcome current-release proof incomplete | Potential handoff uncertainty | Bounded state machine/tests | Natural observation with no manufactured data |
| Local Mac is an operational dependency | Host outage stops execution | launchd, immutable releases, restore docs | Build a second host from INSTALL_NEW_MAC |
| External credentials | Business continuity/security exposure | Out-of-repo env and rotations | Maintain owner-controlled secret inventory and rotation SOP |

## Security posture

- service-role credentials stay outside renderer/browser bundles;
- operational docs intentionally omit tokens, cookie values, passwords and raw
  device XML;
- incidents/notifications use redacted structured payloads;
- privileged backend functions and RLS/grants require separate database audits;
- the installed BotApp is not Developer-ID signed at this snapshot, so package
  integrity depends on its recorded hash and trusted local installation path.

## Scalability

The architecture supports multiple phones, clones and accounts through explicit
assignments and leases. Scaling requires proving concurrent physical execution,
capacity monitoring, rate/cap policies, safe account onboarding and operational
repair procedures. Database and Vercel scaling are external to this Worker tag.

## Maintenance cost drivers

- Instagram layout/localization drift;
- Android/AppCloner package inventory and ADB authorization;
- physical canary testing;
- multi-repository release coordination;
- Supabase migration/grant auditing;
- commercial/Stripe reconciliation;
- incident and notification noise control.

## Acquisition verification checklist

- verify repository ownership and remote access;
- resolve the Golden tag and reproduce tests on a clean host;
- verify Vercel project ownership, domains and deployment history;
- verify Supabase project ownership, backups, migrations, RLS and grants;
- verify BotApp source-to-package reproducibility and signing plan;
- inventory phones, clones, app-instance bindings and replacement cost;
- review credential custody and rotation without exposing values;
- execute a separately authorized canary and rollback drill;
- review Stripe products, prices, webhooks, cancellations and entitlement
  reconciliation in the dedicated commercial handover.

## Valuation boundary

This checkpoint provides strong Worker-level reproducibility and transparent
known gaps. It does not independently certify revenue, customer contracts,
Instagram policy compliance, Stripe liabilities, privacy/legal compliance or
all live external infrastructure. Those require business/legal diligence.
