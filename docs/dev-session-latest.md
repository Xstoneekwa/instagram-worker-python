# Dernière session dev — état du projet (mémoire courte)

## T-10 + Follow 7+1 + V2 shadow runtime — 2026-07-25

Natural Loriele and Mythyl evidence isolated the legacy Follow scroll issue to
physical row cadence being shortened by identity deduplication and partial
rows. The scoped patch prefers 7+1, preserves every Golden fallback and does
not touch Unfollow or Auto Login 07ee. T-10 needs only its two production
boolean values corrected; connected+ready remains a safe no-phone skip. Mythyl
certified V2 shadow execution and fail-open behavior with enforce false, but
lease expiry prevented persisted depth. See
[the checkpoint](./checkpoints/2026-07-25-t10-follow-scroll-v2-runtime.md).

## T-10 preflight + default Follow-source contract — 2026-07-24

The missing Vercel registration for the existing preflight cron is restored backend-side. The Worker bridge for historical 07ee performs no new UI action: after a successful publish it rereads the backend ready state and reuses the canonical create-only `30/4` Follow-source hook. See [the checkpoint addendum](./checkpoints/2026-07-24-t10-preflight-follow-source-defaults.md).

## Production baseline - 2026-07-16

The current rollback and pre-performance-optimization checkpoint is
[JULY_16_PRODUCTION_BASELINE](./checkpoints/2026-07-16-production-baseline-runtime.md).
It records the exact Worker/Backend/BotApp references, physical validation,
pending observations and the three open latency paths. Older session notes below
remain historical evidence.

*Document volatil : à mettre à jour après les prochains jalons produit / tech.*

## Return CT ambiguous own_unified — checkpoint 2026-07-12

- **Incident d'intégrité traité d'abord** : `instagram_navigation.py` était
  tronqué dans le worktree. Sauvegardes forensiques hors repo :
  `/tmp/instagram_navigation.py.truncated.<timestamp>` et
  `/tmp/instagram_navigation.py.truncated.<timestamp>.diff`.
- **Restauration ciblée** : baseline `HEAD:instagram_navigation.py` vérifiée
  complète avant restore, avec les symboles
  `post_follow_controlled_return_to_followers_list`,
  `return_to_followers_list`, `detect_followers_list_screen` et
  `run_mute_engine_v2`. Aucun reset global, checkout global ou stash global.
- **Patch** : commit
  `26cd04cd816c8b358397e5e9d4224360fb5ea54f`
  (`fix(worker): recover ambiguous followers list return`) ; le chemin
  `post_follow_return_ct_compact_ambiguous_list_safe_back_recovery` réutilise
  `post_back_det` après `compact_safe_back` et conclut
  `compact_safe_back_then_list` sur liste CT confirmée.
- **Tests Golden Flow** : py_compile, test Return CT ciblé, Golden Flow
  (`tests/test_golden_flow_open_follow_mute_return.py -q`), suite worker,
  `git diff --check` et no-leak scan passés dans le checkpoint.
- **Statut** : diagnosed / patched / tested. **Runtime pending** : aucun run
  device, Welcome validation ou Busy projection runtime ne doit être déduit de
  ce checkpoint.

## CP5 operator Stop — checkpoint 2026-07-08

- **Stop source-agnostique** : `POST /api/instagram-dashboard/stop` accepte
  Scheduler, Auto Restart, P3 resume, Play, queued/claimed/running.
- **Suppression fenêtre** : table `operator_stop_suppressions`, reason
  `operator_stop_suppressed`, expiration à `scheduled_window_end`.
- **Cleanup avant Play** : pas de réconciliation `ig_runs` prématurée ;
  `stop_cleanup_in_progress` bloque Play ; lease libérée après terminal worker.
- **Worker** : `ACCOUNT_RUN_REQUEST_ID` en env subprocess ; orchestrator vérifie
  cancel aux frontières session/welcome/follow.
- **BotApp** : Stop inchangé côté visibilité (déjà source-agnostic via
  profiles) ; labels `Stopping…`, `Stopped by operator — manual restart required`.
- **Hors scope CP5** : provisioning client CP6, classifier popup Meta.

## CP4 buffer T-10 + préflight planifié — checkpoint 2026-07-07

- **Contrat livré** : pour chaque fenêtre CP2 matérialisée,
  `business_action_deadline = session_end - 10 min`,
  `preflight_start = session_start - 10 min`. Calcul timezone IANA, minuit et DST
  couverts (`session_transition_buffer.py` + tests backend).
- **Worker (`d406831`)** :
  - `account_session_orchestrator` vérifie `business_action_deadline` avant toute
    nouvelle phase business ; terminal reason `session_transition_buffer_active` ;
  - `scheduled_session_preflight_runner.py` : verification-only (package
    foreground + identity guard), pas login/logout/action business ;
  - consumer : `scheduled_session_preflight` device-bound ; lease conservé après
    préflight OK jusqu'au handoff Scheduler.
- **Backend (`0941a3d` + `63b9663`)** :
  - table `scheduled_session_preflights` + RPCs bind/complete/get_valid/handoff ;
  - `login-preflight-cron` refondu CP4 (gate Scheduler ON avant Android) ;
  - `schedule-session-cron` : cutoff T-10 + gate `preflight_ready` + handoff lease ;
  - `auto-restart-tick` / `run-control` : P3 et Play bloqués après cutoff /
    pendant réservation préflight.
