# Phone Farm — Current Production State

> Snapshot vérifié le **2026-07-14 à 14:55 SAST** (`2026-07-14T12:55:56Z`).
> Ce document est un instantané, pas une découverte dynamique. Toute utilisation
> opérationnelle doit revalider les cinq niveaux séparément : **Git**, **build**,
> **déploiement/installation**, **activation**, **validation physique**.

## Légende des sources

- `CODEX` : décision explicite ou observation conservée dans une tâche Codex.
- `DOC` : documentation versionnée existante.
- `GIT` : commit, tag, branche ou ref GitHub vérifié.
- `RUNTIME` : symlink, processus, Vercel ou Supabase vérifié en lecture seule.
- `UNKNOWN` : preuve insuffisante.
- `STALE` : preuve historiquement utile mais remplacée par une preuve plus récente.
- `NOT PHYSICALLY VALIDATED` : code ou artefact prêt sans validation complète sur appareil.

## Résumé exécutable

| Couche | Git | Construit | Déployé / installé | Actif | Validation physique |
|---|---|---|---|---|---|
| Worker | `9b7fa2f` poussé | release immuable propre | `9b7fa2f-followers-suggestions-boundary` | symlink + PID `96576` sur cette release | **NOT PHYSICALLY VALIDATED** pour le correctif Suggestions |
| Backend | `65c58f1` poussé | build Vercel READY | `dpl_7wHp4NShgJLqyYZ3d5S5ZCG2ctxg` | alias production actifs | routes et UI partiellement observées ; drawer final non confirmé visuellement |
| BotApp | `b812370` poussé | bundle `app.asar` construit | installation officielle hash-identique au build | heartbeat scheduler frais ; PID direct `UNKNOWN` | deux incidents visibles ; `Mark reviewed` dans le bundle, drawer non confirmé visuellement |
| Supabase | migrations appliquées | n/a | projet `ACTIVE_HEALTHY` | zéro run/request/preflight actif | état DB vérifié, pas une validation device |

Sources : `GIT`, `RUNTIME`, `CODEX`, vérifiées le 2026-07-14.

## Worker Python

### Git

- Dépôt : `Xstoneekwa/instagram-worker-python`.
- Branche : `codex/followers-suggestions-boundary-20260714`.
- Commit : `9b7fa2f216448b5f3106468b2f3569a644f87080`.
- Sujet : `fix(worker): recognize followers suggestions boundary`.
- Ref GitHub vérifiée à la même valeur le 2026-07-14. Source : `GIT`.

### Release construite

- Release : `9b7fa2f-followers-suggestions-boundary` sous le root canonique des
  releases worker.
- HEAD disque : `9b7fa2f216448b5f3106468b2f3569a644f87080`.
- Worktree release propre au moment du contrôle. Source : `RUNTIME`.

### Activation réelle

- Symlink canonique : `phonefarm-worker-current`.
- Cible : release `9b7fa2f-followers-suggestions-boundary`.
- Consumer : PID `96576`, démarré le 2026-07-14 à 14:11:53 SAST.
- CWD du PID : la même release `9b7fa2f-followers-suggestions-boundary`.
- Incident notifier : PID `1170`, encore exécuté depuis
  `b6fedca-plus-search-surface-recovery` ; cette divergence est ouverte.
- Heartbeat dispatcher Supabase : `idle`, frais à 14:55:56 SAST, mais
  `git_sha=unknown`. Le CWD du processus reste donc la preuve de commit active.

Source : `RUNTIME`, 2026-07-14.

### Validation physique

- Le correctif `followers_suggestions_boundary` de `9b7fa2f` a été testé avant
  promotion et activé, mais aucun nouveau Play/run physique n'a été exécuté
  après sa promotion.
- Verdict : **NOT PHYSICALLY VALIDATED**.
- Deux device heartbeats étaient `online` et frais ; cela ne valide pas le flow.

