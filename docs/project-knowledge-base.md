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
- Entry 2E-5L : Cas D direct `login_form_empty` sur `app_start`. Instagram peut
  ouvrir directement le formulaire vide complet avec champ
  `Username, email or mobile number`, champ `Password`, bouton `Log in`,
  `Forgot password?`, `Create new account` et `Meta`. Le probe expose
  `username_editable_present=true`, `password_field_editable_present=true`,
  `password_required=true`, `ready_for_credentials_flow=true`. Le routeur donne
  `start_login_form_flow`; le dry-run no-password donne
  `would_request_credentials=true`, `would_submit_password=false`,
  `would_publish=false`. Validation reelle faite sans saisie username/password,
  sans tap `Log in`, sans Vault read et sans publish. Le vrai submit reste futur
  via Vault/`SecretValue` uniquement. Le patch couvre l'ecran observe EN; des
  aliases FR/EN pourront etre ajoutes plus tard.
- Entry 2E-5M : Cas E account picker / profile chooser. Instagram peut ouvrir
  une liste de comptes (`account_picker`) avec plusieurs usernames visibles,
  `Use another profile`, `Create new account` et `Meta`. Le probe expose
  `available_usernames`, `expected_username_present` et
  `expected_username_match_count`. Si le compte attendu est present une seule
  fois, le routeur retourne `select_expected_account_from_picker` et l'action
  executor fait un seul tap sur la ligne du `expected_username`, via hierarchy
  XML/bounds, sans coordonnees fixes et sans cliquer un autre compte. Si le
  compte attendu est absent, ambigu ou manquant en input, le flow stoppe safe
  sans tap. Validation reelle no-password : account picker avec
  `cinema_catchup` et `i_m_your_traker`, tap unique sur `cinema_catchup`,
  aucun tap `i_m_your_traker`, aucun `Use another profile`, aucun `Log in`,
  aucun password/Vault/publish. Le premier post-action a ete `unknown`
  transitoire, puis stabilisation vers `continue_password_only` avec
  `ready_for_password_submit=true`; le vrai submit reste futur via
  Vault/`SecretValue` uniquement.
- Entry 2E-5N / 2E-5P-17 : Cas E/F old logged-in account recovery. Instagram peut
  ouvrir sur le home/feed ou le profil d'un ancien compte encore connecte. Le
  probe expose `active_account_home`, `active_account_profile`,
  `account_switcher_sheet`, `add_account_sheet` et `actual_logged_in_username`.
  **Un feed connecte seul ne suffit pas** : `active_account_home` ne doit pas
  conclure `connected_no_password_needed` sans identite verifiee ; sinon stop safe
  `identity_unknown_on_connected_home` ou recovery add-existing selon lifecycle.
  Smoke : `--operator-smoke-active-account-username` pour le gate Cas E quand le
  XML feed n'expose pas le username. Si le compte actif est le compte attendu, le
  flow finalise `connected` sans recovery. Si le compte actif
  est different, le recovery n'est autorise que par lifecycle gate explicite :
  `lifecycle_status in canceled/stopped/archived` et
  `clone_reuse_allowed=true`, avec source temporaire
  `operator_smoke_override` pour le smoke. Sinon stop safe
  `block_wrong_active_account`, dashboard
  `review_logged_in_account_mismatch`, aucun clic. Le recovery V1 suit le chemin
  Instagram sans logout automatique : home -> profil -> account switcher via
  username/fleche -> `Add Instagram account` / `Add profile` ->
  `Log into existing account`, puis retour vers les cas deja couverts
  (`continue_as_candidate`, `account_picker`, `login_form_empty`,
  `continue_password_only`, `connected`). Toujours no-password : aucun `Log in`,
  aucun password, aucun Vault read, aucun publish par defaut. Les aliases EN sont
  couverts dans le patch; les aliases FR (`Ajouter un compte Instagram`,
  `Ajouter un profil`, `Se connecter a un compte existant`,
  `Ajouter un compte existant`) restent documentes pour extension. Smoke reel
  2026-05-26 : depart `active_account_home`/profil
  `actual_logged_in_username=i_m_your_traker`,
  `expected_username=cinema_catchup`, lifecycle override
  `canceled + clone_reuse_allowed=true`, recovery autorise. Navigation
  account switcher -> `Add Instagram account` -> `Log into existing account`,
  puis re-observation vers `continue_as_candidate`; le flow existant a tape
  `Continue` et stoppe sur `continue_password_only` en `credentials_missing`,
  `ready_for_password_submit=true`, sans password, sans `Log in`, sans Vault,
  sans logout et sans publish.
- Entry 2E-5O : Cas G logout fallback old canceled account. Ce chemin est
  separe du Cas F et ne sert que si `Add Instagram account` ->
  `Log into existing account` echoue ou n'est pas disponible. Il est opt-in :
  le flow login principal ne declenche pas de logout automatiquement. Le gate
  impose `actual_logged_in_username` detecte dynamiquement sur profil,
  different de `expected_username`, puis lifecycle explicite
  `canceled/stopped/archived + clone_reuse_allowed=true` via
  `operator_smoke_override` temporaire ou future DB lifecycle. Sinon stop safe
  `review_logged_in_account_mismatch`, aucun logout. Le patch ajoute
  `profile_menu_ready`, `profile_menu_missing_transient`,
  `profile_menu_sheet`, `settings_and_activity`, `save_login_info_prompt` et
  `logout_confirmation_prompt`. Avant menu, l'orchestrateur attend/reobserve une
  fois puis tente au plus un refresh safe `Home` -> `Profile`; si le hamburger
  reste absent ou si le username change, stop safe. Sur `Save your login info?`,
  il tape uniquement `Not now`; sur `Log out of your account?`, il tape `Log out`
  uniquement apres gate confirme. Ecran final attendu : cas deja couverts
  (`login_form_empty`, `continue_as_candidate`, `account_picker`,
  `continue_password_only`, `connected`) ou stop safe apres reobserve unique.
  Si `Log out` n'est pas visible dans settings, un scroll controle borne tente
  de rejoindre la section `Login` avant de stopper safe. Toujours
  no-password/no-leak : aucune saisie, aucun `Log in`, aucun Vault, aucun
  publish, aucune ecriture Supabase, aucune coordonnee fixe. Smoke reel
  2026-05-26 : profil `i_m_your_traker`, lifecycle override `canceled +
  clone_reuse_allowed=true`, hamburger OK, scroll settings vers `Log out`, prompt
  `Save your login info?` gere par `Not now`, confirmation logout geree par
  `Log out`. L'orchestrateur a stoppe safe sur `post_logout_unknown_screen` apres
  reobserve unique; inspection passive juste apres : stabilisation vers
  `account_picker`.