- **Scheduler OFF** pendant tout CP4 ; cron préflight techniquement actif
  (`INSTAGRAM_LOGIN_PREFLIGHT_CRON_ENABLED=true`, `DRY_RUN=false`) mais lecture
  seule tant que Scheduler OFF.
- **Hors scope CP4** : `operator_stop_suppressed` (CP5), provisioning client
  (CP6), classifier popup Meta.

## CP3 device UI lease — checkpoint 2026-07-07

- **Règle :** 1 téléphone physique = 1 opération UI active (phone-level, pas
  clone-level). Table canonique `auto_restart_device_locks` + RPC
  `acquire_device_ui_lease` / `release_device_ui_lease` /
  `reconcile_stale_device_ui_leases`.
- **Worker** (`runtime-serve-longlived`) : `DEVICE_BOUND_RUN_TYPES` étendu aux
  flows login ; renew/release inchangés ; reconcile stale au début de chaque
  `run_once` dispatcher. Reason refus : `device_lease_unavailable`.
- **Pas de préemption** ; Stop conserve le lease jusqu’au terminal (CP5 :
  `operator_stop_suppressed` hors scope).
- **Hors scope CP3 :** buffer T-10, provisioning slots CP6, popup classifier.

## CP3.1 clôture lease UI — checkpoint 2026-07-07

- **Objectif :** fermer les derniers flows UI qui ne bindaient le lease qu'au
  claim Worker (ou pas du tout). Contrat final : *aucune request UI visible par
  un Worker sans lease device-level bindé*.