Source : `CODEX`, `RUNTIME`, 2026-07-14.

## Backend Next.js / Vercel

### Git

- Dépôt : `Xstoneekwa/boost-my-businesses-frontend`.
- Branche : `codex/incident-mark-reviewed-drawer-20260714`.
- Commit : `65c58f10f18b2bf5067b555757ce168ee9e3b291`.
- Sujet : `fix(botapp): expose linked operator review in incidents`.
- Ref GitHub vérifiée le 2026-07-14. Source : `GIT`.

### Build et déploiement

- Projet Vercel : `boost-my-businesses-ai-frontend-vercel`.
- Deployment : `dpl_7wHp4NShgJLqyYZ3d5S5ZCG2ctxg`.
- État : `READY`, target `production`.
- Alias publics actifs : `www.boostmybusinesses.com` et
  `boostmybusinesses.com`.
- Le commit `65c58f1` est explicitement présent dans les métadonnées Vercel.

Source : `RUNTIME` Vercel, vérifiée le 2026-07-14 à 14:55 SAST.

### Validation fonctionnelle

- Le backend expose la corrélation incident → action Operator Review et la
  projection `stopping` héritée de `3b0dcff`.
- Le build et le déploiement sont prouvés.
- Les deux incidents attendus étaient visibles dans BotApp.
- La présence visuelle de `Mark reviewed` à l'intérieur de chaque drawer n'a
  pas pu être confirmée à cause de `noWindowsAvailable` dans Computer Use.
- Verdict : déployé et partiellement observé ; **NOT PHYSICALLY VALIDATED** pour
  l'interaction complète du drawer.

Source : `CODEX`, `GIT`, `RUNTIME`, 2026-07-14.

## BotApp macOS

### Git

- Dépôt : `Xstoneekwa/phone-farm-botapp`.
- Branche : `codex/botapp-incident-mark-reviewed-20260714`.
- Commit : `b8123709ae03cdf4dace6f6e54e4a0f4dd4ab2ec`.
- Sujet : `fix(botapp): add incident operator review action`.
- Ref GitHub vérifiée le 2026-07-14. Source : `GIT`.

### Build et installation

- Bundle construit : `release/mac-arm64/BotApp.app`.
- Application officielle : bundle macOS Applications `BotApp.app`.
- SHA-256 `app.asar` construit et installé :
  `259667d0b6174a7b2206cd67656b7282c8950eaf250d25e96996435e82ed4c5d`.
- Les deux fichiers avaient la même taille (`10515106` octets) et la même date
  de modification (`2026-07-14 14:40:32 SAST`).
- Le bundle actuel ne contient pas encore de `package-provenance.json`.

Source : `RUNTIME`, 2026-07-14.

### Activation et validation

- Heartbeat `botapp-scheduler-runtime:*` : `idle`, frais à 14:55:33 SAST.
- PID macOS directement attribuable au bundle : `UNKNOWN` lors du snapshot.
- Relay et dispatcher ont été observés opérationnels dans BotApp.
- Deux incidents étaient visibles : `followers_surface_lost` et
  `recovered_snapshot_rejected`.
- `Mark reviewed` est présent dans le bundle testé ; ouverture du drawer non
  confirmée visuellement.

Source : `CODEX`, `RUNTIME`, 2026-07-14.

## Supabase

- Projet : `boost-my-businesses-ai` (`zgafnshkjywfltxgbtzg`).
- Région : `eu-west-1`.
- État : `ACTIVE_HEALTHY`.
- PostgreSQL : 17.
- Dernière entrée live appliquée :
  `20260713231003_operator_review_canonical_transition`. Le fichier source
  contrôlé correspondant dans le backend est
  `supabase/migrations/20260714003000_operator_review_canonical_transition.sql`.
- Entrée live du lock critique :
  `20260712232853_drop_ambiguous_acquire_device_lock_overload`. Le fichier
  source contrôlé correspondant est
  `supabase/migrations/20260710160200_drop_ambiguous_acquire_device_lock_overload.sql`.