- Entry 2E-5P : premier smoke password reel controle via Vault/`SecretValue`.
  Le seul chemin autorise est `instagram-credentials` -> Supabase Vault ->
  runtime Vault reader -> `SecretValue` -> password executor. Aucun password,
  `secret_ref` complet, UUID Vault, service-role key, bearer token, XML brut ou
  screenshot path ne doit etre affiche. Le smoke ne peut partir que de
  `continue_password_only` ou `login_form_empty`, avec username attendu
  `cinema_catchup` confirme. Validation 2026-05-26 : tests pre-smoke OK et
  device idle, mais stop safe avant saisie car aucune credential row active ne
  satisfait simultanement username attendu, `secret_provider=supabase_vault` et
  `secret_ref` Vault valide; l'ecran observe etait aussi `unknown`, donc non
  accepte. Aucun Vault password read runtime local, aucun password saisi, aucun
  tap `Log in`, aucun publish. Prochaine action : soumettre/mettre a jour les
  credentials via le flow securise `instagram-credentials`, jamais dans Cursor.
- Entry 2E-5P retry 2026-05-26 : apres setup du token interne, credentials
  `cinema_catchup` prets (`active`, version 1000, `reauth_required=true`,
  provider Vault, reference de forme valide, username attendu confirme,
  secret charge en memoire). Pre-check device OK (`emulator-5554` unique, aucun
  runner/sender/follow/unfollow/outreach projet actif). Observation passive :
  ecran initial `unknown`, donc non accepte. Stop safe avant
  `SecretValue.reveal_for_login_executor()`, aucune saisie password, aucun tap
  `Log in`, aucune publication HTTP et aucune ecriture de statut Supabase.
  `final_outcome=unknown`,
  `status_candidate=unknown_stop_safe_initial_screen_not_accepted`,
  `would_publish=false`.
- Correction architecture login/provisioning 2026-05-27 : les flows reels et
  smokes reels demarrent maintenant Instagram par defaut avant probe via
  `app_start(package_name)`, avec `package_name=com.instagram.android`
  configurable pour futurs clones et wait court borne (`0..3000 ms`, defaut
  `1500 ms`). Le mode sans start est explicitement
  `observe_current_screen_only=True` / `--observe-current-screen-only` et reste
  reserve aux tests/diagnostics. Objectif : ne plus prendre launcher Android,
  home Android, autre app, clone non ouvert, ecran stale ou transition comme
  base de routing prod. Apres app_start, le flow dump/probe/classifie puis route
  vers Cas A-G; aucun password submit ni Vault reveal tant que le routing
  n'aboutit pas a `continue_password_only` ou `login_form_empty`. Echec start :
  `app_start_failed`; start OK + ecran `unknown` :
  `screen_preparation_failed`; connected direct :
  `connected_no_password_needed`, sans publish.
- Relance 2E-5P 2026-05-27 avec app_start obligatoire : pre-check device OK,
  credentials inchanges et prets, `app_start_attempted=true`,
  `app_start_ok=true`, package `com.instagram.android`, wait `1500 ms`.
  L'ecran apres start reste `unknown`, donc stop safe
  `screen_preparation_failed`. Aucun routing Cas A-G exploitable,
  `preparation_flow_used=none`, `screen_before_submit=unknown`,
  `submit_executed=false`, `password_submit_result=not_executed`,
  `final_outcome=unknown`, `would_publish=false`. Aucun password, aucune
  reference Vault complete, aucun token/header, aucun XML brut et aucune ecriture
  status Supabase.
- Entry 2E-5Q-1 inspection safe 2026-05-27 : apres le `unknown` de preparation,
  inspection passive app_start + wait `1500 ms` + dump/probe sans tap ni
  credential. L'ecran observe est `continue_as_candidate`, top activity
  Instagram modal, avec labels safe `cinema_catchup`, `Continue`,
  `Use another profile`, `Create new account`, Meta/Instagram. Aucun signal
  password, `Log in`, home/feed, profil, loading, checkpoint ou 2FA. Le
  `classifier_outcome=unknown` est acceptable pour cet ecran pre-login non final:
  le screen type de routing est reconnu, mais le classifier status ne doit pas
  publier de connected/2FA/checkpoint/failed. Cause probable du `unknown`
  precedent : etat transitoire/stale ou timing de preparation. Pas de patch code;
  2E-5P reste bloque sans submit tant que le flow controle n'atteint pas
  `continue_password_only` ou `login_form_empty`.
- Entry 2E-5P relance screen-prep 2026-05-27 : le flow part de
  `continue_as_candidate` attendu apres app_start, tape `Continue` une seule
  fois, puis stabilise vers `continue_password_only`. Le submit est autorise
  uniquement apres verification username attendu + champ password + bouton
  `Log in`. Un patch minimal de `instagram_login_password_form_executor.py`
  deduplique les strategies selector uniques (`text` puis `content-desc`) quand
  la primaire est unique, tout en conservant le refus si une meme strategie
  retourne plusieurs cibles. Submit reel execute une seule fois via
  `SecretValue.reveal_for_login_executor()` dans l'executor; aucun retry
  password, aucun publish. Resultat : `final_outcome=unknown`,
  `status_candidate=unknown`, `password_submit_result=submitted_not_connected`.
  Le post-submit a montre un etat non classable avec seulement un label safe
  `OK`, sans signal connected/2FA/checkpoint/login_failed exploitable. No-leak
  confirme; aucune ecriture status Supabase.
- Entry 2E-5P-2 Password required dialog : la popup Instagram
  `Password required` / `Enter your password to continue.` / `OK` est maintenant
  un cas explicite `password_required_dialog`, avec aliases FR documentes
  (`Mot de passe requis`, `Saisissez votre mot de passe pour continuer`, `OK`).
  L'executor password stoppe avant submit si une lecture accessible prouve que le
  champ password est vide (`password_input_not_confirmed`). Si la popup apparait
  apres submit, recovery bornee : tap `OK` une fois, refocus, refill via
  `SecretValue.reveal_for_login_executor()` dans l'executor, second `Log in`
  unique. Si la popup reapparait :
  `final_outcome=password_input_failed`, aucun retry supplementaire. Le mapping
  orchestrateur conserve `password_input_missing_or_not_accepted` /
  `password_input_failed` sans les replier en `unknown`, sans publish par
  defaut. Smoke reel post-patch : la popup n'etait plus visible; app_start a
  d'abord stoppe safe sur preparation `unknown`, puis l'ecran s'est stabilise en
  `continue_as_candidate`; une relance a tape `Continue` une seule fois et a
  stoppe safe sur `unknown_login_screen` pendant transition. Aucun submit
  supplementaire, aucun publish/status write.
- Entry 2E-5P-4 Vault password extraction : bug critique identifie apres 2E-5P-3.
  L'ADB Keyboard injectait correctement, mais la valeur etait parfois le JSON
  Vault complet (`password`, `account_id`, `credentials_version`, `created_at`,
  etc.) car `SecretValue` recevait le secret brut sans extraction. Patch :
  `parse_vault_secret_for_login` extrait uniquement `password`, le Vault reader
  et le runtime credentials normalisent avant `SecretValue`, l'executor garde
  `blocked_secret_payload_shape` avant toute injection. Dry-run no-device OK :
  `vault_secret_is_json=true`, `vault_secret_has_password_key=true`,
  `extracted_password_valid=true`, `secret_value_safe_for_injection=true`,
  `injectable_password_only=true`, sans afficher password/payload. Aucun submit
  device dans ce patch ; smoke 2E-5P-4 separe apres validation operateur.
