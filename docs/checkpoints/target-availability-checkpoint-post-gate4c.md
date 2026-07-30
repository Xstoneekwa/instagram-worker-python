# Target Availability — checkpoint post-Gate 4C

Date du checkpoint : 2026-07-30

Périmètre : Target Availability uniquement

Nature : documentation et consolidation de preuves
État d'autorisation : aucune étape suivante autorisée

## 1. Executive Summary

Les fondations Target Availability sont construites, déployées de façon dormante et certifiées sur deux frontières successives : Gate 4B en mémoire locale sans écriture DB, puis Gate 4C avec un writer DB limité à un seul compte pilote. La chaîne observée sait aujourd'hui produire des faits techniques courts, déterministes et correctement scopés vers `ct_target_availability_observations`, sans décider qu'une cible est disponible ou indisponible et sans modifier une campagne, un CT, un compte ou un comportement utilisateur.

Le système n'est pas activé comme moteur métier. Les quatre tables d'identité, d'assessment et d'état courant restent vides. Aucun calcul de confidence, confirmation cross-run, TTL, recheck, quarantine, Performance, Utilization, Lifecycle ou Premium Replacement n'est en production. Les flags Availability Shadow, Policy Shadow et enforce restent OFF. Le kill switch est revenu ON après la certification.

Verdict du checkpoint : **GO — TARGET AVAILABILITY POST-GATE4C CHECKPOINT COMPLETED**. Ce GO valide la consolidation documentaire et la fondation d'observation limitée. Il ne constitue pas un GO d'activation, de Shadow universel, de policy, de remplacement ou de notification.

## 2. Scope

Ce document couvre :

- le contrat DB additif Target Availability ;
- l'instrumentation Worker observatoire ;
- la résolution canonique tenant/account/target ;
- les preuves Gate 4B et Gate 4C ;
- l'état dormant du Backend ;
- les frontières avec Performance, Utilization, Lifecycle et Premium Replacement ;
- les risques, dépendances de runs et gates restantes.

Ce document ne réalise aucune modification de code, de DB, de configuration, de runtime ou de données. Il ne déclenche aucun run/tick, ne redémarre aucun composant et n'autorise aucune étape suivante.

### Légende de maturité

| État | Définition |
|---|---|
| `CONSTRUCTED` | Contrat ou code présent et validé localement. |
| `DEPLOYED_DORMANT` | Présent en production, désactivé ou sans consommateur actif. |
| `CERTIFIED` | Observé sur un run naturel ou produit normal avec critères explicites satisfaits. |
| `TESTED_ONLY` | Couvert par tests, sans preuve terrain. |
| `NOT_YET_IMPLEMENTED` | Composant ou politique encore absent. |
| `NOT_YET_OBSERVED` | Présent mais pas encore démontré sur le terrain. |
| `BLOCKED` | Une dépendance ou un gate interdit la poursuite. |

## 3. Current Production State

| Domaine | État actuel | Preuve / limite |
|---|---|---|
| DB Availability | `DEPLOYED_DORMANT` | 5 tables, RLS forcée, ACL service-role-only ; seules 4 observations Gate 4C existent. |
| Worker hooks | `CERTIFIED` | Gate 4B mémoire puis Gate 4C writer limité. |
| Résolution tenant | `CERTIFIED` | Résolution active et exacte via `client_instagram_accounts`. |
| Writer observation | `CERTIFIED` sur 1 pilote | 4 écritures valides, zéro fuite non-pilote/cross-tenant. |
| Identity engine | `NOT_YET_IMPLEMENTED` | Tables déployées, aucun producteur. |
| Assessment engine | `NOT_YET_IMPLEMENTED` | Table déployée, aucun calcul actif. |
| Availability current | `NOT_YET_IMPLEMENTED` | Table déployée, aucune projection courante. |
| Availability Shadow | `DEPLOYED_DORMANT` | Flag OFF ; aucun Shadow live certifié. |
| Policy Shadow | `DEPLOYED_DORMANT` | Flag OFF ; aucune décision métier. |
| Enforcement | `BLOCKED` | Flag OFF ; aucun gate d'activation accordé. |
| Performance | `NOT_YET_IMPLEMENTED` dans ce moteur | Frontière définie, aucun score Target Availability. |
| Utilization | `NOT_YET_IMPLEMENTED` dans ce moteur | Frontière définie, aucun calcul d'épuisement. |
| Target Lifecycle | `TESTED_ONLY` / dormant | Modèle pur présent côté Backend, aucune projection production. |
| Premium Replacement | `NOT_YET_IMPLEMENTED` | Aucune sélection, aucun remplacement, aucun email. |

### Runtime sécurisé après Gate 4C

- Worker actif : `6a5edff51346cc44fa84775fe7511bf455802163`.
- Release : `/Users/admin/phonefarm-worker-releases/6a5edff-unfollow-search-autorestart-v2-gate4b-merged-v1`.
- Symlink actif : `/Users/admin/phonefarm-worker-current`.
- Dispatcher : wrapper PID `81353`, consumer PID `81445`, instance unique lors de la recertification.
- `TARGET_AVAILABILITY_CAPTURE_ENABLED=false`.
- `TARGET_AVAILABILITY_WRITER_ENABLED=false`.
- allowlist Availability vide.
- kill switch Availability présent, donc ON.
- Availability Shadow, Policy Shadow et enforce : OFF.
- Unfollow V2 : shadow ON, enforce OFF ; hors décision Target Availability.
- Probe mémoire : absente après nettoyage.

