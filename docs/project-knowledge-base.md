# Project Knowledge Base — Phone Farm Instagram

Ce document est la verite globale de handoff du projet Phone Farm Instagram.
Il ne remplace pas `docs/outreach-entry-api.md`, qui reste la documentation
technique detaillee des Entries, APIs, RPC, credentials et provisioning.

## 1. Big Picture

Le projet construit une Phone Farm Instagram production-grade :

- backend Supabase comme source durable des comptes, credentials metadata,
  statuts, actions dashboard, incidents et jobs;
- Python worker pour l'execution locale, les probes, le provisioning futur et
  les sessions business;
- Edge Functions Supabase pour les boundaries API server-side;
- dashboard client/admin pour les statuts, actions et workflows operateurs;
- app locale Mac / BotApp future pour piloter les workers et les devices;
- phones physiques, clones/app profiles et assignments pour la capacite runtime;
- architecture hybride obligatoire : XML/accessibility rapide, vision, state
  machine et recovery pour les actions critiques.

## 2. Etat Courant Valide

Checkpoints recents valides :

- Entry 2E-5A : classifier/provisioner skeleton;
- Entry 2E-5B : login UI probe;
- Entry 2E-5C : isolated login probe CLI;
- Entry 2E-5D : visible `app_start` probe;
- Entry 2E-5E : Login Screen Router;
- Entry 2E-5F : Controlled Action Executor;
- Entry 2E-5G : Secure Credential Runtime Access Design;
- Entry 2E-5H : Supabase Vault Reader Helper checkpointe.
- Entry 2E-5H-2 : real Supabase Vault read RPC + fake secret smoke valide,
  sans vrai credential client.
- Entry 2E-5I : Controlled Password Form Executor valide mocks-only,
  sans smoke reel.
- Entry 2E-5J : Provisioner Orchestrator Skeleton en cours de validation
  mocks-only, sans run device ni vrai login.
- Entry 2E-5J-2A : smoke prep no-password en cours, pour verifier device idle,
  app_start, probe/router/orchestrator dry-run sans credential.

Le runtime principal n'est pas encore branche au login/provisioning complet :
pas de vrai login, pas de password tap, pas de runner hook, pas de run device
provisioning.

## 3. Checkpoints SHA / Tags Recents

- 2E-5F :
  - SHA `eb01e002f90e7a870ed8d10df19f1bdb044517e0`
  - tag `checkpoint-entry2e5f-login-action-executor-20260526`
- 2E-5G :
  - SHA `47da05b006e0f3bc9293d8b369bb36a365a746ac`
  - tag `checkpoint-entry2e5g-secure-credential-runtime-access-20260526`
- 2E-5H :
  - SHA `880deef630c8a6217f9fbc4d64b7167fd3e20764`
  - tag `checkpoint-entry2e5h-supabase-vault-reader-helper-20260526`
- 2E-5H-2 :
  - SHA `1d3ced61d813139bdc4f3f3c202127dd35f40e8a`
  - tag `checkpoint-entry2e5h2-real-vault-read-rpc-20260526`
- 2E-5I :
  - SHA `349587f816dae843bca2b37d38f15cc76d425bcf`
  - tag `checkpoint-entry2e5i-password-form-executor-20260526`

## 4. Architecture Actuelle

- Supabase est la source de verite durable.
- Edge Functions gerent les boundaries API et les controles d'auth/ownership.
- Les modules Python ajoutes restent isoles et injectables tant que le runtime
  complet n'est pas approuve.
- Supabase Vault stocke les secrets; `account_credentials` reste metadata-only.
- `account_dashboard_actions` est la projection UI actionnable.
- `account_incidents` est la verite ops durable.
- `account_incident_notifications` est l'audit de livraison Slack/Discord.
- `client_instagram_accounts` porte les statuts dashboard-safe :
  `login_status`, `provisioning_status`, `onboarding_status`.

Sources de verite separees :

- credentials secret value : Vault uniquement;
- credentials metadata/status : `account_credentials`;
- login/provisioning status client/admin : `client_instagram_accounts`;
- actions dashboard : `account_dashboard_actions`;
- incidents ops : `account_incidents`.

## 5. Roadmap Provisioning / Login

Deja pose :

- login UI probe;
- Login Screen Router;
- Controlled Action Executor;
- Secure Credential Runtime Access;
- Supabase Vault Reader Helper;
- real Vault read RPC service-role only;
- Controlled Password Form Executor mocks-only valide;
- Provisioner Orchestrator Skeleton mocks-only en cours.
- Orchestrator dry-run/no-submit pour smoke prep no-password.