- Entry 2E-5P-5 Password-only secure login smoke (device, post-2E-5P-4) :
  pre-check OK (`emulator-5554`, pas de runner/business). Credentials safe :
  `active` v1000, Vault, `injectable_password_only=true`,
  `guard_would_block_revealed_value=false`. Preparation :
  `continue_as_candidate` -> tap `Continue` une fois ->
  `continue_password_only`. Submit : `adb_keyboard_b64`,
  `password_field_non_empty_confirmed=true`, `submit_executed=true`, aucune
  popup `Password required`, `retry_count=0`. Post-submit :
  `logged_out/session_expired` (pas `connected`); injection/extraction Vault
  validees, session Instagram a traiter separement. `would_publish=false`.
  No-leak confirme. Tag
  `checkpoint-entry2e5p5-password-only-secure-login-smoke-20260526`.
  Correction 2E-5P-7 : ce resultat venait d'un dump post-submit immediat
  (`post_submit_wait_ms=0`) et ne doit plus etre considere comme final fiable
  sans settling borne.
  CLI reproductible ajoute : `instagram_login_provisioner_cli.py` lance un flow
  isole avec `--device-serial`, `--expected-username`, `--account-id`,
  `--package-name`, app_start par defaut, `--no-publish` par defaut et JSON
  safe. Le CLI genere un `run_id` safe et append un log JSONL safe dans
  `logs/instagram_login_provisioner.jsonl`; les commandes post-run doivent
  filtrer par `run_id` et ne re-sortir que les champs autorises, dont
  `post_submit_observation_count`, `post_submit_wait_total_ms`,
  `post_submit_screens` et `final_terminal_screen`.
  `--dry-run` / `--no-submit` ne charge pas Vault et ne submit pas.
  `--observe-current-screen-only` est reserve diagnostic.
- Entry 2E-5P-7 Post-submit settling : apres `submit_tapped=true`, l'executor
  password observe maintenant plusieurs fois de maniere bornee avant de
  classifier. `connected`, `needs_2fa`, `checkpoint`, `login_failed` et
  `password_required_dialog` sont terminaux; `Password required` garde la
  recovery OK/refocus/refill/un seul second submit. `logged_out` devient final
  seulement apres settling complet avec `reason=session_expired_after_settling`;
  `unknown` devient `post_submit_unknown_after_settling`. Aucun publish/status
  write par defaut, aucun retry password hors recovery bornee, logs JSONL safe
  uniquement.
- Entry 2E-5P-8 Google Password Manager save prompt : le settling post-submit
  detecte maintenant `screen_type=google_password_manager_save_prompt` via les
  signaux safe `Google Password Manager` / `Save password for Instagram?` /
  `Continue` et alias FR documentes. Le flow ne clique jamais `Continue`, ne
  sauvegarde jamais le mot de passe, dismiss une seule fois par `back`, puis
  reobserve Instagram. Si la popup reste visible :
  `final_outcome=save_password_prompt_blocking`, no retry infini, no publish.
  Les logs safe exposent seulement `save_password_prompt_detected`,
  `save_password_prompt_dismissed`, `dismiss_method` et
  `post_dismiss_screen_type`.
- Entry 2E-5P-9 Startup settling : apres `app_start_ok=true`, l'orchestrateur
  ne conclut plus `unknown` sur un seul dump trop precoce. Il observe jusqu'a
  quatre fois de maniere bornee, avec fast path si le premier ecran est clair,
  et route des que `continue_as_candidate`, `account_picker`,
  `login_form_empty`, `continue_password_only`, `active_account_home/profile`
  ou un etat terminal exploitable apparait. Si tout reste `unknown` :
  `reason=screen_preparation_failed_after_startup_settling`. Logs safe :
  `startup_observation_count`, `startup_wait_total_ms`, `startup_screens`,
  `startup_final_screen_type`, `screen_after_app_start_initial/final`.
- Entry 2E-5P-10 Long post-submit loading : si toutes les observations apres
  `Log in` restent sur `loading`, l'executor retourne maintenant
  `final_outcome=login_submit_still_loading` avec
  `reason=post_submit_loading_timeout`, au lieu d'un `unknown` final. Le CLI
  expose `--post-submit-timeout-ms` (defaut 10000 ms) et logge
  `post_submit_timeout_ms`, `post_submit_interval_ms` et
  `post_submit_loading_timeout`. La popup Google Password Manager reste
  interdite au bouton `Continue`; dismiss safe par `back`, 2 tentatives max,
  puis `save_password_prompt_blocking` si encore visible. Les smokes device
  reels sont lances par l'operateur depuis son terminal sauf demande explicite.
- Entry 2E-5P-11 Cas A router wiring : bug observe avec override operateur visible
  (`operator_smoke_override`, `canceled`, `clone_reuse_allowed=true`) mais
  `router_decision=unknown_no_action`. Cause : mismatch entre
  `screen_after_app_start_final=continue_as_candidate` et le `screen_type` passe
  au routeur (`active_account_home` / `unknown`). Correctifs : priorite probe
  Continue-as, `_routing_screen_type` avec fallback startup, filet
  `_route_provisioning_screen`, settling post `tap_use_another_profile`
  (`post_use_another_profile_*`, `screen_after_use_another_profile_final`),
  `preparation_flow_used=use_another_profile_previous_account_stopped`. Le
  formulaire prefilled reste la etape suivante, pas le premier correctif.
- Entry 2E-5P-12 Username replace prefilled : apres Cas A, l'ecran
  `login_form_prefilled_username` doit remplacer l'ancien username editable avant
  tout reveal password. Correctifs : cible EditText prioritaire, clear cascade
  (`clear_text`, `set_text("")`, `set_text(expected)`, `adb_keyboard_b64`),
  confirmation hierarchy, reasons `username_clear_failed` /
  `username_still_prefilled_after_input`, logs
  `username_field_focused_before_input`, `username_clear_method`,
  `username_input_method`, `username_input_ms`. Smokes `--no-publish`.
- Entry 2E-5P-13 Password masked confirmation : apres username replacement
  valide, le password injecte via `adb_keyboard_b64` peut apparaitre masque
  visuellement alors que la cible accessibilite reste au placeholder `Password`.
  L'executor confirme maintenant le non-empty par cible password EditText,
  bullets/masked chars dans le hierarchy, ou `unknown_but_input_success` si ADB
  B64 a reussi sans preuve de champ vide. Si un signal safe prouve vide :
  `password_input_not_confirmed`, no submit. Logs safe :
  `password_field_target_kind`, `password_input_method`,
  `password_input_result`, `password_confirm_method`. Recovery
  `Password required` bornee conservee; smoke toujours `--no-publish`.
- Finalisation 2E-5P-13 : Cas A full flow valide en run operateur
  (`connected`, username remplace, password masque confirme, save-password
  prompt dismiss par `back`, `would_publish=false`). Correctif metadata :
  conserver l'override lifecycle initial apres `Use another profile`; la
  re-observation `login_form_prefilled_username` ne doit plus remplacer
  `operator_smoke_override/canceled/clone_reuse_allowed=true` par des valeurs
  par defaut `unknown/false`.