Une commande de statut exécutée dans un environnement sandboxé a retourné un préflight `supabase_dns_failed` et `processCount=0/starting`. Cette sortie n'est pas une preuve de panne production : le `ps` local montrait les deux PID vivants et les logs du dispatcher montraient des ticks naturels jusqu'à 12:05Z avec `evaluated=5`, `eligible=0`, `enqueued=0`. Le runtime canonique est donc établi par la convergence processus + logs, pas par ce préflight réseau isolé.

## 4. Architecture

### Chaîne canonique et frontières

```text
Faits Instagram / Worker
        |
        v
Target Availability Observations
        |
        +--> Identity resolution/history
        |
        v
Availability Assessment + confidence + TTL + recheck
        |
        v
Availability Current
        |
        +-------------------+
                            |
Performance -----------+    |
                       v    v
Utilization --------> Target Lifecycle
                            |
                            v
                    Plan / package policy
                            |
                            v
                 Premium Replacement (futur)
```

### Responsabilités

| Moteur | Responsabilité | Ce qu'il ne doit jamais décider seul |
|---|---|---|
| Target Availability | Accessibilité, identité, restriction et qualité des preuves. | Rendement, épuisement, remplacement, notification. |
| Performance | Yield et qualité d'une cible sur une période. | Disponibilité technique ou identité. |
| Utilization | Consommation historique et couverture d'audience. | Suppression, renommage ou indisponibilité. |
| Target Lifecycle | Agrégation Availability + Performance + Utilization en état de cycle de vie. | Action Premium hors politique de plan. |
| Premium Replacement | Consommation d'un état Lifecycle et d'une politique Premium. | Déduire une indisponibilité brute ou agir pour Growth/Pro. |

La dépendance est unidirectionnelle. Premium Replacement ne rétroagit pas sur l'observation et ne peut pas devenir une source de vérité Availability.

### Matrice des signaux et contrat futur proposé

Les valeurs suivantes sont des propositions de conception, **non activées** et non des paramètres production.

| Signal | Preuve minimale proposée | Confidence / répétition | TTL proposé | Effet downstream futur | Faux positifs à neutraliser |
|---|---|---|---|---|---|
| Username changed | Même stable ID avec username différent | Medium après 1 preuve fournisseur ; High après 2 runs sains | 7 j | Mettre à jour l'identité courante, conserver l'historique ; jamais renommer sans stable ID | Résultat de recherche voisin, cache UI, typo |
| Deleted | Terminal explicite et identité préalablement établie | High après 2 runs distincts ou preuve fournisseur forte | 24 h puis recheck | Quarantine ; Lifecycle peut proposer retrait après policy | Panne réseau, login wall, rate limit |
| Suspended | Surface explicite de suspension, session saine | High après confirmation cross-run | 24 h | Quarantine et recheck prioritaire | Challenge du compte opérateur, UI localisée |
| Banned | Preuve terminale distincte de suspension temporaire | High, 2 runs ou fournisseur | 24 h | État candidat permanent, jamais action immédiate | Blocage régional, restriction de session |
| Profile unavailable | Lookup terminé sans profil dans une session saine | Low au premier passage, Medium après 2 runs | 1 h | Recheck seulement ; aucune mutation Lifecycle ferme | Réseau, recherche Instagram, navigation |
| Login wall | Session state logged-out/restricted | Low | 15 min | Invalider la preuve, déclencher recovery hors moteur | Popup, session expirée |
| Access restricted | Accès global ou followers restreint | Low/Medium selon surface ; confirmer cross-run | 1 h | Assessment temporaire ou verified-restricted | UI A/B, lenteur, permissions |
| Verified badge seul | Badge détecté | Aucune preuve d'indisponibilité | 7 j comme attribut | Aucun effet négatif | Faux badge visuel, OCR |
| Verified + followers restricted | Badge + followers surface interdite dans une session saine | Medium puis High après 2 runs | 7 j | Quarantine/review ; remplacement futur seulement via Lifecycle | Restriction temporaire, expérience UI |
| Temporary Instagram error | Erreur classifiée et budget retry épuisé | Low | 15 min | Recheck, pas de changement courant durable | Incident plateforme |
| Network ambiguity | Signal réseau absent/instable | Insufficient | 15 min | Ignore pour décision ; métrique d'observabilité | DNS, proxy, Wi-Fi |
| UI ambiguity | Contradiction ou surface inconnue | Insufficient | 1 h | Recheck avec budget borné | A/B test, langue, sélecteur |
| Stable-ID conflict | Stable ID incompatible avec identité courante | High si extraction fiable | Jusqu'à revue | Quarantine immédiate, aucune auto-correction | Mauvaise extraction, recyclage de vue |
| Stale observation | Dernière preuve au-delà du TTL | Insufficient | Selon signal | Dégrader confidence, jamais prolonger implicitement | Horloge, retard d'ingestion |
| Repeated confirmation | Même résultat sur runs distincts sains | Augmente la confidence, ne crée pas seule un statut | Fenêtre glissante | Autorise futur assessment plus ferme | Runs dupliqués, même incident |

### Source, type et projections proposées par signal

Ce second tableau explicite les champs que le premier condense. Il décrit un contrat futur, sans writer ni policy active.