- **Worker : aucun changement de code.** Le commit `131aef3` couvre déjà tous
  les `DEVICE_BOUND_RUN_TYPES` (dont `login_provisioning`,
  `login_email_code_resume`, `login_orphan_challenge_recovery`) au claim
  (transfer/acquire du lock `pending-request:<id>` puis renew), plus le
  `reconcile_stale_device_ui_leases(grace=0)` en tête de `run_once`. C'est le
  filet de sécurité ; la correction CP3.1 est **backend-only** (bind à
  l'enqueue). → Release canonique construite depuis `131aef3`.
- **Backend (scheduler-canonical-control) — gaps corrigés :**
  - `readiness-now` (connect_enqueue), `enqueue-client-connect`,
    `login-preflight-cron`, et le chemin retry-après-handoff de
    `login_email_code_resume` : acquisition + bind du lease *immédiatement après*
    la création de la request (`leaseRequestOrCancel`), sinon
    `cancel_account_run_request` + release et refus `device_lease_unavailable`.
  - `restore-login-screen` : bind lease avant que le Worker puisse claim la
    request `login_orphan_challenge_recovery`.
- **Classification :** readiness passive (`readiness_only`/`dryRun`),
  `check-readiness` client, heartbeat/notifier, scrcpy/Open Phone (manuel) =
  lecture seule, aucun lease.

## P3 auto restart après intervention humaine — checkpoint 2026-07-07

- **Flow canonique livré** : run interrompu (reason actionnable) → incident P2
  → humain corrige sur le téléphone → clic « Prêt à relancer » (BotApp) →
  autorisation durable armée → le tick Auto Restart canonique consomme
  l'autorisation **atomiquement** et crée exactement **une** request de reprise
  dans la fenêtre active → dispatcher → runner → identity guard inchangé.
  Scheduler resté **OFF** pendant tout P3 ; zéro run, zéro run request.
- **Resume plan créé tôt** : table `account_session_resume_plans` (une ligne
  par run `account_session`, worker-owned). Créée par le runner
  (`account_session_resume_plan_store.create_early_resume_plan`) **avant toute
  action device/UI**, dès que compte/assignment/device/clone/package/fenêtre
  sont connus — pour Scheduler, Play manuel et reprises Auto Restart (même
  chemin runner). Mise à jour par le dispatcher au terminal
  (`record_terminal_failure` : reason exacte conservée,
  `resume_state=awaiting_human_resume_authorization` si l'incident est
  recovery-eligible, sinon `not_recoverable`) et par l'orchestrateur en fin de
  session (`record_end_of_session`, verdict V1A). Les anciens runs sans plan
  restent `resume_plan_missing` : aucun plan rétroactif inventé.
- **Run types** : seuls les `account_session` participent au flow P3.
  `outreach_session` exclu (pas de plan précoce pour l'instant),
  login/provisioning conservent leurs mécanismes validés
  (`login_provisioning`, `login_email_code_resume`), non mélangés.
- **Autorisation humaine** : table backend `incident_resume_authorizations`
  (armed/consumed/expired/revoked). Index uniques partiels : 1 seule `armed`
  par incident, 1 seule `armed|consumed` par (compte, fenêtre) → `1 clic → 1
  request max → 1 fenêtre`, jamais ré-armable après consommation dans la même
  fenêtre. Action backend `ready_to_resume` (relay/admin, auditée,
  status/recovery only, `runCreated:false`) ; refusée hors fenêtre
  (`resume_window_closed`) ou plan non récupérable.
- **Consommation par le tick** (`processHumanConfirmedResumes` dans
  `auto-restart-tick.ts`) : fenêtre encore active sinon expiration
  (`resume_authorization_expired`), gates canoniques
  (`evaluateRunStartEligibility` trigger scheduler → `manual_only` exclu,
  aucune run/request active), claim atomique `armed→consumed` AVANT la
  création (2 ticks concurrents → `resume_authorization_consumed` pour le
  perdant), puis `create_account_run_request` (CP0) avec metadata
  `recovery_mode=human_confirmed_resume` + `incident_id` + `original_run_id` +
  `resume_plan_id` + `resume_window_key`. Échec de création après claim :
  autorisation reste consommée (anti-boucle), incident →
  `reintervention_required`. Décisions auditées dans `auto_restart_decisions`
  (`human_confirmed_resume_enqueued` / `_evaluated`).
- **Claim worker** : `validate_auto_restart_request_at_claim` route
  `recovery_mode=human_confirmed_resume` vers la validation par la table plans
  (état `resume_requested`, compte/run/fenêtre cohérents) — reprise depuis le
  préflight, session complète, quotas normaux ; identity guard **inchangé**
  (match exact, safe-stop exit 75). Un nouvel échec de reprise **enrichit
  l'incident original** (même dedupe key run original, `resume_run_id` en
  metadata, pas de spam Slack/Discord) et repasse le plan en
  `awaiting_human_resume_authorization` sans nouvelle reprise automatique.
  Une reprise réussie marque `resume_succeeded` et résout l'incident.
- **États visibles** (Admin + BotApp, dérivés de `metadata.recovery.state` +
  autorisation) : Action requise / Prêt à relancer / Reprise demandée /
  Nouvelle intervention requise / Résolu. BotApp : bouton « Prêt à relancer »
  (libellé exact) seulement si `recovery.eligible` prouvé par le backend ;
  drawer affiche fenêtre active/fermée, autorisation armée/consommée/expirée ;
  aucun Manual Retry ; le flag mort `resume_scheduling` a été retiré.
- **Reasons stables ajoutées** : `awaiting_human_resume_authorization`,
  `resume_authorization_expired`, `resume_authorization_consumed`,
  `resume_retry_window_exhausted`, `resume_plan_not_recoverable`,
  `resume_window_closed`, `resume_plan_missing` (déjà existante, réutilisée).
- **Hors périmètre P3 (inchangé)** : pas de détection/fermeture automatique
  des popups Meta/interstitials ; pas de classification de la popup Meta sans
  détecteur dédié ; Scheduler OFF bloque toujours toute reprise (gate tick +
  CP0). Prochain checkpoint : détecteur popup/interstitial, validation réelle
  d'un cycle humain → prêt à relancer → reprise contrôlée.

## P2 incidents runtime canoniques — checkpoint 2026-07-07

- **Pipeline livré** : échec runtime → incident canonique `account_incidents`
  → outbox `account_incident_notifications` → Slack + Discord → vue Incidents
  Admin (`/instagram-dashboard/incidents`) → vue Incidents BotApp (candidat).
  Scheduler resté **OFF** pendant tout P2 ; zéro run, zéro run request.
- **Taxonomy / matrice** (`runtime_incident_matrix.py`) : la vraie reason
  d'abord, `worker_exit_nonzero` uniquement en dernier fallback. Incidents
  notifiés dès le premier échec : `run_identity_verification_failed`
  (`actual_logged_in_username_not_detected`, `active_instagram_account_mismatch`),
  `assigned_instagram_package_unavailable`, login requis / challenge,
  crash/exit non-zero d'un run démarré, device indisponible en run.
  Jamais notifiés : `scheduler_disabled`, `resume_plan_missing`,
  `manual_only_requires_manual_trigger`, gates Scheduler normaux, stop manuel
  propre. Cas Mythyl : `run_identity_verification_failed` /
  `actual_logged_in_username_not_detected`, label opérateur « Impossible de
  confirmer le compte Instagram actif. Intervention humaine requise avant
  reprise. », état affiché `action_required`.
- **Point de publication canonique** : le dispatcher
  (`account_run_request_consumer._publish_run_failure_incident`) publie à la
  finalisation des runs `timed_out`/`failed` ; l'identity guard garde son
  payload mais partage la même dedupe key run-scopée
  (`incident_type:run:<run_id>`) → un seul incident par run+type, occurrences
  enrichies (`runtime_incidents.py`).
- **Notifier canonique** : service long-lived
  (`incident_notification_service.py` + `scripts/incident_notifier_service.sh`),
  composant `notifier` de `phonefarm-runtimectl`, launchd
  `com.boost.phonefarm.incident-notifier` exécuté via le pointeur
  `phonefarm-worker-current`. Legacy
  `com.openai.phonefarm.incident-notifications` désactivé (plist sauvegardé
  dans `phonefarm-runtime/run/launchd-backups`) — aucun doublon possible.
  Webhooks résolus depuis `incident_notification_channel_settings` (AES-GCM,
  `incident_notification_channel_config.py`), fallback env désactivé.
  Retries bornés (`INCIDENT_NOTIFICATIONS_MAX_ATTEMPTS=3`), déduplication par
  `delivery_key` (canal+incident), heartbeat worker, état outbox
  sent/failed/pending visible en interne. Lien interne sécurisé dans les
  messages via `INCIDENT_NOTIFICATIONS_DASHBOARD_BASE_URL` ; jamais de secret,
  package, serial ni log brut dans Slack/Discord.
- **Activation runtime** : `RUNTIME_INCIDENTS_ENABLED=true` dans
  `run-control-dispatcher.env` ; `incident-notifier.env` créé (canonical
  settings, dry_run=false, slack+discord). Release immuable `5717352`
  (branche `runtime-serve-longlived`, contient `cb2bd14`), pointeur basculé,
  dispatcher + heartbeat + notifier relancés et vérifiés sur ce root.
- **Validation production sans run client** : incident interne
  `system_test_incident` (`c26fbb8e…`, `metadata.test=true`, aucun compte
  client) → livraison Slack (HTTP 200) + Discord (HTTP 204), 1 tentative
  chacun, cycles suivants `skipped_duplicate_count=2` (aucun doublon). Le test
  est exclu par défaut des listes/compteurs (`include_test=1` pour le voir).
- **Limites réservées au prochain checkpoint** : détection spécifique popup
  Meta/interstitial, resume plan universel, bouton « Prêt à relancer »,
  Auto Restart après intervention humaine (l'action `manual_retry` est
  volontairement rejetée côté backend et retirée du drawer BotApp).

## P0 package clone → runner — checkpoint 2026-07-07

- **Cause prouvée corrigée** : pour un `account_session`, le dispatcher
  résolvait le bon clone (`app_instance` → `package_name`) mais
  `_build_runner_command` ne transmettait que `--device-serial`. Le runner
  retombait sur `com.instagram.android` (app primaire, sans compte) et
  l'identity guard safe-stoppait avec exit 75
  (`actual_logged_in_username_not_detected`) — cas réel : cold start Scheduler
  `mythyl_fitness` / A16-02 / run `622f457c` (2026-07-06 22:01 UTC).
- **Fix (commit `cb2bd14`)** :
  - `account_run_request_consumer._build_runner_command` transmet désormais
    `--package-name` et `--expected-app-instance-id` au runner pour tous les
    run types runner.py (dont `account_session`) ;
  - `runner.py` accepte ces arguments et applique le package dispatcher
    **avant** l'app readiness (log `runner_package_resolved_from_dispatcher`) ;
  - si le résolveur d'assignment interne (opt-in env) trouve un package
    différent, il gagne (lecture plus fraîche) et un warning
    `runner_package_dispatch_mismatch` est émis ;
  - **identity guard inchangé** : match exact du pseudo, safe-stop exit 75.
- **Tests** : `tests/test_runner_package_dispatch.py` (nouveau, 3 cas :
  application CLI, défaut sans CLI, précédence resolver) +
  `test_account_run_request_consumer.py` étendu (commande `account_session`
  avec/sans package). Échecs préexistants de la suite (réseau/env) identiques
  à la baseline 52d76e7 — non liés au patch.
- **Déploiement** : release immuable
  `/Users/admin/phonefarm-worker-releases/cb2bd14` (worktree, `lock_state.json`
  copié), pointeur `phonefarm-worker-current` basculé 52d76e7 → cb2bd14,
  dispatcher + device-heartbeat relancés (roots vérifiés cb2bd14, heartbeat
  cycle OK 2 phones). Scheduler resté **OFF** pendant toute l'opération :
  0 request active, 0 run, dernière request = celle de l'incident 22:00 UTC.
- **Reste à faire avant le prochain cold start `mythyl_fitness`** :
  fermer la modale de consentement Meta affichée dans `com.instagram.androif`
  (clone 2 A16-02), sinon le préflight échouera même avec le bon package ;
  décision sur l'observabilité incident (`RUNTIME_INCIDENTS_ENABLED`, reason
  `actual_logged_in_username_not_detected` non publiée en incident).