- Entry 2E-5P-14 Account picker full flow : le CLI generique supporte Cas B
  sans flag operateur. Si `screen_type=account_picker`, le router selectionne
  uniquement `expected_username` present dans `available_usernames`; si absent :
  `expected_account_not_listed`, no tap/no credentials/no submit. Apres tap,
  settling borne `post_account_picker_*`. Sorties normales : direct
  `connected_home` sans submit, ou `continue_password_only` / formulaire login
  puis password submit via `SecretValue`. Logs safe ajoutes :
  `available_usernames`, `expected_username_present`,
  `selected_account_username`, `account_picker_selection_executed`,
  `screen_after_account_picker_final`. Smokes toujours `--no-publish`.
- Entry 2E-5P-14B Account picker target resolution : le compte attendu peut etre
  n'importe ou dans la liste visible (2–6+ comptes). L'executor regroupe les
  nœuds accessibility d'une meme row par chevauchement vertical ; plusieurs
  zones pour un seul username sur une row ne sont plus traitees comme ambigues.
  Selection par username exact + bounds, jamais par index fixe.
  `account_picker_selected_row_index_if_known` est diagnostic seulement. Scroll
  picker si compte hors viewport : complement futur documente.
- Entry 2E-5P-15 Direct empty login form full flow : le CLI generique supporte
  Cas C sans flag special. Si `screen_type=login_form_empty`, le router choisit
  `start_login_form_flow`, l'executor saisit `expected_username` puis revele le
  password via `SecretValue` uniquement apres validation username/formulaire,
  submit `Log in`, post-submit settling et dismiss Google Password Manager si
  present sans cliquer `Continue`. Cas C reste distinct de
  `login_form_prefilled_username` : `username_replaced=false`,
  `username_input_result=username_input_confirmed` quand confirme. Metadata safe
  ajoutee/fiabilisee : `username_input_ms`. Smokes toujours `--no-publish`.
- Entry 2E-5P-15B Login form empty username confirmation : sur `login_form_empty`
  direct, le placeholder username n'est plus traite comme un username pre-rempli.
  L'executor prefere l'EditText username, ignore les hints, confirme via hierarchy
  ou `username_input_assumed` apres `set_text` reussi, et reserve
  `username_still_prefilled_after_input` au Cas A avec un vrai ancien username.
  Metadata safe : `username_placeholder_ignored`.
- Entry 2E-5P-16 Continue-as expected login flow : le CLI generique supporte Cas
  D sans flag special. Le demarrage stabilise sur `continue_as_candidate` avec
  `suggested_username=expected_username`, tape `Continue`, observe
  `continue_password_only`, verifie le username affiche, puis revele le password
  via `SecretValue`. Le flow ne saisit jamais le username
  (`username_input_result=not_required`), injecte seulement le password, submit,
  post-submit settling et dismiss Google Password Manager si present sans cliquer
  `Continue`. Metadata safe : `displayed_username`, `password_only_username`,
  `username_match`. Un demarrage direct sur `continue_password_only` reste un cas
  futur separe. Smokes toujours `--no-publish`.
- Entry 2E-5P-17 / 2E-5P-17B Active account home add-existing-account path : si
  Instagram ouvre sur le home/profil d'un ancien compte connecte different de
  `expected_username`, le flow principal n'est autorise que si lifecycle old
  account est `canceled`/`stopped`/`archived`, `clone_reuse_allowed=true`, avec
  source explicite (`operator_smoke_override` en smoke). Le chemin ouvre profil,
  account switcher, `Add Instagram account`, puis settling post-add. Deux chemins
  valides : sheet `Log into existing account` (E1) ou ecran deja couvert direct
  (E2, ex. `login_form_empty`). La sheet n'est pas obligatoire ;
  `add_account_sheet_opened=false` + `log_into_existing_account_tapped=false`
  sont OK sur E2. Stop safe `post_add_existing_unknown` si l'ecran reste inconnu
  apres settling. Ce n'est pas le logout fallback : aucun `Log out`,
  pas de Settings logout, pas de `Create new account`, pas de Accounts Center.
  Metadata safe : `actual_logged_in_username`, `active_account_username`,
  `recovery_path=add_existing_account`, et les booleans d'etapes add-existing.
- Entry 2E-5P-18 Controlled logout fallback : Cas F est un fallback explicite,
  jamais le chemin principal. Cas E add-existing reste prioritaire sans flag.
  Logout n'est autorise que si `actual_logged_in_username != expected_username`,
  lifecycle old account `canceled`/`stopped`/`archived`,
  `clone_reuse_allowed=true`, source sure (`operator_smoke_override` ou
  `lifecycle_lookup_safe`) et `--operator-smoke-allow-logout-fallback true`.
  Le flow ouvre le menu profil/settings, cherche `Log out` dans
  `Settings and activity`, scrolle vers le bas de facon bornee si le bouton est
  hors viewport, puis tape uniquement une cible texte/accessibilite visible et
  non ambigue. Pas de coordonnees fixes; si absent apres les scrolls :
  `logout_not_visible_after_scrolls`. Il choisit `Not now` sur
  `Save your login info?`, confirme `Log out`, settling post-logout borne (ne pas
  conclure `unknown` trop tot), puis reprend `account_picker`,
  `continue_as_candidate`, `login_form_empty`, `login_form_prefilled_username` ou
  `continue_password_only`. Entry 2E-5P-18B : post-logout peut etre un
  `continue_as_candidate` de l'ancien compte — dans ce cas `Use another profile`,
  pas `Continue`, puis reprise des cas couverts. La reprise post-logout interne
  est observe-only et ne relance pas Instagram ; le JSONL conserve
  `app_start_attempted/app_start_ok` du demarrage initial CLI. Smokes toujours
  `--no-publish`, sans runner ni flow business.
- Entry 2E-5P-19 Central provisioning/login orchestrator :
  `run_login_provisioning_flow(...)` est la fonction centrale appelee par le CLI.
  Elle fait `app_start` par defaut, startup settling, classification, routage
  vers les sous-flows valides, reprise post-action, submit credentials seulement
  apres identite confirmee, et JSONL no-leak/no-publish. Metadata :
  `central_orchestrator_used=true`, `central_orchestrator_version`,
  `selected_route`, `selected_route_reason`. Routes : `continue_as_expected`,
  `use_another_profile`, `account_picker`, `login_form_empty`,
  `login_form_prefilled_expected`, `replace_prefilled_username`,
  `continue_password_only`, `already_connected_expected`, `add_existing_account`,
  `logout_fallback`. Cas E reste prioritaire; Cas F seulement avec flag explicite.
  Cas G/H (bad password, password-required, 2FA/checkpoint) sont reportes apres
  dashboard client/admin hors detection terminale safe deja existante.
