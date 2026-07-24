# Known limitations and deferred work

These are explicit product/operations facts at the Golden checkpoint. They are
not hidden defects and must not be silently upgraded to “certified”.

## Worker and physical validation

- f93c501 navigation is offline-certified and active but has no physical replay
  on the consolidated release.
- Late post-login popup stabilization is deployed/tested; the exact post-patch
  popup dismissal was not physically replayed.
- Welcome's current-release outbound bubble and Welcome-to-Follow handoff remain
  pending physical completion.
- Two simultaneous physical business runs on separate phones are not certified.
- Outreach is implemented/tested but not re-certified physically here.
- Physical latency gains for some Golden optimizations remain observational,
  not guaranteed SLAs.

## Device estate

- At `2026-07-24T15:27Z`, the heartbeat service observed two registered phones:
  one online and one ADB-unauthorized.
- Package/clone identity cannot be derived from suffixes or UI labels; it depends
  on database app-instance bindings plus physical foreground/identity proof.
- USB authorization, cable reliability, Android updates and AppCloner changes
  remain operational risks.

## Cross-repository provenance

- Vercel inspection identifies deployment
  `dpl_Ab6AKB5rXxvuGyuUiXe7f2tZc5K4` as `READY`, but exposes no Git source SHA.
- Installed BotApp `app.asar` is hash-identified but contains no provenance
  manifest and is ad-hoc signed (`TeamIdentifier` absent).
- Last coordinated Backend/BotApp source SHAs are useful historical references,
  not proof that they produced the current live artifacts.
- Local Backend and BotApp worktrees can contain unrelated/ongoing changes and
  must never be swept into this Worker checkpoint.

## Commercial and product scope

- Final consolidated Frontend/Stripe handover is deferred.
- Package changes, cancellation, agency multi-account onboarding, occupied-phone
  Auto Login and final commercial production validations require separate
  checkpoints.
- This Worker tag does not certify Stripe products/prices, webhook delivery,
  invoices, refunds, cancellation liabilities or entitlement reconciliation.

## Data and security

- Supabase backup/restore, RLS, grants and privileged-function posture are not
  recertified by a Worker documentation tag.
- Historical heartbeats/rows can be stale; live process/root and exact control
  objects remain authoritative.
- No credentials or browser sessions are stored in this repository; recovery
  therefore depends on separate owner-controlled secret backups.

## External/platform risk

Instagram UI, policy, rate limits and account protections can change without a
code deploy. The system reduces UI risk but cannot eliminate platform risk.

## Deferred improvements

- embed Git SHA/build ID in Vercel health/deployment metadata;
- embed a signed BotApp provenance manifest and notarize the app;
- maintain a redacted physical canary fixture/capture catalog;
- certify concurrent multi-phone execution;
- complete the Frontend/Stripe commercial handover;
- rehearse new-Mac and database disaster recovery.
