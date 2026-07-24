# Locked Decisions

## GOLDEN_PHONEFARM_PRODUCTION_2026_07_24

Any change to these decisions requires explicit Liam approval and a new
certified checkpoint.

- The runtime Worker is commit `e7f54a9`; documentation commits do not replace
  the immutable business release.
- Auto Login `login_provisioning` routes through
  `historical_auto_login_07ee_adapter.py` into the isolated 07ee engine.
- The historical 07ee source stays immutable. The only certified overlay is the
  bounded post-`connected_home` stabilization that lets existing popup handlers
  process a late prompt.
- Package and app-instance binding are authoritative. Suggested usernames are
  not active identity proof, and there is no fallback from a bound clone to the
  primary Instagram package.
- A successful Auto Login must verify the expected foreground package and final
  account identity before publishing `connected`.
- Follow navigation uses the shared `instagram_list_continuation` contract:
  positional viewport continuity, bounded soft scroll, actionable `See more`,
  fresh Suggestions-boundary confirmation and controlled CT rotation.
- Follow, Welcome DM and Unfollow share the boundary classifier, while Welcome
  and Unfollow retain their own business/action policies.
- Golden Follow remains candidate → Follow → Mute → post → verified Like →
  Return CT. Story/Facebook, identity and safe-stop protections remain mandatory.
- Warmup advances only on distinct active SAST dates with persisted successful
  `follow_verified` evidence: 10, 20, 40, then configured/package limits.
- Account caps remain persistent settings. Effective limits are the minimum of
  account, package, warmup, ops and remaining-quota constraints.
- Electron BotApp is an operations surface, not scheduling authority. The
  durable launchd dispatcher and backend scheduler contract are authoritative.
- The embedded scheduler is part of the dispatcher; no second scheduler service
  may be created.
- One phone owns one UI session. Device, account, app instance and lease scopes
  must remain isolated.
- Renderer/browser code never receives privileged Supabase credentials.
- Incidents retain exact machine reasons; human review is a separate canonical
  state and notifications remain idempotent.
- No test may be manufactured by changing package, caps, schedule, entitlement
  or account data.
- No automatic retry, physical run, migration or deployment is implied by this
  documentation checkpoint.

Historical July 16 decisions remain applicable where they do not conflict with
this Golden checkpoint.