- Entry 2E-5P-20 Controlled backend status publish :
  le CLI central peut maintenant injecter le publisher status backend existant
  (`instagram_account_status_publisher.py`) mais seulement si
  `LOGIN_PROVISIONER_PUBLISH_ENABLED=true` **et** `--publish` sont presents.
  `--no-publish` garde la priorite absolue. V1 publie uniquement un succes sur
  `connected` sur, avec `ok=true`, `completed=true`, `status_candidate=connected`,
  `account_id` present et route centrale sure. Les cas G/H, mismatch/review,
  unknown/logged-out ambigu et identity unknown restent non publies
  (`not_publishable` ou `deferred_until_dashboard`). Le publish est fail-open :
  un echec backend laisse le resultat login local en `connected` et ajoute
  seulement `publish_result=failed`, `publish_error_code` safe et
  `publish_failed_safe`. JSONL safe expose `would_publish`, `published`,
  `publish_enabled`, `publish_attempted`, `publish_reason`, `publish_result`,
  `publish_error_code`; aucune fuite password, `secret_ref`, UUID Vault, token,
  XML brut ou screenshot path. Slack/Discord restent inchanges : aucun nouveau
  type de notification, aucun nouveau webhook, aucun changement de contenu,
  frequence ou canal, et aucun envoi direct depuis le provisioner. Un succes
  normal `connected` est backend status only, sans Slack/Discord. Contrat futur :
  tout evenement deja notifie Slack/Discord doit aussi avoir une trace backend
  safe et pouvoir apparaitre en dashboard admin; s'il exige une action client, il
  doit aussi etre projetable en dashboard client via dashboard action/status sync.
  Les evenements internes restent admin-only. `--no-publish` bloque tout publish
  backend/status/action dans ce flow.
- Entry 2E-5P-11 Login form username prefilled : apres `Use another profile`,
  Instagram peut afficher un formulaire login avec l'ancien username deja rempli.
  Nouveau `screen_type=login_form_prefilled_username`. Si le champ username est
  editable et different de `expected_username`, le router choisit
  `start_login_form_flow_replace_username`, l'executor clear/remplace par
  `expected_username`, puis seulement ensuite revele le password `SecretValue` et
  submit. Si le prefilled username est deja correct :
  `start_login_form_flow_prefilled_expected`, sans ressaisie inutile du username.
  Si le champ n'est pas editable : `username_prefilled_not_editable`, no submit.
  Si clear/input echoue : `username_input_failed`, no password submit si possible.
  Logs safe ajoutes : `screen_type`, `prefilled_username`, `username_replaced`,
  `username_input_confirmed`, `username_input_result`, `router_decision`,
  `preparation_flow_used`, `screen_before_submit`. Ce cas ne doit pas etre
  confondu avec un mismatch compte bloquant; les smokes restent `--no-publish`.
- Standard futur provisioning/login : chaque flow doit fournir une commande
  terminal reproductible ou un CLI dedie. Les options minimales sont
  `--device-serial`, `--expected-username`, `--account-id` si credentials
  requis, `--package-name`, app_start par defaut, `--observe-current-screen-only`
  diagnostic, `--dry-run` / `--no-submit` quand applicable, `--no-publish` par
  defaut et sortie JSON safe avec timings, no-leak et forbidden scope explicite.
  Les CLIs ne doivent pas accepter password/`secret_ref` en argument, ne doivent
  pas publier ni ecrire status Supabase par defaut, et ne doivent pas hooker
  runner/sender/follow/outreach business.
- Entry 2E-5P-3 Password input injection : cause racine identifiee. En mode
  `continue_password_only`, l'ancien target `text="Password"` pointait vers un
  label `android.view.View`, pas vers le vrai `android.widget.EditText`. Le
  diagnostic reel a montre `adb_keyboard_b64` success mais
  `password_field_non_empty_confirmed=false`, donc le guard a bloque `Log in`
  avec `password_input_not_confirmed`. Patch : preferer l'`EditText` unique en
  password-only, focus accessibilite + fallback bounds, ADB Keyboard
  `ADB_INPUT_B64` via stdin en priorite, `set_text` fallback, guard non-empty
  avant `Log in`. Smoke reel apres patch : `input_method_used=adb_keyboard_b64`,
  `password_field_focused_before_input=true`,
  `password_field_non_empty_confirmed=true`, `submit_tapped=true`, aucune popup
  `Password required`, `retry_count=0`; resultat post-submit
  `logged_out/session_expired`, sans publish/status write. No-leak confirme :
  aucun password, longueur/hash, `secret_ref` complet, UUID Vault complet,
  token/header, XML brut ou screenshot path.
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

## 13. Dashboard Foundation 1A

Dashboard Foundation 1A est le premier socle backend read-only pour les futures
vues admin/client :

- Admin Manage ;
- Admin Radar / Server Check ;
- projection client-safe ;
- compatibilite future BotApp / Mac app.

Objets ajoutes :

- `public.get_admin_account_overview(...)` ;
- `public.get_admin_radar_overview(...)`.

Ces RPC sont volontairement internes/admin-safe en V1 :

- `SECURITY DEFINER` ;
- `service_role` uniquement ;
- pas de grant `anon` ou `authenticated` ;
- pas encore d'Edge Function admin dashboard ;
- pas encore de JWT admin/assistant/client.

La projection Manage expose uniquement des champs safe et stables : compte,
client, username, email masque, statuts dashboard, subscription/package,
entitlements, status credentials safe, status login/provisioning/onboarding,
actions dashboard pending, blocking campaign, dernier incident, timestamps safe.

La projection Radar expose une premiere classification `health_status` et
`health_reason`, plus les champs stables necessaires aux futures KPI cards et
quick rules. Les metriques absentes restent `null`, `unknown`, `false` ou `[]` :
le backend ne fabrique pas de faux chiffres.

Regles no-leak maintenues :

- jamais de password, hash ou longueur password ;
- jamais de `secret_ref`, Vault id, token, Authorization header, service-role ;
- jamais de webhook Slack/Discord ;
- jamais de XML brut, screenshot path, raw logs ou metadata sensible ;
- jamais d'`adb_serial`, `device_udid`, USB/hub port dans une projection client.

Roadmap apres 1A :

- 1B/1C : Edge/JWT admin et projection client-safe avec ownership explicite ;
- groupe admin : activity log, special care, phone notes, phone display order,
  mutation status dropdown avec audit ;
- groupe business critique separe : Source Quality Control / FBR CT et sync
  Target Accounts ;
- groupe BotApp ops plus tard : operations, webhooks, gateway, phone controls
  reels, AI log analysis.

## 14. Dashboard Settings Registry

`docs/dashboard-settings-registry.md` est la source de verite documentaire pour
rebrancher l'UI admin Codex `/instagram-dashboard` sur le backend Phone Farm.

Synthese :

- l'UI existante est un prototype admin riche mais legacy GramBot/Appium ;
- elle ne doit pas etre jetee, mais ne doit pas etre exposee client telle
  quelle ;
- `ig_account_settings` ne doit pas rester la source principale des settings ;
- les reads admin doivent passer par une future API/Edge admin-dashboard puis
  par les RPC 1A et les projections safe ;
- les writes admin doivent passer par des APIs de domaine avec validation role,
  entitlement, support runtime et audit log ;
