# Phone Farm — Release Registry

> Registre vérifié le **2026-07-14 à 14:55 SAST**. Il conserve les états
> remplacés au lieu de les supprimer. Une ligne `poussé` ne signifie jamais
> `déployé`, et une ligne `déployé` ne signifie jamais `validé physiquement`.

## Statuts

- `TESTED` : validations automatiques rapportées.
- `PUSHED` : ref GitHub vérifiée.
- `BUILT` : artefact ou build prouvé.
- `DEPLOYED` : backend/release/package placé dans la cible.
- `ACTIVE` : processus, symlink, alias ou heartbeat prouve l'utilisation.
- `PHYSICALLY VALIDATED` : flow observé sur appareil réel.
- `REPLACED` : ancien état conservé pour rollback/historique.
- `ABANDONED` : branche explicitement non intégrée.

## Worker

| Date | Commit | Objet | Testé | Poussé | Release | Actif | Physique | Statut final |
|---|---|---|---|---|---|---|---|---|
| 2026-07-11 | `0a6f62d` | Golden defaults + contrat CT | oui | historique Git | `b6fedca-plus-search-surface-recovery` à l'époque | non | validation moderne incomplète | `REPLACED` |
| 2026-07-12 | `26cd04c` | Return CT liste ambiguë | oui | ancêtre distant | inclus dans releases suivantes | non seul | partiel/historique | `REPLACED` |
| 2026-07-13 | `15df562` | preuve bulle Welcome | oui | ancêtre distant | release intermédiaire | non | cas réel incomplet | `REPLACED` |
| 2026-07-13 | `358f12b` | recovery entrée sender Welcome | oui | ancêtre distant | release intermédiaire | non | cas réel incomplet | `REPLACED` |
| 2026-07-13 | `139ace7` | vérification Welcome + retour surface | oui | branche release locale | `b6fedca-plus-search-surface-recovery` | notifier seulement | historique | `REPLACED` pour dispatcher |
| 2026-07-13 | `476c8e1` | conserver preuve followers validée | oui | `release/b6fedca-plus-search-surface-recovery` | `476c8e-welcome-surface-proof` | non | run réel observé, résultat non certifiant | `REPLACED` |
| 2026-07-14 | `74de873` | preuve Welcome imbriquée | oui | branche dédiée | `74de873-welcome-committed-proof` | non | run réel ayant mené aux incidents du 14 juillet | `REPLACED` |
| 2026-07-14 | `9b7fa2f` | frontière followers Suggestions | oui | oui | `9b7fa2f-followers-suggestions-boundary` | **oui** | **NOT PHYSICALLY VALIDATED** | `ACTIVE` |
| 2026-07-13 | `6ec8270` | copie Golden Follow/Mute/Like | oui (`175` ciblés + `166` transverses rapportés) | oui | aucune release active | non | **NOT PHYSICALLY VALIDATED** | `PUSHED, NOT DEPLOYED` |
| 2026-07-13 | `1af5ca9` | progression synchrone live | oui | branche dédiée | aucune | non | non | `REPLACED` par `43c2bc3` |
| 2026-07-13 | `43c2bc3` | retrait publication synchrone | oui | oui | aucune | non | non | `NOT DEPLOYED`, effet net annulé |
| 2026-07-14 | `4d08ca3` | import Cursor cloud | non retenu | branche isolée | aucune | non | non | `ABANDONED` |

Sources : `GIT`, `CODEX`, `RUNTIME`. La vérification Git confirme que
`6ec8270` **n'est pas ancêtre** de `9b7fa2f`.

## Backend / Vercel

| Date | Commit | Objet | Testé/build | Poussé | Deployment production | Actif | Statut final |
|---|---|---|---|---|---|---|---|
| 2026-07-12 | `931bd5b` | nettoyer cancel marker au retry | build initial corrigé par commit suivant | ancêtre | ancien déploiement | non seul | `REPLACED` |
| 2026-07-12 | `181d6cf` | build retry scheduler | oui | ancêtre | ancien deployment READY | non seul | `REPLACED` |
| 2026-07-12 | `61d6ccf` | projection runs actifs / blockers stale | oui | ancêtre | ancien deployment READY | non seul | inclus dans production actuelle |
| 2026-07-13 | `9c9fc8d` | supprimer overload lock ambigu | oui | oui | ancien deployment/ancêtre | non seul | inclus dans production actuelle |
| 2026-07-13 | `537f527` | compteurs live vérifiés | oui | ancêtre | preview/ancêtre | non seul | inclus dans production actuelle |
| 2026-07-13 | `9b0626f` | route Profiles légère | oui | ancêtre | preview/ancêtre | non seul | inclus dans production actuelle |
| 2026-07-14 | `509163e` | review actions + croissance 72 h | oui | ancêtre | ancien deployment READY | non seul | inclus dans production actuelle |
| 2026-07-14 | `5c166a0` | Operator Review final + snapshots | build READY | oui | `dpl_8bQpsy7iKNxrMyMcsTCY87USwyrv` | remplacé | `REPLACED` |
| 2026-07-14 | `3b0dcff` | projection `stopping` | build READY | oui | `dpl_AtaJLhWxdkRbsxG82gscGu2A1Umw` | remplacé | inclus dans production actuelle |
| 2026-07-14 | `65c58f1` | action liée dans Incident drawer | build READY | oui | `dpl_7wHp4NShgJLqyYZ3d5S5ZCG2ctxg` | **oui** | `ACTIVE`, interaction drawer partielle |

Source : métadonnées Vercel + refs GitHub vérifiées le 2026-07-14.

## BotApp

| Date | Commit | Objet | Testé | Poussé | Package/installation | Actif | Validation UI | Statut final |
|---|---|---|---|---|---|---|---|---|
| 2026-07-13 | `dcbb85e` | clarification review/restart | oui | ancêtre | ancien package | non | historique | `REPLACED` |
| 2026-07-13 | `1969505` | checkpoint scheduler | docs/tests | ancêtre | ancien package | non | historique | `REPLACED` |
| 2026-07-13 | `9aefbca` | profils actifs + compteurs | oui | ancêtre | ancien package | non | partielle | `REPLACED` |
| 2026-07-13 | `ec40656` | polling Profiles léger | oui | ancêtre | ancien package | non | partielle | `REPLACED` |
| 2026-07-13 | `b73a191` | faux zéro croissance | oui | oui | ancien package | non | partielle | `REPLACED` |
| 2026-07-14 | `dec2a12` | croissance followers 72 h | oui | oui | ancien package installé | non | oui pour rendu principal | `REPLACED` |
| 2026-07-14 | `f30c8dc` | merge runtime groupes Profiles | oui | oui | package installé puis remplacé | non seul | polling observé | inclus dans package actuel |
| 2026-07-14 | `b812370` | Mark reviewed depuis Incidents | oui | oui | installation officielle hash-identique | heartbeat frais | drawer non ouvert par automation | `ACTIVE, PARTIALLY VALIDATED` |

## Règle de mise à jour

Pour toute nouvelle release, ajouter une ligne seulement après avoir enregistré
séparément : commit, branche distante, résultat tests, artefact construit,
destination de déploiement/installation, preuve d'activation et verdict
physique. Ne jamais déduire un niveau du niveau précédent.