## Full-cycle minimum physique — checkpoint 2026-06-08

- **Validation fonctionnelle complète sur téléphone physique** :
  `account_session` est validé sur `i_m_your_traker` avec
  `RFGL145VCKE` / `com.instagram.androie`, assignment
  `full_cycle/full_cycle_6h`, run
  `d82c23e4-071a-46ae-9395-dda36cdfbc58` (`completed`). Le flow réel validé
  est désormais `Welcome -> Follow -> Mute/Like normal -> H3 Unfollow ->
  Outreach add-on`.
- **Résultat du run minimum** : 1 Welcome envoyé (`vipbeach`), 1 Follow
  persisté (`ctec.system`, `was_successful=true`, source
  `cafecuba_geneve`), Mute post-follow OK (`muted_posts=true`,
  `muted_stories=true`), H3 Unfollow OK (`cocolibcook`,
  `unfollowed_completed`, `follow_status=unfollowed`), Outreach add-on OK
  (`roazhontiti35`, job `sent`), cleanup DB OK.
- **Guards validés avant retry** : add-on Outreach `account_session` reste
  OFF par défaut et ne s'active qu'avec
  `ACCOUNT_SESSION_OUTREACH_ADDON_ENABLED=true`; `WELCOME_SESSION_SEND_MAX_JOBS`
  borne maintenant le scan/enqueue Welcome uniquement quand le hard cap env est
  explicitement présent; `unfollow-any` ignore les rows dont la DB porte déjà
  `unfollowed_at` et passe au candidat visible suivant.