- le dashboard client futur sera separe et client-safe uniquement.

Points critiques conserves :

- `password` en clair dans `ig_account_settings` est legacy et a deprecier ;
- credentials via `account_credentials` + `instagram-credentials` + Vault ;
- `device_udid`, device internals, raw logs, raw metadata, XML, screenshot
  paths, `secret_ref`, Vault id, tokens, webhooks et `service_role` sont
  interdits client ;
- `account_status` monolithique doit etre remplace par `admin_status`,
  `login_status`, `provisioning_status`, `subscription_status` et
  `automation_health`.

Ordre recommande apres ce registre :

1. DF-1B admin-dashboard Edge/API over RPC 1A pour Manage/Radar read-only.
2. Credentials via API dediee.
3. DM vers `ig_account_dm_settings` + `ig_dm_templates`.
4. Targets/CT vers `ig_targets`.
5. Unfollow/Follow vers les tables runtime verifiees.
6. Filters apres reconciliation runtime.
7. Safety/Device en admin/ops-only avec audit obligatoire.

## 15. Dashboard Foundation 1B

Dashboard Foundation 1B ajoute l'Edge Function read-only
`admin-dashboard` au-dessus des RPC 1A.

Actions exposees :

- `health` ;
- `manage_overview` -> `public.get_admin_account_overview(...)` ;
- `radar_overview` -> `public.get_admin_radar_overview(...)`.

Garanties :

- auth interne uniquement par `ADMIN_DASHBOARD_INTERNAL_API_TOKEN` ;
- POST only pour les actions ;
- service-role utilise cote Edge pour appeler les RPC ;
- aucune lecture directe des tables legacy ;
- aucun acces `anon`, `authenticated`, JWT client ou JWT admin dans ce patch ;
- aucune mutation settings, lifecycle, device, Source Quality Control ou status
  dropdown ;
- aucun changement UI Codex / `boost-ai-frontend`.

Relation avec les checkpoints precedents :

- DF-1A fournit les RPC service-role-only et le contrat no-leak ;
- DF-1B-prep documente le mapping Settings Registry et l'ordre de migration ;
- DF-1B cree la couche API interne qui pourra alimenter plus tard des vues admin
  separees cote UI, pas seulement une page unique `/instagram-dashboard`.

Vues admin futures a prevoir progressivement : Admin Manage, Admin Radar /
Server Check, Account Detail, Settings / Growth Settings, Devices / Phones,
Activity Log, Target Accounts / CT, DM Templates, Credentials / Dashboard
Actions.

Suite possible :

1. Validation operateur du patch local.
2. GO explicite deploy + secret remote + smoke HTTP read-only.
3. Plus tard seulement : consommation UI Codex de `admin-dashboard`.
4. Les settings, device controls, client dashboard et mutations restent hors
   scope tant que les APIs de domaine et audits ne sont pas prets.

Remote deploy/smoke 2026-05-28 :

- `admin-dashboard` deployed on project `zgafnshkjywfltxgbtzg`;
- remote secret `ADMIN_DASHBOARD_INTERNAL_API_TOKEN` configured without
  recording or displaying its value;
- smoke HTTP covered missing auth, bad token, `health`, `manage_overview`,
  `radar_overview`, unsupported action and clamp validation;
- remote responses confirmed no-leak. No UI, migration, settings mutation,
  device control or backend publish was executed.

## 16. Credential Secure Pipeline V1 Foundation

Backend Patch 1 adds the schema/RPC foundation for a single secure Instagram
credential pipeline, without activating frontend flows.

Migration:

- `supabase/migrations/20260529155500_credential_secure_pipeline_v1_foundation.sql`.

Scope:

- extends existing `account_credentials` with safe current-state metadata fields
  (`last_updated_at`, actor/source fields and `metadata_safe`);
- extends existing `account_dashboard_actions` with safe actor fields and
  `metadata_safe`;
- creates `credential_update_requests` for one-time update-link metadata,
  storing only `token_hash`, never raw tokens;
- adds service-role RPC skeletons:
  `record_instagram_credential_ingestion`,
  `create_credential_dashboard_action`,
  `create_credential_update_request_metadata`.

No-leak rules:

- no password, raw token, raw request body, `secret_ref`, Vault payload, raw
  logs/XML, screenshot path or device internals in `metadata_safe`;
- RPC returns are safe and do not return `secret_ref` or `token_hash`;
- RLS remains conservative: no direct `anon` or `authenticated` table access;
  service-role/internal backend only.

Current status:

- schema/skeleton only;
- no Next.js frontend activation;
- no Add Profile wiring change yet;
- no Request password update UI;
- no legacy `ig_account_settings.password` read, migration or deletion.

Future wiring plan:

- Add Profile should call a backend credential ingestion path that writes the
  secret to Vault and records safe metadata in `account_credentials`, then stop
  writing `ig_account_settings.password`.
- Password update links should be generated by a future Edge/API, store only
  `token_hash` in `credential_update_requests`, and submit the new password as
  write-only material through the credentials API.

## 17. Credential Secure Pipeline Patch 2A

Backend Patch 2A adds the backend-only Add Profile credential orchestration on
the existing `instagram-credentials` Edge Function.

Scope:

- new internal action `submit_add_profile_credentials`;
- internal bearer token only, no client/browser token path;
- `instagram-credentials` must stay deployed with `--no-verify-jwt`, because the
  internal token is opaque and must be checked inside the function;
- HTTP smokes use a temporary `.env.smoke.local` whose
  `INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN` must match the remote Edge secret,
  then delete that file after the smoke;
- validates the `ig_accounts` row before credential ingestion;
- accepts the password only as a write-only POST body field;
- rejects missing account rows and username mismatches before any Vault write
  when `ig_accounts.username` is available;
- reuses the existing Supabase Vault writer and
  `rotate_instagram_account_credentials`;
- updates the rotation RPC metadata path to populate `source='add_profile'`,
  `metadata_safe` and safe actor fields when this internal action is used;
- stores the resulting `account_credentials` row as `status='active'`, matching
  the Python provisioner lookup contract;
- attempts safe status sync to `credentials_submitted` / `login_pending` /
  `verification_pending` only when a `client_instagram_accounts` row exists;
- returns only safe fields including `credentials_status='active'`,
  `credentials_version`, `reauth_required`, `next_action` and
  `password_status='write_only'`.

Patch 2A deliberately does not modify `boost-ai-frontend`, does not stop the
legacy Add Profile route yet, does not write or migrate
`ig_account_settings.password`, does not activate Request Password Update,
does not create secure links/client token pages, and does not run devices,
provisioner, runner, sender, follow or outreach flows.

Patch 2B frontend contract:

- server-side `accounts/create` should create the account, then call
  `instagram-credentials` with `action='submit_add_profile_credentials'`;
- once that call succeeds, new Add Profile flows must stop persisting the
  password in `ig_account_settings.password`;
- the frontend/API response must keep the password write-only and must not expose
  secret references or Vault details.

Retry/orphan-secret status:

- `external_request_id` and request ids are safe metadata only in Patch 2A; they
  do not yet provide full idempotent replay protection.
