# Phone Farm — Feature Status Matrix

> Vérifiée le **2026-07-14 à 14:55 SAST**. Une coche dans une colonne ne
> sous-entend jamais les colonnes suivantes.

| Fonction | Implémenté | Testé | Poussé | Déployé/installé | Activé | Validé physiquement | Prévu / remarque |
|---|---|---|---|---|---|---|---|
| Golden historique `fb3dbc0` | oui | oui | oui/tag | historique | non | oui, run `fc65bace` | référence immutable |
| Copie Golden moderne `6ec8270` | oui | oui | oui | non | non | **NON** | décision d'intégration future |
| Welcome list-native | oui | oui | oui | oui | oui | oui historiquement | dernières branches ajoutent des guards |
| Préservation preuve Welcome `74de873` | oui | oui | oui | remplacé | non | échec réel observé | remplacé par `9b7fa2f` |
| Frontière Followers Suggestions `9b7fa2f` | oui | oui | oui | oui | oui | **NOT PHYSICALLY VALIDATED** | prochain Play exige GO |
| Follow/Mute/Like/Return CT actif | oui | oui | oui | oui | oui | historique/partiel | runtime actif n'inclut pas `6ec8270` |
| CT rotation 30/4 | oui | oui | oui | oui | oui | validé sur comptes réparés | audit requis pour futurs comptes |
| Unfollow strict/any/non-followers | oui | oui | oui | code présent | selon package | oui historiquement | pas retesté ce snapshot |
| Outreach jobs/entitlements | oui | oui | oui | oui | selon entitlement | oui historiquement | Edge auth documentée séparément |
| Daily Scheduler | oui | oui | oui | oui | oui si switch/gates OK | partiel | positive path final à reconfirmer |
| `manual_only` hard exclusion | oui | oui | oui | oui | oui | n/a | aucun assignment live manual_only au snapshot |
| Preflight T-10/late | oui | oui | oui | oui | oui | partiel | zéro preflight actif au snapshot |
| Auto Restart | oui | oui | oui | oui | configuré production | partiel | distinct du Daily Scheduler |
| Play manuel | oui | oui | oui | oui | oui avec gates | exercé ; dernier run échoué | aucun nouveau Play sans GO |
| Stop / état `stopping` | oui (`3b0dcff`/`f30c8dc`) | oui | oui | oui | oui | **NOT PHYSICALLY VALIDATED** end-to-end | vérifier vert→gris <4 s |
| Operator Review RPC canonique | oui | oui | oui | oui | oui | DB validée | une action reste pending |
| Mark reviewed dans Incident drawer | oui (`65c58f1`/`b812370`) | oui | oui | oui | bundle actif | **PARTIAL** | drawer non ouvert par automation |
| Snapshots followers quotidiens | oui | oui | oui | oui | cron actif | DB observée | dernier snapshot 2026-07-14 00:30Z |
| Croissance followers 72 h | oui | oui | oui | oui | UI active | données partielles | `—` si historique insuffisant |
| Stripe Test checkout | oui | oui | oui | oui | test mode | 2 checkouts fulfilled | Live interdit actuellement |
| Stripe Live | non prouvé | non | non | non | non | non | gates dans backend matrix |
| Incident Slack/Discord | oui | historique/tests | oui | oui | notifier actif | non revalidé ce snapshot | notifier sur ancienne release |
| BotApp `package-provenance.json` | historique sur ancienne branche seulement | historique | non dans branche active | non | non | n/a | contrat futur documenté, aucun build modifié |
| Revue auth RPC/Edge | inventaire créé | lecture statique partielle | docs uniquement | n/a | n/a | n/a | RPC publics `SECURITY DEFINER` à auditer |

## Sources

- `CURRENT_PRODUCTION_STATE.md` pour le snapshot live.
- `RELEASE_REGISTRY.md` pour les commits et promotions.
- `LOCKED_DECISIONS.md` pour les règles.
- Backend `docs/botapp-scheduler-runtime-contract.md`.
- Backend `docs/STRIPE_TEST_LIVE_MATRIX.md`.
- Backend `docs/RPC_EDGE_AUTH_MATRIX.md`.
