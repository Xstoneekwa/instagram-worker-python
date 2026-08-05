# Follow60 Ordering V2 shadow V1

## Décision

Le changement d'ordre comportemental n'est pas activé. Les runs historiques ne
transportent pas la géométrie de la surface initiale pré-Follow avec une
couverture suffisante pour prouver les gates de fréquence et de gain. Les deux
captures opérateur du 5 août sont des exemples hors run; elles ne comptent pas
dans les statistiques terrain et ne définissent aucune coordonnée.

## Contrat shadow

- `FOLLOW60_ORDERING_V2_SHADOW_ENABLED` vaut `0` par défaut.
- Une valeur vraie ne suffit pas: l'`account_id` doit aussi être présent dans
  `FOLLOW60_ORDERING_V2_SHADOW_ACCOUNT_IDS`.
- L'évaluation consomme uniquement le XML déjà acquis par la capture mono
  pré-Follow V1.
- Le classifieur PostGrid V1 existant est réutilisé sans nouvelle acquisition.
- Aucun dump, screenshot, sleep, tap, scroll, navigation, écriture DB ou
  modification d'ordre n'est produit.
- Le résultat V2 est un unique événement terminal redacted
  `follow60_ordering_v2_shadow_terminal`; il n'est lu par aucun moteur d'action.

## Catégories

- `DIRECT_GRID_SAFE`: identité positive, onglet Posts positif, cellule top-left
  entièrement visible et preuve V1 tap-safe.
- `PRIVATE`: signal privé positif.
- `NO_POSTS`: état No Posts positif.
- `BELOW_FOLD`: reveal requis ou rangée coupée.
- `AMBIGUOUS`: tout autre état; fail closed.

## Condition de passage au comportemental

Une future décision exige des données shadow réelles et un nouveau GO one-shot:
couverture `DIRECT_GRID_SAFE >= 40%`, gain médian global conservateur `>= 5 s`,
gain éligible `>= 8 s`, PostOpen direct `<= 7–8 s`, réentrée `<= 2,5 s`, puis
preuves de persistance Like-before-Follow, Stop et fallback V1. Cette livraison
ne satisfait ni ne contourne ces gates.

## Evidence V2 terminal

`FOLLOW60_ORDERING_V2_SHADOW_EVENT_V2` remplace l'événement public immédiat V1
par un contexte mémoire par candidat. Le contexte est créé depuis la même
mono-capture pré-Follow, enrichi par les événements structurés que V1 produit
déjà, puis retiré après exactement un événement
`follow60_ordering_v2_shadow_terminal`.

Les statuts terminaux sont `completed`, `partial_manual_stop`,
`partial_worker_stop`, `partial_error` et
`shadow_internal_error_non_blocking`. Le Stop publie seulement les étapes déjà
observées. Un binding incomplet ou incohérent rend l'événement shadow invalide
mais ne remonte jamais d'exception au moteur V1.

### Sources des champs

| Champ shadow | Source V1 existante | Fonction/objet | Frontière | Copie sans acquisition |
|---|---|---|---|---|
| account/request/run | binding Mainline validé | `_run_followers_list_engine_session` | avant candidat | oui |
| business session/attempt/binding kind/Worker SHA | binding + identité runtime | runner | avant candidat | oui |
| target/candidat/action correlation | target courant + `visual_candidate_id` | runner | avant candidat | oui |
| filtre/éligibilité | candidat après le pipeline V1 de rejets | runner | entrée Follow gate | oui |
| budget configuré/effectif | caps déjà résolus | runner | entrée Follow gate | oui |
| identité/public/private/loading/CTA | mono-capture déjà acquise | private fast path V1 | profil ouvert | oui |
| Posts count/source | XML immuable déjà présent | parse CPU redacted | création contexte | oui |
| tabs/cellules/rangée/top-left/overlaps/Reels/Tagged | classifieur PostGrid V1 | `_post_follow_post_grid_evidence_from_xml` | création contexte | oui |
| path SAFE/Reveal/Golden/No Posts/V5 | logs structurés V1 | observer fail-open | PostOpen | oui |
| viewer/V5/Like | logs structurés V1 | PostFollow V1 | viewer/Like | oui |
| Return CT/persistance | preuves et receipts V1 | runner/PostFollow | fin cycle | oui |
| reentry | retour viewer→profil et preuve CT déjà payés | événements V1 | après Like/skip | oui |
| coût shadow | `time.perf_counter_ns()` | contexte shadow | chaque enrichissement | CPU seulement |

Quand V1 ne publie pas une valeur, V2 conserve `unknown` avec une reason stable.
Il n'invente ni timestamp UI, ni preuve, ni durée évitable.

### Groupes publiés

- `binding`: account/request/run/business session/attempt/target/candidat/action ;
- `business_eligibility`: pipeline et budgets déjà résolus ;
- `initial_surface`: identité et état du profil ;
- `post_grid_geometry`: géométrie redacted, sans bounds publiques ;
- `classification`: classe initiale et premier rejet ;
- `v1_actual_execution`: chemin réellement traversé et timings disponibles ;
- `reentry_evidence`: niveau 0/1/2/not-proven sans action de validation ;
- `operation_counters`: sept compteurs obligatoirement à zéro ;
- `cpu_timings`: création, classification, enrichissement, sérialisation, total ;
- `safety_assertions`: V1 autoritaire et absence d'action shadow.

`DIRECT_GRID_SAFE` exige simultanément identité, profil public, CTA Follow,
Posts count positif, onglet Posts, cellule top-left unique row 1/column 1,
première rangée visible, zéro overlap Suggested/Highlights et Reels/Tagged non
sélectionnés. Aucun `unknown` critique n'est promu.

### Compteurs de non-acquisition

Chaque terminal publie sept zéros: screenshots, XML, polls, taps, queries
accessibility, path overrides et intents shadow. Le module n'importe ni driver
téléphone ni Vision/PIL et ne contient aucun appel screenshot/dump/click/tap/
swipe/sleep/poll/accessibility. L'observateur logger est fail-open.

## Procédure d'analyse du prochain smoke

1. Filtrer `event=follow60_ordering_v2_shadow_terminal` et le schema V2.
2. Vérifier un terminal par couple account/run/action correlation.
3. Vérifier candidat/target/request/business session et zéro orphelin.
4. Calculer la complétude des sources disponibles; gate `>=95%`.
5. Vérifier les sept compteurs à zéro et V1 seul moteur comportemental.
6. Séparer les timings CPU shadow des durées UI V1.
7. Vérifier qu'un Stop produit un seul `partial_manual_stop` honnête.
8. Après dix candidats conformes, poursuivre jusqu'à trente terminaux; sinon
   arrêter au premier champ absent sans activer de comportement V2.
