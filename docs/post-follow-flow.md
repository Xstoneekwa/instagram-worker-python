# Post-follow flow

## Phase post-follow

Après un follow (ou équivalent **Requested** / skip “déjà abonné”), le **post-follow** regroupe :

1. **Observation** de l’UI (navigation engine, hints overlay, fingerprint).
2. Décision **mute** (selon config et cas privé / request pending).
3. **Retour** vers la liste followers du **compte CT** (return CT).
4. Gestion des **succès partiels** et de la **dérive** (guards dédiés).

Le code d’orchestration vit principalement dans `instagram_navigation.py` (phase dédiée + retour CT contrôlé) et le **runner** décide des compteurs / social memory / continuation de boucle.

## Follow / request reconciliation

Le runner et `perform_follow_safe` peuvent **réconcilier** le résultat métier avec l’UI réelle lorsque le XML annonce un échec (ex. code 33/34) mais que l’écran montre **Following** / **Requested**, ou que des événements / la phase mute prouvent un succès effectif.

Objectif : éviter les **fausses erreurs** “follow failed” et les mauvais routages (retour CT en mode échec follow alors que l’action a réussi).

## Mute posts / stories

Si la config active le **mute réel** après follow :

- ouverture du menu “Following” / options ;
- feuille mute ;
- toggles **Posts** / **Stories** selon les flags ;
- **checkpoint** post-mute (fermeture contrôlée des overlays) avant return CT.

## Private follow request pending

Pour compte **privé** avec follow en **Requested** et config qui respecte le pending :

- le **mute** est typiquement **skipped** (raison documentée dans les logs) ;
- le post-follow continue vers **return CT** sans forcer des taps sur une surface incertaine.

## Return CT

Le **return CT** ramène l’UI vers la liste followers du **profil source (CT)** avec :

- backs **bornés** ;
- détection **liste + surface CT** ;
- garde-fous **anti-dérive** (surfaces dangereuses : story, reel, composer, DM, etc.) ;
- **budget temps** par round et **abort** propre si la surface reste dangereuse ou incertaine.

### Checkpoint 2026-07-12 — ambiguous own_unified compact return

Status: **patched / tested**, **runtime pending**.

Incident traité avant patch fonctionnel :

1. `instagram_navigation.py` a été trouvé tronqué dans le worktree.
2. Le fichier tronqué et son diff complet ont été sauvegardés hors repo sous
   `/tmp/instagram_navigation.py.truncated.<timestamp>` et
   `/tmp/instagram_navigation.py.truncated.<timestamp>.diff`.
3. La baseline `HEAD:instagram_navigation.py` a été vérifiée complète avant
   restauration ciblée : présence de
   `post_follow_controlled_return_to_followers_list`,
   `return_to_followers_list`, `detect_followers_list_screen` et
   `run_mute_engine_v2`.
4. Restauration limitée à `instagram_navigation.py`, sans `git reset --hard`,
   sans checkout global et sans stash global.

Patch documenté :

- Commit worker :
  `26cd04cd816c8b358397e5e9d4224360fb5ea54f`
  (`fix(worker): recover ambiguous followers list return`).
- Le cas `post_follow_return_ct_compact_ambiguous_list_safe_back_recovery`
  réutilise la détection `post_back_det` après `compact_safe_back`.
- Conclusion attendue : `compact_safe_back_then_list`.
- Scope strict : aucun changement Like, Search Surface Recovery, rotation CT,
  stale action bar générique, réglage production ou refactor hors Return CT.

Validation sans device :

- `python3 -m py_compile instagram_navigation.py`
- test ciblé Return CT
- tests Golden Flow, dont
  `tests/test_golden_flow_open_follow_mute_return.py -q`
- suite worker complète applicable au checkpoint
- `git diff --check`
- no-leak scan

La validation runtime réelle reste en attente : ne pas présenter Return CT,
Welcome ou Busy projection comme runtime validated par ce checkpoint.

## Partial success

Si le follow est **validé** mais le retour CT **échoue** ou est **abandonné** pour dérive / budget :

- le runner traite souvent la session comme **partielle** (`visual_candidate_post_follow_session_partial`) avec une **note** explicite (échec retour vs abort drift vs budget) ;
- le follow **ne doit pas** être réécrit comme échec métier sans raison documentée.

## Drift prevention

Principes documentés côté implémentation :

- **un** back contrôlé maximal sur surface dangereuse détectée, puis **re-observe** ;
- **pas** de navigation exploratoire (swipe feed, ouverture post, commentaire, message) pendant le recovery post-follow ;
- logs **`post_follow_return_ct_drift_surface_detected`**, **`post_follow_return_ct_safe_back_*`**, **`post_follow_return_ct_aborted_to_prevent_drift`**, **`post_follow_return_ct_round_budget_exceeded`** selon les branches ;
- réouverture profil + liste via **taps** uniquement si la config l’autorise explicitement (sinon désactivée par défaut pour limiter la dérive).

Voir aussi [recovery-engine.md](recovery-engine.md).

## Loriele Follow 60 — PostGridEvidence V2 et reçus durables

Le canary `lorielebras_autom` peut produire, à la fermeture finale de la feuille
Mute, une preuve immuable `PostGridEvidence` entièrement typée. Le producteur
croise identité exacte du profil, package/activity, état Grid/Reels/Tagged,
géométrie physique, génération navigation/scroll, viewport et vérification des
deux Mutes. Ses seuls verdicts sont `POST_ROW_POSITIVE_SAFE`,
`POST_ROW_POSITIVE_BUT_CLIPPED`, `POST_GRID_AMBIGUOUS_FINAL` et
`NO_POSTS_POSITIVE`.

Une ligne coupée autorise au producteur un reveal borné, une seule nouvelle
acquisition XML, puis au plus une Vision si le profil, la grille et le nombre de
posts sont déjà positivement prouvés mais que les bounds restent absentes. Le
consommateur ne relance aucune investigation : une ambiguïté finale part
directement vers Golden une fois. Avant un tap direct, une `FreshUiProof`
tap-scoped est créée puis revalidée. Après toute ouverture, rapide ou Golden,
le garde Story/Highlight V5 reste obligatoire ; une capture supplémentaire
n'est permise que sur rejet avant l'unique Back/recovery.

Chaque stage physique vérifié (`mute_posts_verified`,
`mute_stories_verified`, `like_verified`, `return_ct_exact`) est journalisé
dans un outbox SQLite local crash-safe, sans XML, screenshot ni secret. La clé
est `(account_id, original_run_id, action_id_hash, stage)`. Une RPC composite
idempotente projette les reçus sous binding exact account/run/request/action,
et le candidat suivant reste bloqué jusqu'à confirmation de tous les stages du
cycle. Stop pose d'abord le latch UI, puis tente un flush partiel borné ; après
crash, le replay est DB-only et précède toute connexion au téléphone.

Ce contrat est account-scoped : tous les autres comptes conservent le chemin
Golden normal. L'absence ou la contradiction du binding canary échoue avant
toute action Post-Follow avec `follow60_stage_binding_missing_or_invalid`.