- **Restore/no-leak validé** : les réglages Welcome temporaires ont été
  restaurés (`welcome_enabled=false`, template/baseline null), le template
  Welcome temporaire supprimé, `Welcome/Outreach pending/reserved/running=0`,
  `active runs/requests/live=0/0/0`, aucun autre recipient Outreach touché.
- **Réserve mineure à surveiller** : le Like post-follow n'a pas été observé ni
  persisté sur ce run précis (`total_like=0`), mais Mute est OK et cette réserve
  ne justifie pas de désactiver Mute/Like post-follow. Garder Mute/Like comme
  partie normale du flow; auditer Like/performance plus tard si besoin.
- **Checkpoints code liés** : add-on Outreach
  `0444b0a` / `checkpoint-account-session-outreach-addon-guarded-20260608`;
  guards retry `217500ecfcceffe5f270023b4db3e177557ebec0` /
  `checkpoint-fullcycle-minimum-retry-guards-20260608`.

## Welcome DM physique — 2026-06-08

- **Welcome list-native physique validé cap 1/2/3** : `dm_welcome_baseline`
  puis `dm_welcome_session_send` sont validés sur téléphone physique. Le chemin
  production Welcome est `followers list -> candidat -> profil/thread -> Send
  -> back followers list -> candidat suivant`, sans Search global. Le dernier
  run cap 3 validé est `609866af-3b00-4b96-bce6-4047d25ae9ef`
  (`revario_schweiz`, `vaneaurealestate`, `le12emecru`), `jobs_sent_count=3`,
  `jobs_failed_count=0`, `sender_status=success`, `run_status_updated=completed`.
- **Handoff Welcome -> Follow validé via `account_session`** : le vrai chemin
  production enchaîne Welcome optionnel, `prepare_dm_to_follow_handoff()`, puis
  Follow rotation. Test A `7967e78d-ef57-4d30-ad61-9edca67aa1a2` a validé
  1 Welcome (`chezhansi_colmar`) puis 1 Follow (`kernel_monster`) après un skip
  privé propre (`maynaa.aa`). Test B
  `4c9d22db-10f9-4882-90e5-59ef252b6c66` a validé 2 Welcome
  (`velvet_club_geneve`, `schwendi_bierundwistub`) puis 2 Follows avec
  Like/Mute ON, return CT OK, Outreach/Unfollow absents et `completed`.
- **Paramètres Welcome validés** : `WELCOME_SCAN_CANDIDATE_ATTEMPT_CAP` sépare
  cap de candidats et cap de sent; `WELCOME_SCAN_FOLLOWERS_OPEN_WAIT_S=0.6`
  est validé. Les jobs pending non-baseline créés avant anchor sont repris; les
  skips privés/non-DM-able (`dm_not_available`) ne doivent pas bloquer la
  recherche d'un autre candidat tant que le sent cap n'est pas atteint.
- **Optimisations/safety Welcome validées** : placeholder `Message…` ignoré,
  Send button physique corrigé, pas de double-send, post-job restore redondant
  évité après retour followers confirmé, DB jobs `sent_at`/`finished_at`
  propres.
- **Premier Welcome DM physique send-one validé** : le smoke manuel isolé
  `welcome_dm_physical_smoke.py --mode send-one` a envoyé le job exact
  `8df8a49f-747f-4486-9519-e8af3fc5221a` pour
  `j_automatise_pour_toi`, avec cap Welcome=1,
  Follow/Like/Mute/Outreach/Unfollow OFF et sans créer de nouveau job. Les
  détails recipient/message restent hors documentation persistée.
- **Portée du `send-one`** : ce chemin utilise la recherche globale uniquement
  pour un smoke opérateur manuel isolé. Il reste utile comme future base
  Outreach/Search DM (`search -> profil -> DM -> send -> back -> zone de
  recherche -> candidat suivant`), mais ne devient pas la baseline production
  Welcome et ses timings Search ne doivent pas être utilisés comme référence de
  performance produit.
- **Chemin production Welcome à préserver** : la voie validée sur téléphone
  physique est maintenant list-native via `dm_welcome_session_send` et
  `welcome_list_sender`. Le send-one Search reste smoke manuel / socle futur
  Outreach, pas chemin production Welcome.
- **Baseline DB** : ne pas marquer artificiellement
  `welcome_baseline_completed_at` pour ce smoke manuel strict. Le dry-run
  post-send qui remonte `no_pending_welcome_job` et
  `welcome_baseline_not_completed` est attendu après validation du job unique.
- **Correctifs runtime** : le Send button accepte maintenant la variante UI
  physique `row_thread_composer_send_button_background` / icon quand le
  container exact n'est pas sélectionnable; le retry réutilise un draft identique
  ou retape une seule fois, évite le double-send si le message est déjà visible,
  et skip le restore Search lourd pour un unique prepared send-one.

## Outreach DM physique — checkpoint 2026-06-08

- **Audit comparatif validé** : le chemin technique Outreach existant est
  `outreach_session -> dm_sender_engine -> Search -> profil -> DM -> send ->
  restore Search`. Aucun run Outreach émulateur complété n'a été retrouvé dans
  `runs/` ou docs persistants; le socle physique le plus proche validé est le
  Welcome `send-one` Search, qui partage `dm_sender_engine`.
