# Recovery Engine

## Human confirmation is not a navigation recovery

`instagram_human_confirmation_required` is a hard, account-global safety
boundary, not a candidate-local recovery error. It must remain the first
causal reason through Worker outcome, orchestration, incident projection,
restart eligibility, Admin/BotApp, and Slack/Discord. It must never be
relabelled as `profile_identity_activity_unproven`,
`followers_recovery_surface_unproved`, or a generic checkpoint when the
specific fresh surface proof exists.

No automatic Continue tap, dismissal, back navigation, retry loop, or ADB
action is allowed. Repeated observations deduplicate on account + challenge
family while the incident is unresolved. Resolving the backend incident does
not prove the physical challenge disappeared: the next explicitly authorized
attempt must run a fresh Identity Guard and is admitted only after the exact
expected account surface is freshly proved.

## Récupération P0C des mutations interrompues

La queue `follow_candidate_recovery_queue` conserve uniquement les mutations
Follow physiques ambiguës. Le claim est atomique (`SKIP LOCKED`), scopé au
compte, limité à huit intentions actuelles, loué cinq minutes et idempotent via
`recovery_key`.

- Follow tap physique ambigu : reconciliation backend contre la vérité
  canonique existante, sans observation Instagram ni duplicate tap ;
- Like-only ou Mute-only durable historique : projection Social Memory puis
  terminalisation `non_actionable_already_interacted_no_follow_recovery` ;
- UNKNOWN : quarantaine fail-closed, candidat-local, sans préemption CT ;
- reçu Follow et état `following` ne sont jamais fabriqués par la recovery.

Le chemin normal est `bounded backend P0C reconciliation → CT Resume/CT
courant → Followers → Follow60 V2`. P0C n'utilise jamais Instagram Global
Search (`P0C_PHONE_SEARCHES=0`) et une ligne historique arbitraire ne peut plus
consommer le budget avant le travail courant. Une panne locale d'une ligne de
recovery reste locale et ne préempte pas le CT ; l'indisponibilité globale de
la queue reste fail-closed avant la phase.

Like/Mute-only ne vaut jamais Follow : ces projections sont exclues des futures
admissions Follow mais restent inéligibles au backlog Unfollow, lequel exige
toujours un reçu Follow canonique durable. Les renommages Instagram ne sont pas
traités ici et restent du ressort d'une future Identity Recovery.

Une panne systémique de persistance bloque aussi l'Auto Restart sur la même
release. Le dispatcher renouvelle séparément la lease exacte
request/run/worker tant que le child est vivant ; la device lease ne remplace
jamais cette preuve de propriété.

## Recovery-first architecture

Les flows sensibles (followers, ouverture profil, follow, **post-follow / return CT**) sont conçus pour :

- **échouer explicitement** avec une raison stable ;
- **réessayer** dans des bornes (compteurs, streaks, budgets temps) ;
- **abandonner proprement** plutôt que d’empiler des actions aveugles.

La recovery n’est pas un “catch-all” opaque : chaque tentative doit être **observable** et **justifiée** dans les logs.

## Follow durable, post-Follow incomplet

Quand le Follow canonique est confirmé et persisté mais qu'une étape Mute/Like
reste incomplète, la recovery ne peut ni effacer cette mutation ni retaper
Follow. Le classifieur commun exige les reçus crash-safe de l'outbox, notamment
`return_ct_exact`, l'identité candidat et la conservation idempotente du groupe.
Il émet alors `target_local_follow_durable_post_follow_pending` et arrête la
branche Follow à une frontière sûre.

Le budget ajouté par cette fermeture est nul : aucune nouvelle tentative UI,
aucun Back, aucun polling et aucun délai fixe. L'Auto Restart ou la reprise
suivante doit traiter l'outbox avant tout nouveau Follow. La phase Unfollow
obligatoire reste indépendante et peut démarrer après l'exit 53 uniquement si
le handoff exact, l'identité runtime et tous les gardes compte/plateforme sont
valides. Un exit 53 mal formé, un challenge ou une persistance Follow non prouvée
reste bloqué fail-closed.