- A duplicate successful submit rotates credentials to the next version and
  supersedes the previous active metadata.
- If Vault write succeeds but metadata rotation fails, the Edge Function returns
  a safe `credentials_metadata_write_failed` error. Patch 2C-1 adds cleanup /
  revoke foundations to reduce smoke and retry debt; strict end-to-end
  idempotency is still future work.

## 18. Credential Secure Pipeline Patch 2C-1

Patch 2C-1 adds backend-only cleanup/revoke foundations required before running
an authenticated full Add Profile production smoke:

- `revoke_instagram_credentials_vault_secret(...)` is service-role only, accepts
  only `supabase_vault://{uuid}`, and neutralizes the Vault value via
  `vault.update_secret(...)`. The current remote Vault API exposes
  `create_secret` and `update_secret`, not physical delete, so cleanup is
  neutralization rather than deletion.
- `revoke_instagram_account_credentials(...)` is service-role only, marks
  Instagram credentials `revoked`, sets safe revoke metadata and attempts Vault
  neutralization for linked refs. It is idempotent for already revoked or
  missing credentials.
- `cleanup_instagram_smoke_account(...)` is service-role only and intentionally
  narrow: username must match `smoke_*` and the request id must match related
  safe metadata before it removes smoke account/settings/filters/credential
  rows, dashboard actions, credential update requests and smoke client links.

All responses are safe counts/status booleans only. They never return password,
token, Authorization header, service-role key, full `secret_ref`, Vault id,
secret value, raw request body, raw metadata, device ids, XML, screenshots or raw
logs.

Patch 2C-1 does not change frontend code, does not run full Add Profile UI smoke,
does not launch device/provisioner/login, does not activate Request Password
Update, does not create secure links, and does not migrate legacy
`ig_account_settings.password`.

Account lifecycle vs credential cleanup:

- Archive does not revoke credentials. It may later suspend campaigns,
  provisioning and runtime, but restore must remain simple.
- Trash during retention does not revoke credentials by default because the
  account remains restorable.
- Restore must not fail because credentials were neutralized automatically by
  archive/trash.
- Permanent delete after retention may call credential cleanup/revoke and Vault
  neutralization.
- Explicit credential revoke is a separate operation from account deletion.
- Smoke cleanup, failed ingestion cleanup, security revoke and permanent delete
  cleanup are allowed only through strict backend guards.

Allowed cleanup reasons:

- `smoke_cleanup`
- `failed_ingestion_cleanup`
- `explicit_credential_revoke`
- `security_revoke`
- `permanent_account_delete`

Reasons that must not trigger automatic credential cleanup:

- `archive`
- `trash`
- `trash_pending_retention`
- `pause`
- `paused`
- `cancelled_without_final_delete`
- `archived_pending_retention`

Patch 2C-1 SQL guards reject reasons outside the allowed cleanup list. No route
or automation should call these helpers for archive, trash, scheduled trash,
restore or any restorable lifecycle transition.

Restoration safety:

- archived/trashed credentials stay intact unless explicitly revoked;
- restore later checks `credentials_status` before runtime resumes;
- if credentials were explicitly revoked while trashed, restore should ask for a
  credential update before provisioning/runtime.

Future lifecycle sync must coordinate admin dashboard, BotApp/backend, future
client dashboard and automation/runtime. Recommended future fields:
`source_surface`, `actor_type`, `actor_id`, `reason`, `lifecycle_status`,
`archived_at`, `trashed_at`, `scheduled_trash_at`, `scheduled_delete_at` /
`purge_after`, `permanently_deleted_at`, `sync_status`, `botapp_sync_status`,
`client_dashboard_sync_status`, `admin_dashboard_sync_status`,
`audit_event_id`, `credential_cleanup_status`. Patch 2C-1 does not implement
this multi-surface sync.

## 19. Credential Secure Pipeline Patch 2C-2

Patch 2C-2 is the production checkpoint for the authenticated Add Profile E2E
path after fixing the Vercel Production credential-token environment variable.

Validated runtime alignment:

- Vercel Production
  `INSTAGRAM_CREDENTIALS_INTERNAL_API_TOKEN` now matches the token accepted by
  Supabase Edge.
- Safe Vercel Production runtime fingerprint: `present=true`, `len=64`,
  `sha12=4c74d75cb9fd`.
- Edge reprobe to `instagram-credentials` with the Vercel Production token no
  longer returned `401 unauthorized`; the fake account probe authenticated
  internally and failed at the expected business layer with `account_not_found`.

Validated Add Profile production UI flow:

- authenticated admin UI on `/instagram-dashboard`;
- Add Profile wizard completed through Create Profile confirmation;
- `ig_accounts` created;
- `ig_account_settings` created;
- `ig_account_filters` created;
- `ig_account_settings.password = ''`;
- `submit_add_profile_credentials` succeeded;
- `account_credentials` row created;
- `account_credentials.status = 'active'`;
- `ig_accounts.status = 'active'`;
- no `credentials_ingestion_failed` UI result.

Cleanup:

- `cleanup_instagram_smoke_account(...)` succeeded for the smoke account using
  the matching safe request id;
- cleanup removed the smoke account, settings, filters, credential metadata and
  related dashboard action, and neutralized the linked Vault secret;
- post-cleanup counts for smoke accounts/settings/filters/credentials were all
  zero.

Patch 2C-2 was reprobe/smoke/checkpoint only: no runtime patch, no Edge Function
patch, no dashboard UI patch and no migration change. The checkpoint remains
no-leak: no token, Authorization header, service-role key, password, full
`secret_ref`, Vault id, cookies/session, raw logs, XML or screenshot path was
recorded.

## 20. Credential Secure Pipeline Patch 2C-3

Patch 2C-3 hardens Add Profile transaction boundaries, compensation and minimal
idempotency.

Current diagnosis before Patch 2C-3:

- `accounts/create` inserted `ig_accounts.status='active'` before settings,
  filters and credential ingestion completed;
- settings and filters were not created inside a database transaction with the
  account row;
- credential ingestion failure already marked `ig_accounts.status` as
  `support_required`, but earlier settings/filter failures could leave partial
  rows or raw DB error messages;
- no unique username invariant existed on `ig_accounts`, so a double-submit or
  retry could create duplicate account rows for the same normalized username.

Patch behavior:

- new Add Profile accounts start as `support_required`;
- settings start with `account_status='support_required'` and `password=''`;
- settings/filter failures before credential ingestion trigger targeted
  compensation by deleting only the newly created account id. Cascading foreign
  keys clean up just-created settings/filters rows;
- credential ingestion failure keeps the account/settings in `support_required`,
  creates a best-effort safe `review_credentials` dashboard action, and returns
  a safe UI error;
- credentials success is accepted only when the Edge response reports active
  credentials;
- only after active credentials are confirmed does the route finalize
  `ig_accounts.status='active'` and `ig_account_settings.account_status='active'`;
- if finalization fails after credentials are active, the route reports a safe
  failure and leaves the account in `support_required`.

Idempotency:

- migration `20260529220012_patch2c3_add_profile_username_unique.sql` adds
  `ig_accounts_username_lower_unique` on `lower(btrim(username))` for non-empty
  usernames;
- duplicate normalized usernames map to safe `account_already_exists` and do not
  create a second account/credential path.

Patch 2C-3 does not change worker Python, login/provisioner runtime, runner,
the `instagram-credentials` Edge Function, archive/trash/restore semantics,
client dashboard, or global lifecycle cleanup policy. No cleanup/revoke is
triggered for archived or trashed accounts.

No-leak rule: route errors and support metadata must never include passwords,
raw request bodies, tokens, Authorization headers, service-role keys, full
`secret_ref`, Vault ids/values, cookies/session, XML, screenshots or raw logs.

Production validation checkpoint (2026-05-29): migration applied on linked
Supabase; production `accounts/create` happy path, duplicate username guard and
`cleanup_instagram_smoke_account` cleanup verified on smoke username
`smoke_add_profile_2c3_e2e` without leaking secrets in logs or docs.

## 21. Credential Secure Pipeline Patch 2C-4

Patch 2C-4 adds safe Add Profile audit and dashboard action reconciliation
without changing Instagram runtime, login/provisioner, runner, Edge credential
ingestion, lifecycle archive/trash/delete, or global cleanup/revoke behavior.

Audit contract:

- `add_profile_audit_events` records Add Profile outcomes with safe username,
  optional account id, truncated request ids, `source_surface='admin_dashboard'`,
  `operation='add_profile'`, actor id/type when available, result status and
  safe failure reason;
- result status mapping is `success`, `failed`, `compensated`, and `duplicate`;
- compensated settings/filter failures keep audit after the account row is
  deleted via `account_id on delete set null`;
- audit metadata is `metadata_safe` only and uses the existing forbidden-key
  guard.

Dashboard action mapping:

- success resolves open `submit_instagram_credentials` / `review_credentials`
  actions after credentials and account/settings are active;
- credential ingestion failure creates or updates a deduped admin
  `review_credentials` action with `pending`, `warning`,
  `requires_client_action=false`, `blocking_campaign=true`;
- settings/filter failure with successful compensation is audit-only;
- settings/filter failure with failed compensation creates an admin manual-review
  action;
- duplicate username records audit `duplicate` and returns
  `account_already_exists` without creating an action.

Frontend scope is minimal: the Credentials Actions page can merge open
`account_dashboard_actions` with existing derived read-only signals. It renders
only safe columns and keeps mutations disabled.

No-leak rule: audit rows, dashboard action metadata, UI and docs must never
contain passwords, tokens, Authorization headers, service-role keys,
cookies/sessions, full `secret_ref`, Vault UUIDs/payloads, raw request bodies,
raw logs, XML or screenshot paths.

## 22. Credential Secure Pipeline Patch 2C-5

Patch 2C-5 adds safe public profile metadata for Add Profile without changing
worker Python, runner, login/provisioner, Edge credential ingestion, device/app
start, lifecycle archive/trash/delete, or global cleanup/revoke behavior.

Schema:

- `ig_accounts` now has safe public profile fields:
  `username_verification_status`, `username_verified_at`,
  `username_verification_reason`, `instagram_user_id`,
  `external_profile_id`, `is_private`, `is_verified`, `followers_count`,
  `avatar_url`, `avatar_checked_at`, and `public_profile_metadata`;
- `avatar_url` is optional and must be HTTP(S), bounded, and free of
  token/signature/secret/service-role/Vault markers;
- `public_profile_metadata` is guarded by the shared safe-metadata denylist.

Status mapping:

- `verified`: safe source confirmed username;
- `not_found`: safe source clearly confirmed missing username;
- `username_changed`: safe source observed canonical username change/redirect;
- `private_or_limited` / `inaccessible`: public access is limited;
- `verification_unavailable`: no stable public lookup provider is configured;
- `provider_error`: provider failed without a clear verdict;
- `invalid_format`: local syntax check failed;
- `pending` / `unknown`: no final verdict.

V1 implementation:

- Add Profile performs local username normalization and syntax validation before
  account creation;
- invalid syntax returns safe `username_verification_failed` and creates no
  account;
- no public Instagram scraping is performed in Patch 2C-5. New accounts are
  stored as `verification_unavailable/public_lookup_not_configured`, which does
  not block credentials or active finalization;
- no `review_username` action is created for normal provider-unavailable state;
- dashboard views render safe avatar/status fields only and never raw metadata.

Future roadmap — Target Account Quality / CT Filtering Engine:

- CT introuvable;
- username changed;
- avatar missing/suspicious where useful;
- followers below a configured threshold, e.g. `<500`;
- verified / blue badge;
- private or non-exploitable;
- no posts;
- FBR `<= 8%` after at least `100` follows;
- no followable profiles after `X` scrolls;
- archive/suppression CT synchronized frontend/backend.

These CT filtering rules, mass avatar enrichment, comments/AI ranking, MCP
integration and automatic target archive/delete are out of scope for Patch 2C-5.

## 23. Credential Secure Pipeline Patch 2C-6

Patch 2C-6 introduces a safe Instagram public profile lookup provider contract
for Add Profile and future Target Account / CT Quality checks. The provider is
server-side only and must never use Instagram passwords, cookies, sessions,
device/app startup, ADB, uiautomator, worker runtime, or aggressive scraping.

Provider modes:

- `disabled` / not configured: no external call, returns
  `provider_not_configured`, and Add Profile keeps the Patch 2C-5 fail-open
  behavior;
- `mock`: local/test-only deterministic statuses for `found`, `not_found`,
  `unavailable`, avatar, follower count and privacy/verified flags;
- `http`: opt-in server-side endpoint via env. It has a short timeout and stores
  only sanitized fields, never raw responses, headers, cookies, tokens, HTML, IP
  or session details.

Add Profile behavior:

- invalid local syntax still returns `username_verification_failed` before any
  lookup;
- `found` stores `username_verification_status='verified'`, safe canonical
  username metadata, avatar URL, follower count, privacy/verified flags and
  public ids when available, then continues credential ingestion;
- clear `not_found` returns a safe `username_not_found` error before account
  creation;
- `provider_not_configured`, `unavailable`, `rate_limited` and `provider_error`
  remain fail-open for admin Add Profile and persist safe status/reason metadata.

Future CT reuse:

- verify whether a CT exists;
- detect canonical username changes;
- reuse avatar, `followers_count`, `is_verified` and `is_private`;
- support future followers threshold checks such as `<500`;
- feed later FBR and quality rules without implementing CT filtering in 2C-6.

Full Add Profile direction:

- future Patch 2B keeps the frontend password field write-only;
- `accounts/create` creates account/settings/filters/status, then calls
  `submit_add_profile_credentials`;
- new Add Profile flows stop writing passwords to `ig_account_settings.password`;
- successful responses expose only password/credential/status summaries safe for
  dashboard use;
- a later backend transaction/RPC can consolidate account creation, settings,
  filters, ownership/status and credential orchestration, but must keep the
  existing single Vault + `account_credentials` pipeline.