- **Outil preflight ajouté** : `outreach_physical_smoke.py` vérifie en read-only
  l'account, `outreach_session`, assignment, package clone (`com.instagram.androif`
  ou `com.instagram.androie` selon l'instance), ADBKeyboard, absence de
  runs/requests/live views actifs, absence de jobs DM `reserved/running`, pending
  Outreach jobs, pending Welcome jobs sans les traiter, `outreach_enabled`,
  template/message Outreach, counters du jour, caps session/day/total, flags
  réels isolés et absence de tokens non résolus dans les jobs pending.
- **Validation physique 1/2/3 DMs** : Outreach est validé en réel sur
  `j_automatise_pour_toi` (`com.instagram.androif`) puis `i_m_your_traker`
  (`com.instagram.androie`). Le run 3 DMs post-fast-path
  `c2a9da0e-4600-4e78-b702-f428f694b505` a envoyé `deisantidj`,
  `pipa_polaris`, `worm.generation` avec `jobs_sent_count=3`,
  `jobs_failed_count=0`, `jobs_skipped_count=0`, `sender_status=success`,
  `previous_search_reuse_count=2`, `previous_search_reuse_fail_count=0`,
  `fallback_open_search_between_jobs_count=0`, et DB clean après run.
- **Rendu templates DM avant stockage** : `dm_template_renderer.py` rend
  `{username}` / `{{username}}`, `{name}` / `{{name}}` et
  `{account_username}` / `{{account_username}}` avant de figer
  `ig_dm_jobs.message_body`. `{name}` fallback sur `recipient_username` si aucun
  display/full name fiable n'est disponible. Les variables inconnues sont
  rejetées; le sender bloque en dernier recours tout `message_body` contenant
  encore un token.
- **Sources Outreach auditees** : sources DB/Edge supportees `n8n`,
  `dashboard`, `campaign`, `manual`. `client_dashboard`, `admin_dashboard` et
  `manual_smoke` doivent etre representes par `source` + metadata safe
  (`created_by`, `created_for`, `external_request_id`, `import_id`,
  `source_context`). `outreach_physical_smoke.py` STOP maintenant si source
  inconnue, metadata audit absente, token non resolu ou template avec variable
  inconnue.
- **Exigence UI templates dashboard** : le drawer Manage -> DM settings actif
  (`/Users/admin/Projects/boost-ai-frontend/app/instagram-dashboard/InstagramDashboardButtons.tsx`)
  affiche les chips `{username}`, `{{username}}`, `{name}`, `{{name}}`,
  `{account_username}`, `{{account_username}}` pour Welcome et Outreach,
  propose une preview rendue avec sample safe (`justperfect.eu`, `Marie`,
  `j_automatise_pour_toi`) et avertit sur les tokens inconnus. Meme exigence
  obligatoire pour le futur dashboard client.
- **Modes réels bloqués** : seul `--mode dry-run --json` est utilisable pour le
  moment. `real-send` et `send-one` sont prévus côté CLI mais retournent des
  STOP reasons explicites; le script ne lance pas Instagram et n'appelle pas le
  sender.
- **Fast paths Outreach validés sans régression fonctionnelle** :
  `dm_send_post_finalize_fast_path_used`,
  `dm_sender_composer_resolve_fast_path_from_thread_snapshot_used` et
  `dm_sender_post_job_followers_probe_skipped_outreach_restore` sont actifs sur
  le run `c2a9da0e`. La hausse de `total_post_job_ms` observée sur ce run vient
  du restore inter-job job1/job2 (premier back-stack timeout puis hardware-back
  réussi), pas du finalize post-send, qui reste court (~4.7s, ~3.1s, ~3.0s).
- **Caps produit rappelés** : V1 safe Outreach 10 DMs/jour/compte; après 3-5
  jours propres 20/jour; après validation sans restriction 30-40/jour; hard cap
  prudent 50-60/jour max pour un compte solide.
- **Unfollow standalone physique validé** : `i_m_your_traker` sur
  `RFGL145VCKE` / `com.instagram.androie`, mode `unfollow-any`, a validé
  cap 1 réel (`30b39e54`, target `pauline.hphotographie`) puis cap 2 réel
  (`4c82f481`, targets `pause_energie.lise`, `facchinettilaurent`).
  `unfollow_effective_limits_resolved` respecte
  `min(db_session,env_hard_cap,runtime_mode_cap,db_day_remaining)`, le dispatcher
  accepte `full_cycle/full_cycle_6h -> unfollow_session`, et le dry-run probe
  route bien `unfollow-any` vers `_evaluate_visible_unfollow_any_with_session_cache`.
  Le recoverable `heureusecoincidence/actions_sheet_signals_missing` n'a pas été
  persisté comme succès; `return_to_following_list_ok=true`.

## État chantier follow — 2026-06-07

- **Non-régression Follow via handoff** : les runs `account_session` Test A/B ont
  confirmé que le handoff Welcome ne casse pas CT checkpoint V1, private skip,
  scroll followers-list, post-follow mute, post-follow like, return CT et
  accounting. Test B a persisté 2 follows, 2 Likes et 2 mutes posts/stories
  avec `run_status_updated=completed`.
