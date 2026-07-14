# Manual-only schedule mode

> État vérifié le 2026-07-14 contre le backend production `65c58f1` et les
> migrations Supabase appliquées. L'ancien texte « Phase 2 future » est
> **STALE** : les gates de run manuel sont désormais implémentées.

## Modèle actuel

`schedule_mode` sur `account_assignments` :

- `scheduled` — fenêtre récurrente normale (`starts_at` / `ends_at`) ;
- `manual_only` — device + `app_instance_id` réservés, sans fenêtre horaire
  récurrente et sans matérialisation automatique.

Le placement est créé par `assign_account_manual_only`. Les slots scheduled
utilisent `assign_account_slot`; les contrôles de chevauchement ne traitent pas
une row `manual_only` comme une fenêtre scheduled.

## Exclusion automatique verrouillée

`manual_only` est une exclusion dure :

- Daily Scheduler : jamais sélectionné ou matérialisé ;
- login-preflight cron : filtre `schedule_mode=scheduled` ;
- Auto Restart : `manual_only_requires_manual_trigger` ;
- projection scheduler : row non projetée comme slot récurrent.

Preuves code production :

- `lib/instagram-dashboard/schedule-session-cron.ts` ;
- `lib/instagram-dashboard/login-preflight-cron.ts` ;
- `lib/instagram-dashboard/auto-restart-scheduled-eligibility.ts` ;
- `lib/instagram-dashboard/schedule-recurrence.ts` ;
- tests `schedule-session-cron`, `auto-restart-human-resume` et
  `scheduler-tick-control`.

## Run manuel implémenté

Les gates canoniques distinguent désormais :

- `manual_start_allowed_manual_only` pour une action métier manuelle autorisée ;
- `technical_run_allowed_manual_only` pour un run technique autorisé ;
- `manual_only_requires_manual_trigger` pour toute tentative automatique.

Le backend doit encore valider disponibilité device/app instance, absence de
run concurrent, identité, quotas, package, caps, locks et sécurité. `manual_only`
n'est jamais un bypass.

## Tags requis sur chaque run manuel

- `run_trigger=manual`
- `schedule_mode=manual_only`
- `manual_run=true`
- `launched_by`
- `reason`

## Quota policy for manual runs

**Count toward daily social quotas:**

- follow
- unfollow
- like
- welcome DM
- outreach DM
- profile interaction
- any real Instagram action that changes state or contacts someone

**Do not count toward social quotas:**

- `login_provisioning`
- `login_check`
- `readiness`
- `preflight`
- device health check
- dry-run

Manual runs must not bypass quota enforcement.

## État live du snapshot

Le 2026-07-14 à 14:55 SAST, Supabase contenait deux assignments
`scheduled/reserved` et aucun assignment `manual_only`. Cette absence live ne
change pas le contrat.

Sources : code Git backend `65c58f1`, migrations source worker
`20260612172400_manual_only_schedule_mode.sql` et
`20260612183000_manual_only_validate_assignment_fix.sql`, requête Supabase
read-only du 2026-07-14.
