# Dernière session dev — état du projet (mémoire courte)

*Document volatil : à mettre à jour après les prochains jalons produit / tech.*

## CT Checkpoint / Fast-Skip Roadmap

- **V1 runtime-only** : valider vite dans `runner.py` un checkpoint sûr par CT/source, sans migration DB et sans bypass des guards private/social/screen/follow.
- **V2 DB persistée obligatoire après validation V1** : synchroniser la progression CT entre worker Python, dashboard admin, futur dashboard client, BotApp et autres opérateurs.
- Les champs V1 restent proches de la future table : `account_id`, `source_target_id`, `source_username`, `last_run_id`, `last_scroll_index`, candidats vus/rejetés/private/followés, `checkpoint_reason`, `checkpoint_status`, `stale_after`, `created_at`, `updated_at`.
- V2 devra auditer les resets/admin mutations, expirer les checkpoints stale, et ne jamais exposer secrets, sessions, screenshots bruts ou XML brut.

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
