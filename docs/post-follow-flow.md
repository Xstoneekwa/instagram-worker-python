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

## Monotonic Follow and durable post-Follow partials

La preuve canonique `follow_verified` est irréversible dans le sens métier : une
défaillance ultérieure de Mute, Like ou Return CT ne requalifie jamais le Follow
en échec et n'autorise jamais un second tap Follow. Le WAL SQLite
`follow60_post_follow_outbox_v2` est la source autoritaire pour les étapes
post-Follow acquittées. Une classification locale n'est permise que si le Follow
canonique est déjà persisté, que `return_ct_exact` figure dans les reçus durables
et que le groupe est conservé pour reprise idempotente.

Ce contrat produit le terminal borné
`target_local_follow_durable_post_follow_pending` (exit 53) : la cible reste
incomplète, aucun nouveau Follow ne peut être entrepris avant reprise, mais la
phase Unfollow obligatoire de la session peut continuer. Le champ visuel
transitoire `return_ok` n'a aucune autorité sur un reçu `return_ct_exact` déjà
persisté. Toute absence de preuve exacte, et surtout toute défaillance de
persistance du Follow canonique, reste fail-closed avec exit 96.

Cette fermeture n'ajoute aucun XML dump, screenshot, Vision, sleep ou geste UI
au happy path. Elle ne lance pas une nouvelle boucle locale : après la tentative
post-Follow existante, l'état prouvé est persisté puis différé immédiatement.

## Rex Follow 60 — PostGridEvidence V2 et reçus durables

Le canary Follow 60 peut produire, à la fermeture finale de la feuille
Mute, une preuve immuable `GridClassificationProof` entièrement typée. Le producteur
croise identité exacte du profil, package/activity, état Grid/Reels/Tagged,
géométrie physique, génération navigation/scroll, viewport et vérification des
deux Mutes. Ses seuls verdicts sont `POST_ROW_POSITIVE_SAFE`,
`POST_ROW_POSITIVE_BUT_CLIPPED`, `POST_GRID_AMBIGUOUS_FINAL` et
`NO_POSTS_POSITIVE`.

La classification n'autorise jamais un tap. Une ligne coupée autorise au
consommateur exactement un reveal borné, invalide les anciennes bounds, puis
exige une seule nouvelle acquisition XML et une nouvelle classification. Il
n'existe aucun diagnostic Vision intermédiaire : si la nouvelle classification
n'est pas `POST_ROW_POSITIVE_SAFE`, le chemin part directement vers Golden une
fois. Avant un tap direct, une `FreshTapProof` one-shot est créée depuis cette
même hiérarchie fraîche, son fingerprint exact et son repère canonique, puis
consommée sous un TTL distinct. Après toute ouverture, rapide ou Golden,
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

### Final performance pass — CoordinateFrameV1

Chaque preuve PostGrid autoritaire porte désormais un
`coordinate_frame_v1` : fenêtre brute, viewport canonique issu de la
hiérarchie, insets exacts, orientation, densité et hash déterministe de la
transformation. L'acceptation est exacte (`coordinate_frame_exact_match`) ou
normalisée par des insets explicitement comptabilisés
(`coordinate_frame_normalized_match`). Une version, un hash, un inset ou un
viewport incohérent rejette la preuve vers Golden ; aucune tolérance numérique
n'est utilisée.

La hiérarchie stable de fermeture Mute est partagée par la preuve de fermeture,
l'identité exacte et le classifieur PostGrid. Un checkpoint Golden ultérieur,
non autoritaire, ne peut plus écraser cette preuve. Cela supprime la régression
terrain où un repère valide `1080x2340` était remplacé par des valeurs par
défaut `1x1`.

Un profil identifié avec grille sélectionnée, vide, non privé et hors
Reels/Tagged peut recevoir une seule observation de stabilisation bornée à
280 ms afin de promouvoir un marqueur exact `No Posts Yet`. Toute autre
ambiguïté va directement au Golden. Une ligne de posts positivement prouvée
mais coupée conserve un seul reveal, une seule acquisition fraîche, puis le
garde Story/Highlight V5 obligatoire avant tout Like.

### GridClassificationProof / FreshTapProof / LikeTapContextV2

Les générations de preuve PostGrid appartiennent à un domaine unique : la
génération UI canonique du runtime Follow 60. Les générations issues d'un
contexte candidat restent de la provenance descriptive et ne peuvent ni
invalider ni autoriser la classification. L'absence de bounds de l'onglet Posts
peut être compensée uniquement par un ordre XML structurel positif : identité
exacte, onglet Posts sélectionné, zone Suggested séparée, puis bande média
partielle. Les cartes Suggested ne sont jamais des cellules média.

Le TTL de classification/reveal et le TTL de tap sont séparés. Une
`GridClassificationProof` peut justifier le reveal sans fournir de coordonnées
cliquables. Seule une `FreshTapProof`, liée au même XML, fingerprint, repère,
package/activity et génération canonique, autorise le tap de cellule.

`PostOpenContextV1` conserve la continuité de l'ouverture, mais n'autorise plus
le Like. Juste avant le tap Like, `LikeTapContextV2` est construit depuis un XML
frais et doit lier le compte, le run, la request, l'action, le candidat exact,
les bounds exactes du contrôle Like, le package/activity, la génération UI et
le verdict positif V5. Un signal Story/Highlight gagne toujours. Le contexte
est ensuite validé sous son propre TTL court et consommé immédiatement ; au
moindre doute, aucun tap Like n'est envoyé.

### Bloc A — barrière, pont V5 et première rangée post-reveal

Une barrière d'évaluation Follow60 n'est terminalisée en succès opérateur que
si le cycle qui atteint exactement la cible est complet : Follow, les deux
Mutes requis, Like vérifié ou skip sûr, retour CT exact, reçus acquittés et
outbox vide. Le terminal dédié `completed_waiting_operator_evaluation` ne crée
ni incident ni reprise Auto Restart et ne lance aucun candidat supplémentaire.
Un Stop incomplet, un crash ou une preuve manquante reste hors de ce chemin.

Après ouverture d'un post, le pont immuable `PostOpenContextV1` vers
`LikeTapContextV2` transporte les bindings account/run/request/action, le
candidat, package/activity, le verdict V5, les fingerprints, générations,
bounds et nonce. Quand V5 est positif mais que les champs Like exacts ne sont
pas déjà transportés, une seule réacquisition XML est autorisée. Elle doit
reconstruire une preuve identique et bornée ; sinon le Like échoue fermé. Aucun
screenshot ni dump supplémentaire n'est ajouté au happy path exact.

Après l'unique reveal PostGrid, `PostRevealSafeFirstRowV1` peut promouvoir la
rangée supérieure totalement visible même si une rangée inférieure reste
coupée. La preuve vient du même XML frais et lie identité exacte, onglet Posts,
absence de Suggested/Story/Highlight, package/activity, repère, génération et
fingerprint. Le tap direct produit ensuite une nouvelle `FreshTapProof` et le
garde V5 reste obligatoire. Toute ambiguïté conserve un seul fallback Golden.
