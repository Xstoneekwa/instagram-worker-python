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
- Le résultat est un événement redacted
  `follow60_ordering_v2_shadow_evaluated`; il n'est lu par aucun moteur d'action.

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
