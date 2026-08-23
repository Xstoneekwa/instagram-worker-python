# Follow Action Engine

## P0C — global persistence preflight

Avant toute boucle candidat, le Worker doit prouver simultanément l'identité de
release canonique et la capacité de créer/remplacer/fsync un intent durable.
`follow_persistence_runtime_unavailable` est une panne globale de phase : elle
termine le run avant tout tap et ne peut jamais être convertie en rejet local
permettant de passer au candidat suivant. La valeur canonique du SHA est
`WorkerRuntimeIdentity.worker_sha`, résolue par le helper de release ;
`full_sha` n'appartient pas à ce contrat.

La queue P0C n'accepte désormais que la preuve d'un tap Follow physique dont
le résultat demeure ambigu (`follow_tap_sent=true`, sans vérification ni reçu).
Like-only, Mute-only et `prepare_failed` avant tap Follow sont non-actionnables
et ne créent aucune ligne de recovery. La récupération est exclusivement
backend/idempotente : `P0C_PHONE_SEARCHES=0`, aucun Global Search et aucun
nouveau tap.

Toute preuve durable Like ou Mute dans Social Memory bloque les admissions
Follow futures pour la clé `account_id + normalized_username`, indépendamment
du CT. Cette règle est évaluée une fois à l'admission : elle ne peut pas
auto-bloquer la transaction V2 déjà admise qui produit elle-même son Like.
L'ordre physique reste strictement `LIKE → FOLLOW → MUTE → RETURN_CT`.
Like/Mute-only ne fabrique jamais de vérité Follow et ne peut donc jamais
alimenter le backlog Unfollow.

Limitation connue : un renommage Instagram n'est pas réconcilié par ce patch.
L'Identity Recovery reste un chantier séparé ; aucun rapprochement heuristique
de usernames n'est autorisé ici.

## Vue d’ensemble (V2 / V3)

Le **Follow Action Engine** est le chemin **profil déjà ouvert** (notamment candidat visuel) pour :

- attendre / scorer la surface “follow” ;
- appliquer le **exact follow fast path** quand les signaux sont suffisamment clairs ;
- émettre des événements structurés pour le runner et la persistence (mirroring logs / social memory).

Les versions **V2/V3** désignent l’évolution du pipeline de **sélection d’élément**, du **scoring**, et du **harvester** de contrôles ; le détail exact des constantes et fonctions reste dans le code (`follow_action_engine.py`).

## Exact follow fast path

Quand la surface et le candidat passent des garde-fous stricts (fast path **exact**), le moteur peut :

- dériver des **coordonnées de tap** directement depuis les bounds du contrôle retenu ;
- réduire la latence et l’ambiguïté des re-sélections XML.

Ce chemin est **intentionnellement conservateur** : un mauvais fast path coûte cher (faux follow, mauvais compte). Toute évolution doit préserver les **seuils** et les **logs** de décision documentés dans le code.

## Hybrid soft acceptance

Le moteur peut combiner :

- **règles strictes** (reject explicites : Following, Requested, Message, etc.) ;
- et une **acceptation souple** contrôlée pour certains layouts (hybrid), toujours avec **traçabilité** (scores, raisons de rejet).

## Follow Control Harvester V3

Le **harvester** agrège et classe les **contrôles candidats** “Follow-like” issus de la surface (XML / surface engine). Il alimente le classifieur et le fast path.

Objectifs : **rappel** (ne pas rater le vrai Follow) tout en **précision** (ne pas confondre avec d’autres CTAs).

## CTA classifier

Un **classifieur de CTA** distingue Follow / variantes locales des autres actions (Message, Contact, etc.). Les évolutions doivent rester **alignées** avec les exclusions ci-dessous.

## Exclusions explicites

Ne **pas** traiter comme Follow les contrôles liés à :

- **Contact**
- **Message** / messagerie
- **Email**
- **Call** / téléphone
- **WhatsApp**
- **Invite** (inviter, inviter des amis, etc.)

Ces éléments sont des **signaux de risque** de faux tap et de mauvaise navigation.

## `future_use = contact_scraper_candidate`

Les surfaces **Contact** (et assimilés) peuvent être **marquées pour usage futur** (scraper email / contact Instagram) : **ne pas les détruire** par un tap Follow erroné ; les conserver comme **candidats métier** distincts dans la modélisation / les logs si le code expose ce champ.

## Règles anti-faux tap

- Toujours **rejeter** les CTAs hors scope (liste ci-dessus).
- Croiser **position**, **texte**, **resourceId**, **score**, et **état header** (Following / Requested) quand disponible.
- En cas de doute : **pas de tap** ; préférer **observation** supplémentaire ou **échec explicite** avec `failure_reason` stable.
- Toute régression “Contact tapé comme Follow” est **bloquante** : prioriser tests / logs sur les jeux de labels Instagram multilingues.

## Contrat public / privé avant Follow

Lorsque `dont_follow_private_accounts=true`, l'absence du texte « private »
n'est jamais une preuve publique. Le tap exige une identité exacte, le package
Instagram exact, le CTA Follow et une preuve positive de surface profil
publique (onglets profil dans le même snapshot frais). Une surface incomplète
ou inconnue échoue fermée avec `candidate_public_status_unproven`.

Après le tap, l'état `Requested` est une preuve autoritaire de compte privé. Il
est rejeté par le point de sortie commun avec
`follow_requested_rejected_by_private_policy`, y compris sur le fast path RID,
et ne peut jamais publier `follow_completed` ni déclencher le Post-Follow.

## Limite globale avant action — Active SAST Days V1

Le moteur Follow conserve ses gardes d'action existantes, mais leur budget
global provient désormais du minimum entre cap configuré, maximum package,
palier warmup par journées actives SAST, hard caps ops et quota journalier
restant. Le warmup ne modifie jamais les settings persistés. Une action qui
ferait dépasser la limite effective doit être refusée avant le tap.

## Never Follow Twice V1 — admission future uniquement

Une réussite `follow_verified_persisted_v1/success` est une vérité canonique
permanente pour `account_id + normalized_username`. La projection monotone
`ever_followed_canonical_at` / `ever_followed_action_id` est produite
uniquement depuis cet événement et n'est jamais effacée par Unfollow.

Le batch Social Memory existant exclut les admissions futures avec
`already_followed_canonical_once`. Le RPC de persistance est protégé au niveau
de la table canonique : le replay du même `action_id` reste idempotent, tandis
qu'un nouvel `action_id` après une réussite historique est rejeté. Une preuve
UI, Like, Mute, Requested ou ambiguë ne fabrique jamais cette vérité.

Cette garde est évaluée avant l'admission. La transaction V2 déjà admise reste
strictement `LIKE → FOLLOW → MUTE → RETURN_CT`; la projection créée pendant la
persistance Follow ne peut pas auto-bloquer Mute ou Return CT. Les exclusions
Like/Mute Already Interacted et la reconciliation P0C fail-closed restent
inchangées.

Limite connue : la clé demeure le username normalisé. La récupération
d'identité après rename n'appartient pas à ce chantier.