Etapes suivantes :

- 2E-5H-2 : RPC service-role only de lecture Vault + smoke fake secret valide;
- finaliser 2E-5J : orchestrateur isole probe/router/action/credentials/password
  executor/classifier/publisher injectable;
- 2E-5J-2A : smoke prep no-password device idle + app_start + probe/router +
  orchestrator dry-run;
- 2E-5J-2 smoke orchestrator controle ou 2E-5I-2 smoke reel `cinema_catchup`
  via flow securise
  `instagram-credentials` -> Vault apres validation explicite;
- integration provisioner runtime derriere flags;
- status publish connected/2FA/checkpoint/failed via orchestrateur futur;
- state machine login + recovery avant branchement runtime principal.

Contraintes permanentes :

- pas de login par-dessus un compte actif/inconnu;
- pas de password dans logs, prompts, fichiers locaux ou dashboards;
- pas de tap password avant ecran `login_form_empty` valide et contexte test
  dedie.
- `instagram_login_password_form_executor.py` reste un executor bas niveau :
  aucun retry par defaut, aucune escalation directe, aucun status publish,
  aucune ecriture Supabase.
- Le futur provisioner orchestrator decidera retry/escalation/status publish et
  dashboard actions a partir de `failure_reason` et `post_submit_outcome`.
- 2E-5J centralise cette policy dans un module isole, avec `publish_enabled=false`
  par defaut et publisher injectable/mocke.

Retry policy future :

- aucun retry pour `login_form_not_validated`, `expected_username_missing`,
  `password_secret_missing`, `password_secret_invalid`, `login_failed`,
  `needs_2fa`, `checkpoint`, mismatch / wrong account;
- mini retry possible cote orchestrateur seulement pour
  `username_field_not_found`, `password_field_not_found`,
  `login_button_not_found`, `ambiguous_login_form`, `post_submit_dump_failed`,
  `input_failed`, `submit_failed`;
- retry max 1, uniquement apres revalidation `login_form_empty`;
- pas de boucle infinie; si encore failure apres retry : stop safe, status
  provisioning/login selon contexte, dashboard action si necessaire.

Post-submit policy future :

- `connected` -> succes;
- `needs_2fa` -> action dashboard `complete_two_factor`;
- `checkpoint` -> action dashboard `resolve_checkpoint`;
- `login_failed` -> `update_password` ou `review_login_failure`, sans retry avec
  le meme password;
- `unknown` -> re-observe possible 1 fois, puis `retry_later` ou
  `support_required`.
- 2E-5J ne lance aucun smoke reel; toute execution device future exige une
  fenetre device idle, un lock UI exclusif et un compte test dedie.
- 2E-5J-2A autorise uniquement un smoke prep sans password : app_start,
  dump/probe/router/orchestrator dry-run; aucun credential, aucun tap `Log in`,
  aucun publish.
- Smoke prep 2E-5J-2A execute sur `emulator-5554` : device unique, aucun run
  business detecte, `app_start` OK, dump UI OK,
  `screen_type=continue_as_candidate`, `suggested_username=i_m_your_traker`,
  boutons Continue / Use another profile / Create new account detectes,
  `would_submit_password=false`, `would_publish=false`.
- Sans lifecycle confirme, le router bloque en mismatch; avec lifecycle mock
  `canceled` + `clone_reuse_allowed=true`, le dry-run prevoit
  `use_another_profile_previous_account_stopped` sans cliquer.
- Entry 2E-5J-2B : tentative de gate pour smoke controle `Use another profile`
  no-password. Pre-check device OK et ecran `continue_as_candidate`, mais aucune
  entree lifecycle exploitable n'a confirme que `i_m_your_traker` est
  canceled/stopped/archived. Action stoppee avant tap avec
  `reason=lifecycle_not_confirmed`.
- Entry 2E-5J-2B smoke apres lookup operateur : premier essai stoppe avant tap
  avec `failure_reason=ambiguous_target_button` car `description="Use another profile"`
  retournait 2 matches UIAutomator2 alors que le dump XML n'avait qu'un seul
  libelle texte exact non cliquable.
- Correctif 2E-5J-2B : l'action executor resout maintenant la cible via hierarchy
  XML (bounds + zone verticale + deduplication), puis tap unique au centre des
  bounds valides; pas de coordonnees fixes d'ecran.
- Smoke reel 2E-5J-2B apres correctif : `action_executed=true`,
  `post_action_screen_type=login_form_empty`, `ready_for_password_smoke=true`,
  `would_submit_password=false`, `would_publish=false`. Aucun password, aucun
  login, aucun Vault read, aucun publish.