| Signal | Source Worker prévue | Type d'observation | Effet assessment proposé | Effet `availability_current` proposé | Effet Lifecycle futur |
|---|---|---|---|---|---|
| Username changed | Lookup profil + stable ID provider | `identity_username_observed` | `identity_changed` seulement si stable ID identique | Nouveau username + version d'identité | Review/continue ; aucune rotation automatique |
| Account deleted | Résultat terminal de lookup, session saine | `profile_terminal_deleted` | `permanent_unavailable_candidate` | Quarantine + recheck | Retrait candidat après policy |
| Account suspended | Surface explicite Instagram | `profile_terminal_suspended` | `suspended_candidate` | Quarantine temporaire | Hold, pas de remplacement direct |
| Account banned | Surface terminale distincte | `profile_terminal_banned` | `permanent_unavailable_candidate` | Quarantine + preuve répétée | Retrait candidat après policy |
| Profile unavailable | Lookup sans profil | `profile_lookup_unavailable` | `temporary_unavailable` au premier signal | État transitoire + recheck | Aucun effet ferme |
| Login wall | Session/recovery detector | `session_login_wall` | `insufficient_evidence` | Conserver l'état précédent, recheck court | Aucun |
| Access restricted | Navigation/followers surface | `profile_access_restricted` | `access_restricted_candidate` | Restriction temporaire | Hold/review |
| Verified badge | Profile surface | `verified_badge_observed` | Attribut seulement | Mettre à jour evidence, pas le statut | Aucun |
| Verified followers restricted | Badge + followers entry/surface | `verified_followers_restricted` | `verified_restricted_candidate` après répétition | Quarantine/recheck | Recommandation seulement après Lifecycle |
| Temporary Instagram error | Navigation/recovery budget | `instagram_temporary_error` | `insufficient_evidence` | Conserver état, recheck court | Aucun |
| Network error | Connectivity detector | `network_ambiguity` | `insufficient_evidence` | Aucun changement courant | Aucun |
| UI inconsistency | Sélecteurs/surfaces contradictoires | `ui_ambiguity` | `ambiguous` | Conserver état, planifier recheck | Aucun |
| Ambiguous identity | Lookup et extraction identité | `identity_ambiguous` | `identity_review_required` | Quarantine sans mutation d'identité | Hold |
| Stale observation | Projecteur/horloge métier | `evidence_stale` dérivé | Dégrader confidence | Marquer stale, ne pas prolonger | Lifecycle doit ignorer le verdict périmé |
| Repeated confirmation | Agrégateur cross-run | `confirmation_repeat` dérivé | Augmenter confidence sous fenêtre/version | Actualiser confirmation et expiry | Peut franchir un seuil de policy futur |
| Identity conflict | Stable IDs incompatibles | `identity_conflict` | `identity_conflict` ferme | Quarantine jusqu'à revue | Blocage de toute action automatique |

## 5. DB State

### Migrations déployées

- `20260728220631_ct_target_availability_foundations_v1`
- `20260728230641_ct_target_availability_restrict_service_role_and_index_fks_v1`

### Tables et cardinalité au checkpoint

| Table | Rôle | Lignes | État |
|---|---|---:|---|
| `ct_target_availability_observations` | Journal append-only des faits Worker | 4 | `CERTIFIED` sur 1 pilote |
| `ct_target_identity_history` | Historique append-only d'identité | 0 | `DEPLOYED_DORMANT` |
| `ct_target_identity_current` | Projection courante de l'identité | 0 | `DEPLOYED_DORMANT` |
| `ct_target_availability_assessments` | Assessments append-only | 0 | `DEPLOYED_DORMANT` |
| `ct_target_availability_current` | Projection courante Availability | 0 | `DEPLOYED_DORMANT` |

### Contrat détaillé par table

| Table | Schéma / clés | FKs et index principaux | Mutation | Données prochaines / interdites | Rétention et duplication |
|---|---|---|---|---|---|
| `ct_target_availability_observations` | `public`; PK technique ; unique `(tenant_id, account_id, idempotency_key)` | tenant→clients, account→ig_accounts, tenant/account→ownership, account/target→ig_targets, source_run→ig_runs ; indexes target-time/run/FKs | Append-only, service role `SELECT, INSERT` | Prochain : faits Worker courts, evidence allowlistée, latence. Interdit : secrets, décision métier, payload UI brut, action de remplacement | Rétention encore à définir ; upsert/unique neutralise le replay exact |
| `ct_target_identity_history` | `public`; PK ; unique `(tenant_id, account_id, idempotency_key)` | mêmes frontières tenant/account/target ; observation source ; stable-ID indexes | Append-only, service role `SELECT, INSERT` | Prochain : username/stable ID observés avec provenance. Interdit : renommage déduit par similarité, mutation de l'historique | Rétention longue/audit à décider ; idempotency key par preuve/version |
| `ct_target_identity_current` | `public`; PK `(tenant_id, account_id, target_id)` | mêmes frontières ; `last_history_id` ; index stable ID/FKs | Current-state, service role `SELECT, INSERT, UPDATE` | Prochain : projection de la dernière identité prouvée. Interdit : update sans history, stable ID ambigu | Reconstructible depuis history ; CAS/version requis pour éviter out-of-order |
| `ct_target_availability_assessments` | `public`; PK ; unique `(tenant_id, account_id, target_id, assessment_key)` | frontières ownership/target ; indexes recheck/quarantine/FKs | Append-only, service role `SELECT, INSERT` | Prochain : statut, confidence, evidence refs, TTL/recheck, policy version. Interdit : action campagne/CT, email, remplacement | Rétention/audit à définir ; assessment key déterministe empêche doublon |
| `ct_target_availability_current` | `public`; PK `(tenant_id, account_id, target_id)` | ownership/target ; `assessment_id` ; indexes recheck/quarantine/FKs | Current-state, service role `SELECT, INSERT, UPDATE` | Prochain : dernier assessment applicable, expiry, quarantine/recheck. Interdit : score Performance/Utilization ou action Premium | Reconstructible depuis assessments ; CAS/version/observed-at requis |

