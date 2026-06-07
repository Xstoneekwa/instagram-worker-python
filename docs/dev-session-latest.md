# Dernière session dev — état du projet (mémoire courte)

*Document volatil : à mettre à jour après les prochains jalons produit / tech.*

## État chantier follow — 2026-06-07

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
