# Dernière session dev — état du projet (mémoire courte)

*Document volatil : à mettre à jour après les prochains jalons produit / tech.*

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