### Intégrité et idempotence

- Les 5 tables ont RLS activée et forcée.
- Chaque table a exactement une policy `service_role`.
- `public`, `anon` et `authenticated` n'ont aucun grant sur ces tables.
- Les tables append-only autorisent `SELECT, INSERT` au service role ; toute mutation passe par `ct_reject_append_only_mutation_v1`.
- Les tables courantes autorisent `SELECT, INSERT, UPDATE` au service role.
- Les clés étrangères bornent tenant, compte, cible, run, observation, history et assessment.
- Les couples composites tenant/account et account/target empêchent les croisements de propriétaire.
- L'idempotence des observations et de l'identity history repose sur `(tenant_id, account_id, idempotency_key)`.
- L'idempotence des assessments repose sur `(tenant_id, account_id, target_id, assessment_key)`.
- Les tables courantes utilisent la PK `(tenant_id, account_id, target_id)`.
- Les index couvrent les FKs et les futurs chemins target-time, run, stable ID, recheck et quarantine.
- Aucun RPC/fonction n'écrit actuellement dans les tables Target Availability.

L'audit Security Advisor ne contient aucun finding Target Availability. Le Performance Advisor ne remonte que des index non encore utilisés, attendu pour des tables dormantes ou vides. Ces index ne doivent pas être retirés avant observation d'une charge réelle. La frontière ACL suit la recommandation Supabase de combiner grants minimaux et RLS forcée : [Securing your API](https://supabase.com/docs/guides/api/securing-your-api).

## 6. Worker State

| Élément | État / valeur |
|---|---|
| SHA actif | `6a5edff51346cc44fa84775fe7511bf455802163` |
| Branche source | `fix/unfollow-search-autorestart-v2-20260729` |
| Remote officiel | `https://github.com/Xstoneekwa/instagram-worker-python.git` |
| Release active | `/Users/admin/phonefarm-worker-releases/6a5edff-unfollow-search-autorestart-v2-gate4b-merged-v1` |
| Symlink | `/Users/admin/phonefarm-worker-current` |
| Parent Target Availability | `4f61e6710e94b61d81d24de9ab6d1c47cf55e035` |
| Hooks | `loaded` à l'entrée d'une CT ; `summary` après le résumé existant |
| Writer | Queue bornée, batch borné, timeout court, retry borné, circuit breaker, fail-open |
| Résolution tenant | Exacte via ligne active `client_instagram_accounts`, une fois par run |
| Scope | tenant/account/target/run obligatoires, fail-closed si incomplet |
| Payload | Court, sérialisable, déterministe, allowlist de champs ; aucune donnée sensible observée |
| Golden Flow | Les hooks ne réalisent aucune action Instagram et n'altèrent pas rotation/follow/unfollow |

### Writer et résilience

- Queue logique : 256, hard limit 2000.
- Batch : 20, hard limit 100.
- Timeout DB : 1,5 s, hard limit 3 s.
- Retry max : 1.
- Circuit breaker : 3 échecs, ouverture 30 s.
- Upsert idempotent ; duplication ignorée.
- Écriture uniquement dans `ct_target_availability_observations`.
- Une indisponibilité DB ne casse pas le run : le chemin est fail-open pour le Golden Flow, mais la portée/allowlist reste fail-closed.

### Flags et redémarrage

Les hooks lisent `os.environ` à chaque passage, mais l'environnement du processus est hérité à son démarrage. Une modification du fichier env pour `capture`, `writer` ou l'allowlist nécessite donc un restart canonique pour affecter un dispatcher déjà vivant. À l'inverse, le fichier kill switch est testé dynamiquement à chaque hook et permet une fermeture immédiate sans restart.

### Limites connues

- Aucun stable Instagram numeric ID fiable n'est extrait depuis l'UI actuelle.
- Les hooks certifient des points de cycle de vie de CT, pas encore des verdicts d'accessibilité.
- `lookup_result=unknown` et `profile_found=null` dans les 4 preuves Gate 4C : cela démontre le hook/writer, pas une classification Availability.
- Aucun signal rare terminal, contradiction cross-run, replay massif ou contention multi-compte n'a été observé.

## 7. Backend State

| Élément | État |
|---|---|
| Tête production consolidée | `47b6a6619368a558ffa607a3faa0d31da3d81ff4` |
| Commit applicatif V2 | `61bc719` |
| Déploiement | `dpl_99QYDrskoh51eLz8zV1xzSSZXLtw`, alias production `www` |
| Fondation Availability | Modèle pur, ports Supabase, agrégats et shadows présents |
| API/route/cron Availability | Aucun caller applicatif actif |
| Writer Backend | Présent comme adapter, dormant ; les 4 observations viennent du Worker |
| Availability Shadow | OFF |
| Policy Shadow | OFF |
| Enforcement | OFF |
| UI/BotApp | Aucun écran ou comportement Target Availability activé |

Les composants Backend savent représenter le domaine et les flags fail-closed, mais ne produisent actuellement ni identity history/current, ni assessment, ni availability current. Leur présence déployée ne vaut donc pas certification terrain.

Les routes applicatives ne contiennent aucun endpoint Target Availability public ou privé actuellement appelé. Il n'existe donc aucun comportement anonyme : les rôles clients n'ont aucun grant DB et les adapters utilisent le service role côté serveur uniquement. Aucun cron, endpoint UI ou caller inattendu n'a été identifié. L'introduction future d'un reader privé devra ajouter auth, ownership et projection minimale sans exposer les evidences brutes.

## 8. Security and Isolation

| Contrôle | Résultat |
|---|---|
| RLS activée et forcée | 5/5 tables |
| Grants client (`public`, `anon`, `authenticated`) | 0 |
| Policies service role | 1 par table |
| Isolation tenant/account | FKs simples et composites |
| Isolation account/target | FK composite vers `ig_targets` |
| Allowlist Worker | Obligatoire et UUID exact ; vide = capture interdite |
| Kill switch | ON après Gate 4C ; lecture dynamique par hook |
| Writer scope | Observations uniquement |
| Payload sensible | 0 occurrence Gate 4C |
| Writes non-pilote | 0 |
| Writes cross-tenant | 0 |
| Doublons / scopes partiels | 0 / 0 |
| Décision métier | 0 |
| Modification CT/campagne | 0 |

Le modèle de sécurité repose sur trois couches : scope canonique DB, allowlist runtime et kill switch dynamique. Aucune couche ne doit être supprimée lors des phases suivantes.

## 9. Gate 4B Evidence

| Preuve | Valeur |
|---|---|
| Compte | `rex_gen_boost_ai` |
| Run | `59ddd9f3-2e98-4516-be36-2dc686810e54` |
| Source | `auto_restart_tick` naturel |
| Fenêtre | 2026-07-30 00:02:15–01:28:21 SAST |
| Résultat Golden Flow | Completed ; 50 follows, 42 likes, 2 CTs |
| Observations mémoire | 4 créées, 4 valides |
| Rejets / erreurs / invalid scope | 0 / 0 / 0 |
| Payload retenu | 0 |
| Mémoire agrégée | 811 octets |
| Latence hook | moyenne ~3,24 ms ; max 7,21 ms |
| Writer / shadows | OFF / OFF |
| Tables Availability | 0 ligne dans les 5 tables |
| Non-pilote capturé | 0 |
| Recovery | Aucun |
| Cleanup | capture OFF, allowlist vide, kill switch ON, probe supprimée |
| Restart du cleanup | 0 |

**Statut Gate 4B : `CERTIFIED`.** Le Gate prouve la capture mémoire, le scope canonique, l'absence de rétention et l'absence d'impact Golden Flow. Il ne prouve aucune écriture DB ni classification d'indisponibilité.

## 10. Gate 4C Evidence

| Preuve | Valeur |
|---|---|
| Pilote | `j_automatise_pour_toi` |
| Tenant | `aefbca70-fc91-4be8-bc44-c7b8ad776272` |
| Account | `ba73eda4-d22a-4b93-9683-2af7b8aab764` |
| Assignment | `15b7091d-57e6-46cf-9679-39f24d96aafa` |
| Request | `0294f589-fecd-491c-93d6-73782915fd68` |
| Source | `instagram_schedule_session_cron` naturel |
| Run | `9f7f6aba-04e0-4af5-9e96-9498f9abeb60` |
| Fenêtre UTC | 10:00:10Z–11:38:38Z |
| Golden Flow | Completed ; 50 follows, 49 likes, 0 stories |
| Observations DB | 4 valides sur 2 CTs |
| CT 1 | `2f30145a-926d-4ef2-ba4b-243c503e7758` / `forjpro` |
| CT 2 | `877bf7ad-bb0d-4455-9269-17f4bd0856c0` / `les_instants_gourmet` |
| Signaux | `ct_rotation_target_loaded`, `ct_rotation_existing_summary` |
| Raisons | `target_username_lookup_started`, `target_username_lookup_completed` |
| Arrêts follow | `target_budget_reached`, `global_follow_cap_reached` |
| Latence DB | p50 361,8575 ms ; p95 593,8087 ms ; max 629,941 ms |
| Doublons / scopes partiels | 0 / 0 |
| Non-pilote / cross-tenant | 0 / 0 |
| Payload sensible / clés inattendues | 0 / 0 |
| Taille evidence max | 677 octets |
| Autres tables Availability | 0 ligne dans chacune des 4 tables |
| Restarts autorisés/utilisés | 1 / 1 avant le pilote ; 0 après |

Le run Auto Restart de retry `c148bd19...`, request `884d727c...`, terminé à 11:48:49Z avec 22 unfollows, est explicitement hors périmètre Gate 4C. Il n'a produit aucune ligne Availability et n'est ni une réussite ni un échec du Gate.

**Statut Gate 4C : `CERTIFIED`.** Le Gate prouve un writer production limité, idempotent, isolé et sans régression observable sur un pilote. Il ne prouve pas la justesse d'un futur assessment, la charge multi-compte ou les signaux rares.

### Cleanup final Gate 4C

- kill switch replacé ON ; fichier armé absent ;
- capture OFF ; writer OFF ; allowlist vide ;
- Availability Shadow, Policy Shadow et enforce OFF ;
- probe mémoire absente ;
- aucune observation valide supprimée ;
- aucun restart supplémentaire ;
- aucune prochaine étape autorisée.

## 11. Tests

| Classe | Preuve | Résultat | Classification |
|---|---|---:|---|
| Target Availability ciblé, rerun checkpoint | Hooks, scope, writer, probe et sécurité | 71/71 | `TESTED_ONLY` + Gates terrain séparés |
| Worker V2 ciblé production | Unfollow/Search/Auto Restart + Availability | 242/242 | `CERTIFIED` pour la release |
| Worker full suite production | Suite complète | 2212 passed, 5 failures préexistants | `CERTIFIED` avec dette connue |
| Gate 3 Availability | 41 tests Availability | 41/41 | `TESTED_ONLY` |
| Gate 3 Golden Flow | 197 tests | 197/197 | `TESTED_ONLY` |
| Gate 3 session/identity/resume/reliability | 158 tests | 158/158 | `TESTED_ONLY` |
| Backend V2 | Tests applicatifs | 163/163 | `DEPLOYED_DORMANT` pour Availability |
| DB V2 | Tests migrations/contrats | 13/13 | `DEPLOYED_DORMANT` |
| Lifecycle pur | Modèle de domaine | 25/25 | `TESTED_ONLY` |
| Architecture | Frontières universelles | 3/3 | `TESTED_ONLY` |
| Premium Shadow | Calculs sans activation | 15/15 | `TESTED_ONLY` |
| CT Premium | Contrats non activés | 43/43 | `TESTED_ONLY` |
| TypeScript / ESLint / builds | Backend et Worker | verts | `TESTED_ONLY` |
| Signaux rares / stable ID / conflits | Terrain | non observé | `NOT_YET_OBSERVED` |
| Identity/assessment/current producers | Implémentation | absente | `NOT_YET_IMPLEMENTED` |

La couverture ciblée inclut les contrats scope, allowlist, kill switch, writer borné, payload, serialization et déterminisme. Les migrations/tests DB couvrent RLS, grants, append-only, FKs et idempotence ; les preuves Gate 4C ajoutent le contrôle cross-tenant/non-pilote et le cleanup terrain. Les cas de restart/reprise, race de projecteurs et backfill restent non certifiés pour la chaîne future.

Les 5 failures de la full suite étaient reproduites sur le parent `4f61e67` : 3 tests de lifecycle dispatcher et 2 frontières followers suggestions. Elles ne sont pas introduites par Target Availability, mais restent une dette de fiabilité à traiter avant un rollout large.

## 12. Open Risks

| ID | Risque | Sévérité | Probabilité | Impact | Mitigation / gate | Owner |
|---|---|---|---|---|---|---|
| R1 | Aucun stable Instagram ID fiable depuis l'UI | High | High | Faux renommage ou fusion d'identité | Fournisseur/asynchronous proof ; jamais username similarity | Architecture + Worker |
| R2 | Aucun producteur identity/assessment/current | High | Certain | Impossible de conclure Availability | Construire et tester avant Live Shadow | Backend/Domain |
| R3 | Signaux terminaux rares non observés | High | Medium | Faux permanent unavailable | Replay/synthetic + confirmations cross-run | QA + Worker |
| R4 | Politique de rétention/volume absente | High | Medium | Croissance DB et audit coûteux | Budget, partition/retention review avant scale | DB/Ops |
| R5 | Confidence/repeat/TTL encore proposés | Medium | High | États instables ou périmés | Versionner policy et simuler avant deploy | Product + Domain |
| R6 | Contradictions cross-run non certifiées | Medium | Medium | Flapping | Ordre déterministe, CAS, conflict state | Domain |
| R7 | Concurrence de projecteurs non observée | Medium | Medium | Current stale/out-of-order | Tests transactionnels et charge contrôlée | Backend/DB |
| R8 | p95 write ~594 ms sur 4 échantillons | Medium | Medium | Pression hook/queue au scale | Batch/queue telemetry, soak multi-compte | Worker/Ops |
| R9 | Isolation certifiée sur un seul pilote | Medium | Medium | Fuite lors d'un rollout large | Pilotes multi-tenant successifs | QA/Ops |
| R10 | Pas de replay anonymisé de cas rares | Medium | High | Régressions non détectées | Corpus redacted déterministe | QA |
| R11 | Monitoring/alerting Availability absent | Medium | High | Dérive silencieuse | Dashboard métriques + runbook avant activation | Ops |
| R12 | Backend adapters sans caller live | Medium | Certain | Différence code/terrain | Live Shadow limité, observatoire | Backend |
| R13 | Index signalés inutilisés | Low | High tant que dormant | Bruit advisor, faible coût write | Conserver jusqu'aux données de charge | DB |
| R14 | 5 failures Worker préexistants | Low | Medium | Confiance suite globale réduite | Corriger et recertifier séparément | Worker |

Décisions complémentaires : aucun backfill de verdict Availability n'est autorisé à partir de données legacy ambiguës. Un éventuel backfill futur devra porter uniquement sur des faits prouvés, être idempotent, réversible et faire l'objet d'un GO DB distinct. La reprise après interruption devra reprendre depuis les journaux append-only, jamais depuis un état mémoire non persisté.

Synthèse : Critical 0, High 4, Medium 8, Low 2.

## 13. Remaining Work

1. Définir et certifier la source du stable Instagram ID.
2. Implémenter localement l'identity resolver et l'historique sans renommage automatique.
3. Implémenter l'assessment déterministe avec confidence, repeat, TTL, ambiguity et recheck.
4. Implémenter les projecteurs `identity_current` et `availability_current` avec ordre et idempotence.
5. Définir la rétention, le coût, la volumétrie et les métriques DB.
6. Construire un corpus de traces anonymisées et synthétiques pour cas rares.
7. Certifier un Live Shadow Availability sur allowlist limitée.
8. Certifier plusieurs comptes puis plusieurs tenants, toujours sans enforcement.
9. Construire Performance et Utilization comme moteurs indépendants.
10. Certifier Target Lifecycle Shadow avec contradictions et stale evidence.
11. Définir la policy de plan ; conserver Growth/Pro sans remplacement automatique.
12. Construire puis certifier Premium Replacement Shadow, notifications et rollback avant toute activation.

## 14. Updated Roadmap

| # | Étape | Stade | Objectif et dépendances | Surfaces | Tests / run | GO / rollback |
|---:|---|---|---|---|---|---|
| 1 | Assessment computation | Construction | Confidence, répétition, ambiguity, TTL ; dépend du contrat observations et de la policy versionnée | Backend/domain ; DB existante ; aucun UI ; Worker inchangé sauf nouveaux faits prouvés | Fixtures + replay suffisent à construire ; cas terrain opportunistes | GO local si déterministe/fail-closed ; rollback flag OFF |
| 2 | `identity_current` / `identity_history` completion | Construction | Stable ID et historique ; dépend d'une source d'identité certifiée | Provider/Worker ou Backend ; 2 tables existantes ; aucun renommage UI | Synthetic + replay, puis runs cross-run obligatoires | NO-GO deploy sans stable ID ; current reconstructible |
| 3 | `availability_current` computation | Construction | Projecteur ordonné depuis assessments ; dépend 1–2 | Backend/DB adapters ; Worker/UI inchangés | Concurrence, out-of-order, CAS, replay | GO local si idempotent ; rebuild depuis append-only |
| 4 | Shadow Availability | Deployment puis Observation | Lire/calculer sans effet ; dépend 1–3, monitoring et allowlist | Backend reader/projector ; DB writer dormant ; aucun UI client | Pilote naturel recommandé, puis obligatoire avant extension | Kill switch + flags OFF, aucun enforcement |
| 5 | Replay / fixtures / synthetic rare cases | Construction/Observation | Couvrir deleted/suspended/banned/rename/restriction/ambiguity | Harness QA ; traces redacted ; aucune prod write | Replay et synthétique suffisants ; ne pas bloquer sur rare terrain | Corpus versionné ; suppression du corpus si non conforme |
| 6 | Policy Shadow | Construction puis Observation | Transformer assessment courant en proposition sans action ; dépend 4–5 | Backend/domain ; aucun Worker/DB additionnel par défaut ; UI ops future | Replay + run naturel recommandé | Flag OFF ; aucune notification/CT mutation |
| 7 | Lifecycle integration | Construction puis Observation | Agréger Availability, Performance, Utilization ; dépend moteurs indépendants | Backend Lifecycle ; DB/Worker selon contrats ; UI ops seulement | Replay + multi-run obligatoire | Retour au Shadow isolé ; aucun enforce |
| 8 | Notifications Growth / Pro | Construction puis Deployment contrôlé | Informer sans remplacement ; dépend 6–7 et copy/UX | Backend/email/UI ; audit log ; aucun Worker actionnel | Tests email + pilote humain ; run naturel non obligatoire | Feature flag, suppression queue, pas d'auto-action |
| 9 | Premium replacement recommendation | Construction puis Observation | Recommandation Premium-only, replacement-first ; dépend 7 | Backend policy/UI ; audit ; aucun remplacement automatique | Replay + Shadow multi-run | Flag OFF, recommandation masquée |
| 10 | Premium replacement limited activation | Activation | Activer sur allowlist explicite ; dépend tous gates précédents | Backend/UI/notifications ; DB audit ; Worker sans autorité décisionnelle | Run naturel obligatoire + soak | GO séparé ; kill switch/rollback complet |
| 11 | Multi-account rollout | Observation puis Activation graduelle | Étendre par compte puis tenant ; dépend stabilité 10 | Config/monitoring/ops, pas de schéma destructif | Runs naturels multi-tenant obligatoires | Paliers, SLO, rollback par allowlist/kill switch |
| 12 | Final block CT checkpoint and documentation | Documentation | Consolider architecture, preuves, policies et opérations ; dépend clôture gates | Docs uniquement | Aucun run propre ; références certifiées | Commit docs-only ; aucun effet runtime |

La roadmap sépare explicitement Construction, Deployment, Observation et Activation. Un succès de construction ou de déploiement ne saute jamais le gate d'observation correspondant.

## 15. Run-Dependency Matrix

| Phase | Aucun run requis | Synthétique / fixtures | Replay anonymisé | Run naturel | Terrain rare |
|---|---|---|---|---|---|
| 1. Assessment computation | Construction statique oui | Obligatoire | Obligatoire avant deploy | Recommandé pour Shadow | Opportuniste, non bloquant |
| 2. Identity history/current | Non pour le code | Obligatoire | Obligatoire | Obligatoire cross-run avant activation | Username change opportuniste |
| 3. Availability current | Construction statique oui | Obligatoire concurrence | Obligatoire out-of-order | Recommandé | Non requis |
| 4. Shadow Availability | Non | Préflight seulement | Requis | Obligatoire sur pilotes | Opportuniste |
| 5. Rare-case harness | Oui pour assembler corpus | Obligatoire | Obligatoire | Non requis pour clôturer le harness | Capturer quand disponible |
| 6. Policy Shadow | Construction statique oui | Obligatoire | Obligatoire | Recommandé puis obligatoire avant activation | Non bloquant |
| 7. Lifecycle integration | Non pour certification | Obligatoire | Obligatoire | Obligatoire multi-run | Opportuniste |
| 8. Notifications Growth/Pro | Oui pour tests unitaires/email | Obligatoire | Suffisant pour logique | Pilote humain recommandé, pas nécessairement un run Instagram | Non requis |
| 9. Premium recommendation | Non pour certification | Obligatoire | Obligatoire | Obligatoire avant UI large | Opportuniste |
| 10. Premium limited activation | Non | Préflight | Requis | Obligatoire + soak | Ne pas attendre artificiellement chaque cas rare |
| 11. Multi-account rollout | Non | Charge utile | Requis | Obligatoire multi-compte/tenant | Opportuniste |
| 12. Final CT documentation | Oui | Non | Références seulement | Aucun nouveau run | Aucun |

Les tests sans run peuvent prouver la forme, le déterminisme et la sécurité statique. Ils ne prouvent ni la sémantique d'une surface Instagram réelle, ni la latence terrain, ni la stabilité cross-run.

## 16. GO / NO-GO Matrix

| Décision | Verdict | Justification |
|---|---|---|
| Conserver les fondations DB dormantes | GO | Sécurité et intégrité certifiées. |
| Conserver les 4 observations Gate 4C | GO | Preuves valides et auditables ; ne pas les supprimer. |
| Construire localement identity/assessment/current | GO de préparation uniquement | Aucun runtime ni déploiement implicite. |
| Déployer ces futurs producteurs | NO-GO | Code absent, stable ID et policy non certifiés. |
| Live Shadow Availability limité | NO-GO maintenant | Nécessite étapes 4–7, monitoring et GO séparé. |
| Availability Shadow universel | NO-GO | Multi-tenant et volume non observés. |
| Policy Shadow | NO-GO | Performance/Utilization/Lifecycle incomplets. |
| Enforce Availability | NO-GO | Aucun gate d'activation. |
| Renommage automatique d'un CT | NO-GO | Stable ID non prouvé. |
| Archivage/remplacement automatique | NO-GO | Lifecycle et policy non certifiés. |
| Notification/email client | NO-GO | Aucun contrat de décision/UX validé. |
| Premium Replacement | NO-GO | Dépendances aval non construites/certifiées. |
| Growth/Pro replacement | NO-GO architectural | Hors politique prévue. |

## 17. To Include in Final CT Documentation

Les 24 éléments suivants devront être repris dans la documentation CT finale :

1. La définition stricte de Target Availability.
2. La séparation Availability / Performance / Utilization / Lifecycle / Premium Replacement.
3. Le diagramme de dépendances unidirectionnelles.
4. Les 5 tables et leurs responsabilités.
5. Les migrations exactes et l'état production.
6. Les contraintes RLS, ACL, FK et idempotence.
7. Le contrat append-only.
8. Le scope canonique tenant/account/target/run.
9. Le contrat des hooks `loaded` et `summary`.
10. Le writer borné, fail-open pour le run et fail-closed pour le scope.
11. La différence entre flags hérités au restart et kill switch dynamique.
12. Le contrat d'allowlist et le cleanup obligatoire.
13. Les preuves complètes Gate 4B.
14. Les preuves complètes Gate 4C.
15. Le fait que les 4 observations ne sont pas des verdicts Availability.
16. La règle stable ID obligatoire avant tout renommage.
17. La matrice des signaux, confidence, répétition, TTL et ambiguïtés.
18. La matrice de dépendance aux runs et replays.
19. Le runbook d'armement, de monitoring, de cleanup et de rollback.
20. Le monitoring, l'alerting, les SLO et les procédures incident.
21. La politique par package et la future UI opérateur/client.
22. Les contrats de notification/email et l'audit log.
23. L'intégration Lifecycle, Premium Replacement et leurs limitations.
24. Le glossaire et la règle `NEXT_STEP_AUTHORIZED=false` jusqu'à un GO explicite distinct.

## 18. Final Verdict

`CHECKPOINT_STATUS=GO — TARGET AVAILABILITY POST-GATE4C CHECKPOINT COMPLETED`

`TARGET_AVAILABILITY_CURRENT_STATE=OBSERVATION_FOUNDATIONS_CERTIFIED_DB_AND_RUNTIME_DORMANT`

`GATE4B_STATUS=CERTIFIED_MEMORY_ONLY_NO_DB_WRITE`

`GATE4C_STATUS=CERTIFIED_ONE_PILOT_LIMITED_DB_WRITER_RESTORED_SAFE`

`NEXT_PHASE_RECOMMENDATION=CONSTRUCT_IDENTITY_ASSESSMENT_CURRENT_AND_REPLAY_HARNESS_LOCALLY_THEN_REQUEST_SEPARATE_DEPLOYMENT_REVIEW`

`RUN_DEPENDENCY_SUMMARY=STATIC_CONTRACTS_TESTABLE_WITHOUT_RUN_BUT_IDENTITY_SIGNALS_LATENCY_CONCURRENCY_AND_POLICY_REQUIRE_REPLAY_AND_NATURAL_MULTI_RUN_EVIDENCE`

`OPEN_BLOCKERS=STABLE_ID_SOURCE;IDENTITY_ASSESSMENT_CURRENT_PRODUCERS;CONFIDENCE_REPEAT_TTL_POLICY;RARE_SIGNAL_REPLAYS;MULTITENANT_OBSERVATION;MONITORING;LIFECYCLE_PREMIUM_POLICY`

`RISKS_COUNT_BY_SEVERITY=CRITICAL:0,HIGH:4,MEDIUM:8,LOW:2`

`TO_INCLUDE_IN_FINAL_CT_DOCUMENTATION_COUNT=24`

`CODE_CHANGED=false`

`DB_CHANGED=false`

`RUNTIME_CHANGED=false`

`RESTART_COUNT=0`

`RUN_TRIGGERED=false`

`NEXT_STEP_AUTHORIZED=false`
