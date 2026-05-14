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
