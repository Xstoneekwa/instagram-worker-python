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
- Entry 2E-5N : Cas F old logged-in account recovery. Instagram peut ouvrir sur
  le home/feed ou le profil d'un ancien compte encore connecte. Le probe expose
  `active_account_home`, `active_account_profile`, `account_switcher_sheet`,
  `add_account_sheet` et `actual_logged_in_username`. Si le compte actif est le
  compte attendu, le flow finalise `connected` sans recovery. Si le compte actif
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