- Entry 2E-5J-2C : smoke no-password Cas B `Continue as expected account`.
  Pre-check device OK, `expected_username=cinema_catchup`,
  `suggested_username=cinema_catchup`, router
  `continue_expected_account`. Tap controle `Continue` execute une seule fois,
  resolution `selector_text`; post-action pre-login probe `unknown`, mais
  classification UI login `connected` / home feed. Aucun password, aucun tap
  `Log in`, aucun credential, aucun Vault read, aucun publish.
- Mini patch 2E-5J-2C : apres `Continue`, l'orchestrator normalise le cas
  pre-login probe `unknown` + classifier `connected` en
  `final_outcome=connected`, `password_required=false`,
  `ready_for_password_smoke=false`, sans credential, sans retry password et sans
  publish par defaut. La meme passerelle couvre `needs_2fa`, `checkpoint` et
  `login_failed` comme status candidates post-action.
- Entry 2E-5K : ajout du flow `Continue -> continue_password_only -> password`.
  Le probe reconnait un ecran password-only quand le username attendu est deja
  visible, que le champ `Password` et le bouton `Log in` sont presents, sans
  champ username editable. Les overlays Android/Google/password manager sont
  marques `overlay_present` et ne remplacent pas la
  classification metier.
- L'executor password saisit uniquement le password en mode
  `continue_password_only`, via `SecretValue.reveal_for_login_executor()`, puis
  tap `Log in`. Si un overlay bloque une fois le submit, une recovery minimale
  `back` + refocus password + retap est autorisee une seule fois. Aucun password
  en result/metadata/logs, aucun Vault read en tests, aucun runner hook.
- Validation reelle 2E-5K no-password 2026-05-26 : pre-check device OK,
  `app_start` Instagram OK, pre-action
  `continue_as_candidate/cinema_catchup` avec router
  `continue_expected_account`, puis un seul tap controle `Continue`.
  Le premier post-Continue etait `Loading...`; l'orchestrateur fait maintenant
  une seule re-observation courte a 1500 ms, avec metadata
  `post_continue_initial_screen=transition_loading`,
  `post_continue_reobserve=true`, `post_continue_reobserve_count=1`.
  Observation reelle suivante : `screen_type=continue_password_only`,
  username `cinema_catchup`, champ `Password`, bouton `Log in`, pas de champ
  username editable, overlay Google `Suggest strong password` detecte comme
  parasite (`overlay_present=true`, `overlay_blocking_business=false`),
  `password_required=true`, `ready_for_password_submit=true`. Aucun password,
  aucun tap `Log in`, aucun Vault read, aucun publish. Le vrai submit reste
  futur via Vault/`SecretValue` uniquement.
- Entry 2E-5J-2B-1 : audit lifecycle read-only. Les statuts existants couvrent
  `client_instagram_accounts` login/provisioning/onboarding,
  `client_subscriptions` active/paused/cancelled/expired,
  `account_assignments` pending/reserved/active/paused/failed/released et
  `phone_clones` available/reserved/active/maintenance/disabled, mais aucun row
  n'etablit le lifecycle du compte Instagram suggere par username. Solution
  recommandee avant migration : helper lookup read-only injectable, source
  explicite operateur, mockable en tests, qui retourne uniquement
  `lifecycle_status` et `clone_reuse_allowed`.
- Patch 2E-5J-2B-1 : l'orchestrator accepte
  `previous_account_lifecycle_lookup(username, context)`. Ce lookup temporaire
  sert uniquement au smoke operator-approved, reste generique, ne hardcode aucun
  username et n'autorise `Use another profile` que si le lifecycle est
  `canceled/stopped/archived` avec `clone_reuse_allowed=true`. Le cas observe
  `i_m_your_traker` reste une fixture de terrain/documentation, pas une logique
  metier.

## 6. Dashboard / Backend / BotApp Registry

Le dashboard, le backend et BotApp devront exposer plus tard uniquement des
statuts safe :

- credentials configured/missing;
- Vault reader status operational/pending;
- credentials verification pending;
- login attempt status futur;
- retry provisioning;
- `login_status`;
- `provisioning_status`;
- `onboarding_status`;
- actions `submit_instagram_credentials`, `update_instagram_password`,
  `complete_two_factor`, `resolve_checkpoint`, `review_login_failure`,
  `review_account_mismatch`;
- lifecycle compte : active, paused, canceled, onboarding;
- vraie source lifecycle account active/paused/canceled/onboarding a creer plus
  tard cote DB/dashboard;