## Retries intelligents

- Retries **conditionnés** par l’état observé (pas de retry identique sans nouveau signal).
- Limites **max rounds**, **max backs**, **budgets** par phase pour éviter les sessions de plusieurs minutes sur un seul candidat.
- Distinction entre **retry légitime** (XML stale, overlay temporaire) et **signal de dérive** (écran DM, reel, composer).

## Anti-dérive

Règles transverses (alignées post-follow et autres recoveries) :

- Détection de **surfaces dangereuses** (story, post viewer, commentaire, message, search hors contexte, etc.).
- En dérive : **pas** d’exploration (pas de swipe profond, pas d’ouverture de post pour “voir où on est”).
- **Un** geste de sortie minimal (ex. un `back`) seulement si le contexte est jugé **safe** (ex. Instagram au premier plan, overlay connu).
- Si l’état ne revient pas à une **base sûf** (profil CT, liste CT, profil candidat attendu) : **abort** avec reason dédiée.

## Abort rules

- **Streak** de nav défavorable (`UNKNOWN` répété, search, launcher, etc.) au-delà d’un seuil configuré.
- **Budget temps** de round dépassé sans confirmation de la cible.
- **Surface drift** détectée et non résolue après le back contrôlé unique.

Chaque abort doit produire un **code / chaîne de failure** stable pour dashboards et post-mortem.

## No exploratory navigation during recovery

Pendant recovery **critique** (surtout post-follow) :

- interdits : tap sur **média**, **story**, **commentaire**, **composer** ;
- interdits : **swipe** exploratoire, **reopen** profil/liste par taps complexes si la config désactive ce chemin ;
- autorisé avec prudence : **back** contrôlé, **observe_instagram_state**, **détection** followers / profil.

## Wrong-depth recovery : état courant autoritaire

Une recovery ne déduit jamais la profondeur courante de l'action qui vient
d'échouer. Elle choisit l'action suivante uniquement depuis une classification
fraîche de la surface réelle. Le cas critique est un détecteur générique
positif alors que l'Identity Guard prouve encore le profil CT : la branche
correcte est une réouverture directe de Followers depuis ce CT, et non une
suite de Back.

Chaque geste de navigation suit le contrat : invalider la preuve précédente,
envoyer une seule action, observer à nouveau, classifier, puis décider. Un
second Back sans observation intermédiaire est interdit. Si aucune surface ne
peut être prouvée dans le budget borné, la session s'arrête en
`partial_resumable` avec `target_completed=false`; aucun checkpoint incertain,
reçu Follow/Mute/Like ou épuisement de cible n'est publié.

La première raison causale (`followers_recovery_surface_unproved` ou raison de
réacquisition canonique) doit rester dans le résumé et les diagnostics même si
une enveloppe de session ajoute ensuite une raison terminale plus générale.

## Logs obligatoires

- Nom d’événement **stable** (`post_follow_return_ct_*`, `visual_follow_*`, etc.).
- Champs : `phase`, `attempt`, `visual_candidate_id`, `source_profile_username`, `navigation_state`, `failure_reason` / `how` selon le module.
- Les branches **recovery** et **abort** ne doivent pas être silencieuses.

## Reasons explicites

Toute sortie d’échec ou d’abandon doit porter une **reason** lisible et stable (pas seulement `False` / code numérique opaque côté métier). Les PR doivent documenter les **nouvelles** reasons ajoutées.

## Reprise contrôlée après intervention humaine (P3)

- Chaque run `account_session` crée **tôt** (avant toute action UI) un resume
  plan canonique dans `account_session_resume_plans`
  (`account_session_resume_plan_store.py`) ; le dispatcher y conserve la
  reason terminale exacte, l'orchestrateur y persiste le verdict de fin de
  session. Les runs historiques sans plan restent `resume_plan_missing`.
