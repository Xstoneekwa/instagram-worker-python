# Phone Farm — Locked Decisions

> Décisions applicables au 2026-07-14. Elles priment sur les instructions plus
> anciennes lorsqu'elles ont été formulées explicitement par Liam et sont
> conservées dans Codex. Toute modification exige une nouvelle décision explicite.

## Priorité des sources

En cas de contradiction :

1. décision explicite la plus récente de Liam ;
2. état live vérifié des repos, branches, releases, symlinks et services ;
3. documentation source contrôlée ;
4. conversations Codex récentes ;
5. synthèse historique Cursor recopiée dans Codex.

Une synthèse historique n'est jamais une source de vérité live absolue.
Source : `CODEX`, décision Liam du 2026-07-14.

## Agent de développement

- Cursor est définitivement abandonné.
- Codex est l'unique agent de développement autorisé.
- Les mentions de Cursor dans l'historique Git restent des traces historiques,
  pas une autorisation actuelle.

Source : `CODEX`, 2026-07-14.

## Golden Flow

- Référence : `fb3dbc057fdbaecf7e4c11fe4847020a4515a201`.
- Tag sanctuary : `golden-follow-mute-like-returnct-110s-20260615` →
  `86a01a6d4bfcee1b761966f173f7069759ce4325`.
- Run evidence : `fc65bace-78f9-4d20-bd4d-c6ec29da583e`.
- Nominal : candidat → Follow → Mute → Like vérifié → Return CT → candidat suivant.
- Search Recovery, strict grid proof et stale action bar restent des fallbacks.
- Toute modification du cœur protégé exige GO explicite et Golden guard.

Avant tout patch Follow/latence :

1. prouver les écarts Golden/run actuel sous-étape par sous-étape ;
2. ne créer aucun timeout, retry, recovery ou mécanisme avant cette preuve ;
3. le premier patch autorisé doit recopier le Golden aussi fidèlement que possible.

Le commit `6ec8270` satisfait la copie contrôlée de trois blocs, mais il n'est
pas dans le runtime actif et reste **NOT PHYSICALLY VALIDATED**.

Sources : `CODEX`, `DOC`, `GIT`, vérifiées le 2026-07-14.

## Instagram

- Version verrouillée : `372.0.0.48.60`.
- VersionCode conservé : `377611059`.
- Golden et Propulse doivent utiliser la même version.
- Aucun changement de version n'est autorisé pour contourner un défaut runtime.
- Le snapshot du 2026-07-14 n'a pas revalidé ADB : statut
  `NOT REVALIDATED`, sans annuler la décision verrouillée.

Source : `CODEX`, vérification live antérieure du 2026-07-14.

## Réglages production

Ne jamais modifier pour rendre un test possible :

- caps ;
- schedules ;
- contrat CT ;
- actions/incidents ;
- packages/entitlements ;
- Welcome DM ;
- `dry_run` ;
- `send_enabled` ;
- templates ou réglages de compte.

Aucun run, retry, tick scheduler, Play ou geste Android sans GO explicite.
Les runs naturels sont privilégiés pour les validations production.

Source : `CODEX`, 2026-07-14.

## `manual_only`

- `manual_only` réserve device + app instance sans fenêtre horaire récurrente.
- Il est une exclusion dure du Daily Scheduler, des preflights automatiques et
  d'Auto Restart.
- Il autorise uniquement une action explicitement manuelle ou technique passant
  les gates canoniques.
- Un run manuel ne contourne ni quotas, ni identité, ni locks, ni sécurité.

Sources : `CODEX`, code backend déployé `65c58f1`, migrations source worker
`20260612172400_manual_only_schedule_mode.sql` et
`20260612183000_manual_only_validate_assignment_fix.sql`.

## Welcome DM

- Welcome DM est activé par défaut pour Pro et Premium.
- Growth et Internal Test ne l'activent pas par défaut.
- Outreach standalone active Outreach, pas Welcome.
- Seul un choix opérateur/client autorisé peut modifier la préférence ; un
  agent ne peut pas la désactiver pour contourner un bug.

Preuve live : `commercial_packages.default_welcome_enabled=true` pour `pro` et
`premium` le 2026-07-14. Source : `RUNTIME`, `GIT`.

## Contrat CT

- Source attendue : `account_follow_source_settings`.
- `max_follows_per_target_per_run=30`.
- `max_targets_per_run=4`.
- Aucun fallback silencieux `2/3` pour un compte production/scheduled.
- Aucun repair global ou silencieux pendant un run.

Source : `DOC`, `CODEX`, checkpoint du 2026-07-11.

## Preflights, identité et appareils

- Vérifier package attendu et identité du compte avant toute action métier.
- Un mismatch bloque le run.
- Aucun bypass PIN/password/pattern.
- Swipe-only contrôlé autorisé au preflight.
- Un preflight terminalement bloqué ne doit pas être relancé aveuglément.
- Locks UI/device exclusifs obligatoires pour toute interaction.

Source : `DOC`, `CODEX`.

## Git, release et production

Toujours vérifier séparément :

1. commit Git ;
2. branche distante ;
3. artefact/release construit ;
4. déploiement ou installation ;
5. symlink/alias actif ;
6. processus réellement exécuté ;
7. validation physique.

Source : décision Liam, `CODEX`, 2026-07-14.

## Langue et rapports

- BotApp reste entièrement en anglais.
- Les rapports techniques à Liam sont en français.
- Les prompts et rapports restent concis quand cela ne réduit pas la preuve.

Source : `CODEX`, 2026-07-14.
