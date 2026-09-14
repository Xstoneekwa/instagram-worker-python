# Terminalisation d'une reprise après intervention humaine

## Défaut et correction

Lorsqu'un `human_confirmed_resume` échouait, le dispatcher mettait à jour
l'ancien plan via `mark_resume_outcome` mais sautait `record_terminal_failure`
sur le run courant. Son plan restait `run_active`, sans lien vers le nouvel
incident. Une résolution rétablissait le statut administratif actif mais la
réconciliation ne pouvait pas réarmer ce plan. Un ancien plan invalidé, mis à
jour plus récemment, pouvait alors produire le diagnostic `resume_plan_missing`.

Le dispatcher conserve la déduplication et l'outcome de l'ancien run, puis
terminalise celui qui vient réellement d'échouer. La raison et les progrès
réconciliés passent par le store existant. Un échec de persistance émet
`resume_plan_terminal_reconciliation_required` sans autoriser de reprise.

La migration ajoute un cas borné à
`reconcile_stale_account_session_resume_plans_v1`. Pour un plan déjà bloqué,
elle exige le dernier run physique en échec, sa request terminale, le compte
exact, le plan source et l'autorisation consommée correspondants, un incident
actionnable lié et aucune exécution ni lease active. Elle lie l'incident et
la raison au plan et le remet en attente humaine, avec `restart_allowed=false`.
Les ambiguïtés restent bloquées ; aucun quota, checkpoint, run, request ou
autorisation historique n'est réécrit par cette réparation.

## Contrats préservés

La résolution et le tick naturel existants arment et consomment la nouvelle
autorisation. L'autorisation précédente reste consommée. La résolution humaine
ne prouve pas la disparition physique d'une restriction : le préflight conserve
zéro action métier jusqu'à validation du garde d'identité/restriction.

Le cas partial-safe ordinaire, les retries V4, l'arrêt manuel, le retour
administratif à `growth ready`, les pauses indépendantes, les fenêtres,
les quotas, le scheduler, le contrôleur V2 et Target Lifecycle sont conservés.
Il n'existe aucune branche propre à un compte, nom d'utilisateur ou date.

## Preuves

`scripts/test-human-resume-terminal-plan-v1.py` rejoue les migrations et les
dépendances de production dans PostgreSQL 17/PostgREST temporaires. Les tests
exercent la publication réelle du dispatcher, le store, les RPC de résolution,
le Backend inchangé et l'admission Worker. Les observations physiques sont
synthétiques ; aucun téléphone ni ADB n'est utilisé.

Les preuves externes conservent le fail-before/pass-after, l'idempotence,
la concurrence, les rejets d'identité/état, la non-réutilisation des autorisations,
les droits SQL, le catalogue limité à une fonction et le rollback SQL exact.
Les régressions des correctifs récents sont rejouées avec la nouvelle migration.

## Déploiement et rollback

Le candidat descend de l'actif exact `03c49917fca60ae9eb832d55fc143c3e6fc2c578`.
Approval 1, freeze externe, hook normal, commit et Approval 2 restent obligatoires.
La promotion utilise le contrôleur scellé corrigé V2, le gate zéro frais et la
transaction canonique ; l'ancien actif exact devient rollback. Les preuves
Worker et SQL doivent converger avant restauration du scheduler.

Le rollback SQL restaure la seule définition précédente sans effacer les
preuves ni remettre un plan réparé en état actif. Le Worker précédent peut
lire les états réparés. Les données physiques ne sont jamais prétendues validées.
Nettoyage limité aux clusters temporaires et caches reconstruisibles arrêtés ;
runtime, rollback, signatures, preuves et références parallèles sont conservés.