- policy `clone_reuse_allowed` explicite a connecter aux assignments/clones et
  aux actions admin/client;
- audit `previous_account_stopped_override`;
- retry provisioning et relance verification credentials.
- Le dashboard/backend/BotApp ne doit jamais exposer password, `secret_ref`,
  Vault UUID, XML/screenshot, device id, token ou service-role key.

Interdits cote dashboard/backend/BotApp :

- password;
- `secret_ref`;
- Vault UUID;
- token/cookie/service-role;
- device id client-side;
- XML/screenshot brut.

Un futur bouton/admin action pourra relancer une verification credentials, mais
ne devra jamais lire ni afficher le secret.

## 7. Scheduler / Device Rules

Regles device a respecter avant orchestration multi-clone :

- 1 phone = 1 action UI active a la fois;
- smoke reel login seulement sur fenetre device idle;
- business window/preflight et smoke login doivent rester separes;
- pas de visible preflight sur clone B pendant que clone A tourne;
- lock UI exclusif au niveau device;
- buffer technique entre clones;
- fenetre device 6h = business effectif environ 5h55 + buffer/preflight;
- preflight visible seulement quand le phone est idle;
- a relier plus tard avec phone rest et interdiction de runtime 24h/24.

Implication : les probes visibles, `app_start`, login et recovery device doivent
passer par un scheduler/lock device, pas par des appels concurrents ad hoc.

## 8. Phone Rest / Quotas / Session 6h

Roadmap runtime :

- Phase B day limits;
- business session 6h window guard;
- phone rest, cooldown et off-hours;
- packages 80/80 et 120/120;
- ne pas creer un double quota system.

Les quotas produit et les limites runtime doivent converger vers une source de
verite claire. Les patchs provisoires ne doivent pas multiplier les compteurs
ou appliquer des limites non branchees au runtime.

## 9. ATX / Device Runtime Control Layer

ATX / AtxAgent reste a prevoir pour les phones physiques :

- preflight ATX avant session;
- verification UIAutomator health;
- fermer/masquer ATX avant Instagram;
- relier ATX au lock device et a la recovery;
- pas encore patché dans ce depot.

ATX ne doit pas devenir une navigation parallele non observee. Il sert au
controle device, pas a contourner la state machine Instagram.

## 10. NO-GO Securite

- No password in chat, Cursor prompt, logs, git, dashboard, XML ou screenshots.
- No `secret_ref` / Vault UUID client-side.
- No direct dashboard PostgREST vers les tables secretes.
- No runner hook premature.
- No login over active unknown account.
- No device id client-side.
- No parameter dashboard si le runtime ne l'applique pas reellement.
- No Supabase service-role ou Authorization header dans logs/errors.
- No app/device action irreversible sans etat, reason et recovery explicites.
- No fake secret output complet pendant les smokes; uniquement longueur,
  hash prefix court et labels rediges.

## 11. Prochaine Etape Logique

Ordre recommande :

1. Confirmer explicitement le lifecycle canceled/stopped/archived du compte
   suggere actuellement visible via un helper lookup read-only explicite, ou
   afficher directement `login_form_empty` sur device idle.
2. Valider le smoke password-only reel `cinema_catchup` uniquement via le flow
   securise Vault/SecretValue, sans password dans chat/Cursor/logs/git.
3. Definir le modele durable lifecycle compte + `clone_reuse_allowed` dans la
   DB/dashboard, sans hardcode username.
4. Integration provisioner runtime derriere flags.
5. Status publish connected/2FA/checkpoint/failed via provisioner orchestrator.
6. State machine / recovery login plus riche avant tout branchement production.
7. Rappel device : 1 phone = 1 action UI active; aucun password dans
   chat/Cursor/logs/git/XML/screenshots.

Le checkpoint 2E-5H est deja pousse :
`880deef630c8a6217f9fbc4d64b7167fd3e20764`.
Le checkpoint 2E-5H-2 est deja pousse :
`1d3ced61d813139bdc4f3f3c202127dd35f40e8a`.

## 12. Source Docs

- Details Entry/API/RPC/credentials/provisioning :
  `docs/outreach-entry-api.md`.
- Architecture officielle :
  `AGENTS.md` et les documents sous `docs/`.
- Future dashboard / BotApp :
  a completer depuis le code Codex et les references BotApp.

Regle de maintenance : toute evolution majeure doit mettre a jour cette base de
connaissance quand elle modifie l'etat global, la roadmap, les limites runtime,
les garanties de securite ou les checkpoints.
