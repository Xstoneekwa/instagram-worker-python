# Recovery Engine

## Recovery-first architecture

Les flows sensibles (followers, ouverture profil, follow, **post-follow / return CT**) sont conçus pour :

- **échouer explicitement** avec une raison stable ;
- **réessayer** dans des bornes (compteurs, streaks, budgets temps) ;
- **abandonner proprement** plutôt que d’empiler des actions aveugles.

La recovery n’est pas un “catch-all” opaque : chaque tentative doit être **observable** et **justifiée** dans les logs.

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