- **9/3/3 physique validé** : run `5ad58174-95a0-45f2-91d7-33fcfdc3b5a0`
  sur `j_automatise_pour_toi`, `completed`, 9/9 follows, 9 mutes, 9 Likes,
  9/9 return CT, `run_status_updated=completed`, deferred persist terminé
  avant completed, aucun `subprocess_timeout`, aucun private follow. CT validés:
  `messodie_creations`, `pumptracktour`, `home.id_cil`.
- **Scroll followers-list phone physique** : le scroll fort initial est remplacé
  par une stratégie progressive followers-list confirmée. `soft_initial`
  utilise `distance_ratio=0.25` et `steps=4`, `soft_retry`
  `distance_ratio=0.27` et `steps=4`; `strong_search` reste réservé au
  troisième scroll exhausted après deux soft scrolls sans candidat followable.
  Les logs incluent `followers_list_scroll_strategy_selected`,
  `followers_list_soft_scroll_started/completed`,
  `followers_list_strong_scroll_started/completed`, `expected_new_rows_min=7`
  et `visible_before_count`.
- **4/2/2 validés fonctionnellement** : les derniers runs contrôlés sur
  `j_automatise_pour_toi` ont confirmé le budget multi-target `4 follows /
  2 CT / 2 follows par CT`, avec follow, mute, like/skip métier et return CT
  corrélés. Les runs utilisés pour le diagnostic incluent `51720464`,
  `3521ba8f` et `34ae4947`.
- **Private skip fast path** : validé sur profils privés rencontrés
  (`_nataniel_06_`, `fouebastard`, `michel_di_rosa`, `maynaa.aa` selon les
  runs). Aucun `follow_tap_sent` sur privé ; retour CT puis continuation du
  scan OK.
- **CT Checkpoint V1 runtime-only** : ledger en mémoire par CT/source pour
  visible window, candidats vus/rejetés/private/followés, fast-skip et
  observabilité. Il est utile dans la session courante mais ne connaît pas les
  runs précédents ; la validation dense cross-run attend V2 DB persistée.
- **Correction intra-CT post-return** : le skip de revalidation liste et de
  screenshot post-return utilise maintenant une preuve fraîche
  `post_follow_return_ct_success` (`source_username`, return method safe,
  committed surface, même scroll/run/account) au lieu de dépendre seulement du
  TTL du checkpoint visible-window.
- **Correction No Posts précoce** : le fallback visuel coûteux No Posts ne doit
  plus tourner sur profils normaux avec surface profil + grille confirmées et
  sans hint No Posts. Il est gated par signaux forts/ambigus et garde le
  fallback existant si la preuve est insuffisante.
- **Validation finale 4/2/2 `f2eb4d2b`** : run visuellement OK sur
  `bonjour_cluses` puis `messodie_creations`, avec 4 follows, 3 Likes persistés
  et 1 skip Like métier `No posts yet` (`flamingsonic_8084h`) sans faux
  `post_likes_persisted`. `run_status_updated=completed`.
- **Legacy-safe scroll-first** : si le pre-reveal donne
  `profile_tabs_bottom_unknown`, le flow ne tente plus legacy-safe avant scroll ;
  il logge `post_follow_like_legacy_safe_pre_scroll_skipped`, fait le reveal
  safe, puis relance legacy-safe avec viewer proof et Like verify inchangés.

## CT Checkpoint / Fast-Skip Roadmap

- **V1 runtime-only** : valider vite dans `runner.py` un checkpoint sûr par CT/source, sans migration DB et sans bypass des guards private/social/screen/follow.
- **V2 DB persistée après validation plus large** : synchroniser la progression CT entre worker Python, dashboard admin, futur dashboard client, BotApp et autres opérateurs.
- Les champs V1 restent proches de la future table : `account_id`, `source_target_id`, `source_username`, `last_run_id`, `last_scroll_index`, candidats vus/rejetés/private/followés, `checkpoint_reason`, `checkpoint_status`, `stale_after`, `created_at`, `updated_at`.
- V2 devra auditer les resets/admin mutations, expirer les checkpoints stale, et ne jamais exposer secrets, sessions, screenshots bruts ou XML brut.
- **V2 DB persistée (plus tard)** : prévoir compatibilité dashboard web,
  future BotApp, multi-admin, audit/reset manuel, stale expiration et
  synchronisation multi-device/multi-clone. Ne pas intégrer cette persistance
  dans le commit V1 runtime. Attendre plus de cas réels : CT dense avec rejets,
  fast-skip utile, scroll resume planned/applied et contrat dashboard/BotApp.

## Décisions de sécurité follow

- Aucun bypass des guards critiques : private gate, social memory, screen guard,
  follow proof, follow verify.
- Aucun faux Like / faux completed : un profil `No posts yet` prouvé doit être
  un succès follow+mute avec Like skipped métier, sans `post_likes_persisted`.
- Aucun tap magique ni coordonnée fixe ajoutée dans ces optimisations.
- Les candidats inconnus restent dans le flow normal : pas de fast-skip d'un
  profil non vu/rejeté/followé par le ledger runtime.

## Ce qui fonctionne

