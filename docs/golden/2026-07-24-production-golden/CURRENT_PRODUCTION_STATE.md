# Current production state

Snapshot date: 2026-07-24. Values are time-stamped evidence, not permanent
assumptions.

## Worker runtime

| Field | Value |
|---|---|
| Business commit | `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f` |
| Immutable release | `abf0ebf-f93c501-navigation-consolidated-v1` |
| Active symlink target | `/Users/admin/phonefarm-worker-releases/abf0ebf-f93c501-navigation-consolidated-v1` |
| Runtime root check | `true` |
| Dispatcher | healthy, launch-enabled, one process, queue empty |
| Heartbeat publisher | healthy, one process, correct root |
| Tests | `1850/1850` |

At `2026-07-24T15:26:39Z`, Supabase read-only counts were 176 total
requests/0 active, 144 total runs/0 active and 0 active locks. At
`2026-07-24T15:27:06Z`, dispatcher preflight was ready with no queued request.

## Product flows

- Auto Login: 07ee isolated engine active through its adapter; clone binding and
  identity guard validated; late-popup stabilization deployed.
- Follow: Golden action flow plus f93c501 list continuation active.
- Welcome: shared Suggestions boundary active; final current-release physical
  outbound/handoff proof remains pending.
- Unfollow: J+3, protected-row and time/cap guards active.
- Outreach: implemented and test-covered; not re-certified physically here.
- Incidents: structured reasons, operator review, idempotent notifications.
- Scheduler: embedded dispatcher tick; BotApp is not authority.
- Warmup: active SAST business days, then configured/package maximum.

## External surfaces

- Frontend/backend: `https://www.boostmybusinesses.com` resolved to Vercel
  production deployment `dpl_Ab6AKB5rXxvuGyuUiXe7f2tZc5K4`, `READY`.
- BotApp: `/Applications/BotApp.app`, bundle version `0.1.0`; installed asar hash
  is recorded in `golden-evidence/CROSS_REPO.md`.
- Phones: heartbeat service operational; one phone online and one
  ADB-unauthorized at snapshot time.

## No-change statement

Creating this checkpoint changes documentation, Git branch/tag references and
nothing else. It does not change the active symlink, dispatcher, scheduler,
database, Vercel deployment, BotApp package, phone or Instagram session.