Les timestamps du registre live et des fichiers Git diffèrent : les deux noms
sont conservés et ne doivent pas être présentés comme un même identifiant.

État live à `2026-07-14T12:55:56Z` :

- requests actives : `0` ;
- runs actifs : `0` ;
- preflights actifs : `0` ;
- devices online avec heartbeat < 5 min : `2` ;
- assignments : `2 scheduled/reserved`, `0 manual_only` ;
- dernier snapshot followers : `2026-07-14T00:30:26Z` ;
- action Operator Review non terminale : `1`.

Source : `RUNTIME` Supabase, lecture seule.

## Instagram / appareils

- Version verrouillée : `372.0.0.48.60`, versionCode `377611059`.
- Vérification antérieure conservée dans Codex : deux Samsung A16 alignés.
- Le 2026-07-14 à 14:43 SAST, la revérification ADB n'a pas abouti car le daemon
  était arrêté et son démarrage a été refusé par le sandbox.
- Aucun daemon ADB n'est resté lancé et aucune commande device n'a été exécutée.
- Statut actuel : **NOT REVALIDATED IN THIS SNAPSHOT** ; les heartbeats prouvent
  seulement que deux devices sont online.

Source : `CODEX` pour la version ; `RUNTIME` pour les heartbeats.

## Crons Vercel déployés

| Route | Schedule | État de preuve |
|---|---|---|
| `/api/instagram-dashboard/schedule-session/cron` | `*/5 * * * *` | présent dans le commit Vercel actif |
| `/api/instagram-dashboard/login-preflight/cron` | `*/5 * * * *` | présent dans le commit Vercel actif |
| `/api/cron/commercial-lifecycle-expiry` | `*/10 * * * *` | présent dans le commit Vercel actif |
| `/api/cron/client-email-lifecycle` | `*/15 * * * *` | présent dans le commit Vercel actif |
| `/api/cron/instagram-follower-snapshots` | `30 0 * * *` | présent ; snapshot live observé |
| `/api/instagram-dashboard/targets/auto-archive-low-fbr-cron` | `0 3 * * *` | présent dans le commit Vercel actif |

Source : `GIT` commit `65c58f1`, `RUNTIME` Vercel/Supabase.

## Écarts et UNKNOWN à ne pas masquer

1. Le patch Golden `6ec8270` est poussé mais absent de `9b7fa2f` et du runtime.
2. `9b7fa2f` n'a pas encore de validation physique post-promotion.
3. Le drawer `Mark reviewed` n'est pas encore confirmé visuellement.
4. Le notifier d'incidents exécute une ancienne release.
5. Les heartbeats worker publient `git_sha=unknown`.
6. Le PID direct BotApp était `UNKNOWN` au moment du snapshot.
7. La version Instagram n'a pas été revalidée par ADB dans ce snapshot.
8. Les RPC `SECURITY DEFINER` exposés à `anon/authenticated` sont inventoriés
   dans le backend `docs/RPC_EDGE_AUTH_MATRIX.md` et nécessitent une revue.

## Documents liés

- [RELEASE_REGISTRY.md](RELEASE_REGISTRY.md)
- [LOCKED_DECISIONS.md](LOCKED_DECISIONS.md)
- [FEATURE_STATUS_MATRIX.md](FEATURE_STATUS_MATRIX.md)
- [golden-evidence/README.md](golden-evidence/README.md)
- Backend : `docs/botapp-scheduler-runtime-contract.md`
- Backend : `docs/INCIDENTS_AND_OPERATOR_REVIEW.md`
- Backend : `docs/STRIPE_TEST_LIVE_MATRIX.md`
- Backend : `docs/RPC_EDGE_AUTH_MATRIX.md`
- BotApp : `docs/botapp-relay-dispatcher-architecture.md`