- **Ouverture followers visuelle** : chemin opérationnel dans les runs récents.
- **Row mapping** : géométrie / candidats cohérents pour la sélection de ligne.
- **Ouverture profil candidat** : le candidat peut être ouvert depuis le flow visual.
- **Follow exact / harvester** : **partiellement** stable — reste sensible aux variations UI / timing.
- **Filters P0 etendu** : `skip_private_profiles`, `min_followers`,
  `max_followers` et `min_posts` sont branchés Dashboard -> API -> Supabase
  -> `/runs/start` -> worker runtime. Frontend `61b96fd` pousse sur
  `origin/main`; worker `367a226` pousse sur
  `origin/stable-follow-working-state`.
- **Schedule / app instances / lifecycle admin** : validé et poussé.
  Worker `2a7b6f5` + correctif `383911e`; frontend `8bfab6f`.
  `cinema_catchup` est `full_cycle`, slot `18:00 - 00:00`, assignment
  `reserved` sur la `primary_app` index 0. Résumé app instances:
  `3 free · 1 occupied · 0 blocked`.
- **P1b rotation targets + Sources rotation settings** : validé et poussé.
  Worker `93717ecc3b7828a01a00ca1ee8c81d077a821109` sur
  `stable-follow-working-state`; frontend
  `b9f5fb4c23b745b084bedc831e69e892598e4f60` sur `main`. Le worker charge
  plusieurs targets eligible, switch sur exhaustion ou budget target atteint,
  conserve l'attribution `target_id`, et ne switch pas silencieusement sur
  login/checkpoint/credentials/device/rate limit/crash. Settings Sources via
  `account_follow_source_settings` et
  `/api/instagram-dashboard/settings/follow-sources`, defaults 2/3, bounds
  1..50 et 1..10.

## Garde-fous Contact / Follow

- **Contact** ne doit **plus** être tapé comme **Follow** (exclusions et scoring dans le Follow Action Engine).
- **Contact** reste une surface **à préserver** pour un **futur usage** type scraper email / contact Instagram (`future_use` / tagging métier selon implémentation).

## Dernier problème critique

- **Dérive post-follow** et **retour CT instable** : après follow / request OK (y compris privé + mute skipped), le return CT pouvait entraîner des surfaces **dangereuses** (posts/stories, composer commentaire, message).
- Impact : risque pour le compte, sessions arrêtées manuellement, logs incohérents avec l’UI réelle si la désynchronisation n’était pas corrigée.

## Priorité actuelle

- **P1c target metrics / FBR** : prochain bloc après P1b. Ajouter les métriques
  durables par target (`follows_sent`, followbacks, FBR, `last_used_at`,
  cooldown) avant de déclarer le multi-target runtime-ready.
- **P2 runs contrôlés obligatoires** : aucun passage runtime-ready multi-target
  sans runs tests multi-targets contrôlés. Ne pas appliquer 30/4 par défaut.
- **UI Sources visuel pending** : le smoke navigateur local redirige vers
  `restaurant-login`; le contrat API/code Sources est validé, mais la validation
  visuelle drawer reste à refaire avec session admin.
- **Post-follow drift safety guard** : détection de surfaces à risque, **un seul** back contrôlé si safe, **budget temps** par round, **abort** propre sans navigation exploratoire, logs explicites, traitement **partial success** côté runner quand le follow est validé mais le CT return échoue / abort.

## Fichiers de référence (lecture)

- Détails navigation / vision / follow / post-follow / recovery : voir les autres fichiers sous `docs/` et la carte dans `AGENTS.md`.

## Checkpoint préparé — 2026-07-23

- Follow warmup : journées actives SAST basées sur `follow_verified`, pas âge
  calendaire.
- Packages : defaults et maxima jour/session séparés.
- Account settings : valeurs configurées persistées, modifiables vers le bas.
- Worker : enforcement final indépendant et fail-closed.
- Tests : 26 ciblés + 156 gardes, zéro run device.
- Code Worker : `51278eadf1613f80349a7930e17420c8d8dd1e64`.

## Handover — Follow adaptive scroll + garde startup Auto Restart 2026-07-25

- Baseline du correctif Follow :
  `7d2797eb72bbc3432415bd9a3eccc907f3e053b0`.
- Cause du risque de déploiement : `run_forever()` initialisait
  `last_auto_restart_tick=0`, rendant le premier tour de boucle immédiatement
  éligible au POST backend Auto Restart.
- Garde ajouté : token local régulier `0600`, préparé atomiquement par
  `prepare-auto-restart-startup-skip`, consommé par rename puis supprimé au
  prochain startup. Log attendu : `auto_restart_startup_tick_skipped`.
- Le tick startup est opportuniste : heartbeat, préflight, claim et santé du
  dispatcher démarrent normalement sans lui. Le tick naturel suivant reste
  éligible après la cadence locale normale et la politique backend reste active.
- État V2 à préserver : Mythyl uniquement, shadow, `enforce=false`, legacy
  autoritaire.
- Validation physique et run Instagram interdits dans ce checkpoint. Attendre
  un GO Liam séparé.
- Rollback : armer un nouveau token one-shot, repointer atomiquement le symlink
  vers la release précédente, redémarrer une seule fois et vérifier token
  consommé, PID unique, root correct et zéro request/run/lock créé.