- Un échec actionnable (identity guard, package, login, device, crash) laisse
  le plan en `awaiting_human_resume_authorization` : **aucun retry
  automatique** sans clic humain « Prêt à relancer ».
- L'autorisation humaine (`incident_resume_authorizations`, backend) est
  consommée **atomiquement** par le tick Auto Restart : 1 clic → 1 request de
  reprise max → 1 fenêtre active. Reasons stables :
  `awaiting_human_resume_authorization`, `resume_authorization_expired`,
  `resume_authorization_consumed`, `resume_retry_window_exhausted`,
  `resume_plan_not_recoverable`, `resume_window_closed`.
- L'identity guard reste le safe-stop final inchangé sur la reprise ; un
  nouvel échec enrichit l'incident original (« Nouvelle intervention
  requise ») sans boucle ni spam de notifications.

## Contrat de reprise terminale Golden V1

`partial_resumable` est une issue métier conservée dans le plan JSON, jamais
un état de cycle SQL. Une fin de session partielle, sûre, avec quota positif,
phase explicite et aucun marqueur unsafe devient `resume_requested`; la
décision associée est `schedule_resume` et ne peut être évaluée que par le
prochain tick naturel.

La persistance terminale n'est plus best-effort. Elle est liée de façon
idempotente à `run_id / run_request_id / root_business_session_id /
execution_attempt_no`, écrite une seule fois par RPC et relue avec le digest
exact après toute réponse ambiguë. Si la confirmation manque, le Worker sort
en `resume_plan_reconciliation_required` tout en conservant le verdict métier
dans le résumé du run.

Au début du tick, une réconciliation bornée peut transformer un ancien
`run_active` en `resume_requested` uniquement si run et request sont
terminaux, qu'aucune exécution ni lease device n'est active, et que le plan
final prouve quota, phases, frontière sûre et absence de marqueur unsafe. La
réconciliation ne crée aucune request et ne consomme aucun essai. Les cas
ambigus restent bloqués avec `STALE_RESUME_PLAN_STATE`.

## Barrière de déploiement du dispatcher

Un switch de release ou un restart canonique échoue fermé tant que l'une des
frontières globales n'est pas à zéro : requests actives, runs pending/running,
device locks valides ou tick locks `started`. Le contrôle est exécuté avant le
switch et de nouveau avant tout signal envoyé au dispatcher. Une indisponibilité
Supabase bloque également l'opération. Cette barrière empêche un déploiement de
transformer une reprise canonique en arrêt `SIGTERM`/143 au milieu du bootstrap.

## Popup Instagram de consentement publicitaire

La modale Instagram `Choose if we process your data for ads` est une frontière
globale d'intervention humaine. Le détecteur canonique combine le titre, le CTA
`Get started`, le corps observé et le package Instagram. Le Worker ne choisit
jamais le consentement et publie un incident/action opérateur idempotent avec
compte, device et app-instance.

- En Auto Login, un username propre encore lisible et exactement identique
  permet le passage de l'Identity Guard puis la persistance `connected`; la
  demande opérateur reste ouverte.
- Sans preuve exacte, la session reste authentifiée mais
  `identity_pending_popup`, sans retour au formulaire ni relecture des
  credentials. Après traitement manuel, la reprise réobserve le profil propre
  dans la même lignée.
- Avant toute action Follow, Unfollow, DM/Welcome, Like ou autre geste métier,
  la modale produit un arrêt `partial_resumable` sans tap, sans faux reçu et
  sans incrément de compteur.

Reasons stables :

- `instagram_ads_data_consent_popup_identity_pending`
- `instagram_ads_data_consent_popup_business_action_paused`
# Followers recovery generation boundary

Recovery actions invalidate all actionable Followers viewport and row evidence before the UI
mutation. A successful recovery must reacquire and recertify the list before candidate
selection. A CT Resume gesture with unproved continuity also discards pre-gesture candidates;
it cannot fall through to legacy evaluation with old row coordinates.
