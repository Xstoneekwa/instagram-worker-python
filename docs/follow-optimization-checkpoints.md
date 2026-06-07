# Follow optimization checkpoints

Ce registre fige les decisions de la sequence d'optimisation
follow / mute / like / return CT / accounting. Il ne decrit aucun patch runtime
nouveau.

## Checkpoint: CT Checkpoint / Fast-Skip V1 runtime

- Scope: ledger runtime-only par CT/source dans `runner.py`, avec fenêtre visible,
  candidats vus, rejetés/private, followés, `last_scroll_index`, raisons stables
  et champs proches de la future V2 DB.
- Objectif: accélérer la sélection du candidat suivant dans une même session et
  rendre les décisions observables sans persistance DB.
- Validation 4/2/2: runs contrôlés `51720464`, `3521ba8f`, `34ae4947` et suites
  suivantes. Les runs ont validé follow/mute/like ou skip métier, return CT,
  private skip fast path et checkpoint updates/reuse.
- Limite V1: runtime-only. Un CT déjà exploité dans un run précédent (ex.
  `pumptracktour`) n'est pas connu au démarrage suivant ; la vraie validation
  cross-run/dense attend la V2 DB persistée.

Observabilité attendue:

- `follow_target_checkpoint_created`
- `follow_target_checkpoint_updated`
- `follow_target_checkpoint_reused`
- `follow_target_fast_skip_started`
- `follow_target_fast_skip_candidate_seen`
- `follow_target_fast_skip_candidate_rejected`
- `follow_target_scroll_resume_planned`
- `follow_target_scroll_resume_applied`
- `followers_post_return_list_revalidation_skipped_checkpoint_fresh`
- `followers_post_return_picker_refresh_skipped_checkpoint_fresh`

Sécurité:

- aucun bypass private gate, social memory, screen guard, follow proof,
  follow verify;
- aucun fast-skip de candidat inconnu;
- aucun private follow;
- fallback revalidation lourde + screenshot conservé si preuve manquante,
  mismatch CT/source, scroll mismatch, return method ambiguë, surface non
  committed ou recovery incertaine.

## Checkpoint: No Posts early visual gate

- Problème observé: le fallback visuel No Posts précoce coûtait environ 5.7 à
  6.1 secondes par profil normal quand il tournait systématiquement après tier1
  négatif.
- Correction: gate strict avant `visual_profile_has_no_posts(...,
  include_visual_fallback=True)`. Sur profil normal avec surface profil + grille
  confirmées et sans hint No Posts, log
  `visual_profile_no_posts_early_visual_check_skipped` et poursuite du flow
  normal.
- Le check précoce peut tourner seulement avec signaux forts ou ambigus :
  grid/tabs absents ou incohérents, hints No Posts faibles, surface profil sans
  cellules candidates visibles, ou tier1 inconclusive avec suspicion No Posts.
- Si No Posts est prouvé: skip Like rapide, follow+mute restent valides, aucun
  faux `post_likes_persisted`.
- Si ambigu/faux: fallback existant conservé ; pas de faux completed ni faux
  skip Like.

## Future: CT Checkpoint V2 DB persisted

- Hors scope du commit V1.
- Objectifs futurs: dashboard web, future BotApp, multi-admin, audit/reset,
  stale expiration, synchronisation multi-device/multi-clone et historique des
  candidats par CT/source.
- Contraintes: ne pas exposer secrets, sessions, screenshots bruts ou XML brut ;
  mutations admin auditées ; reset contrôlé ; compatibilité avec les champs V1
  (`account_id`, `source_target_id`, `source_username`, `last_run_id`,
  `last_scroll_index`, candidates seen/rejected/private/followed,
  `checkpoint_reason`, `checkpoint_status`, `stale_after`, timestamps).

## Checkpoint: return CT detection reuse

- Commit: `84c037f`
- Message: `perf(follow): reuse post-back CT list detection`
- Tag: `checkpoint-follow-return-ct-post-back-det-reuse-20260606`
- Scope: reutiliser la detection de liste CT deja disponible apres le
  `compact_safe_back_only`, quand elle prouve la surface followers du CT.

Validation:

- post-back CT detect -> success: environ `3.41s` -> `0.29s`
- gain cible: environ `3.1s`
- return CT total: environ `14.18s` -> `13.30s`
- flow complet OK: follow, mute, like, return CT
- recovery non utilisee sur le run de validation
- fallback conserve si la detection reutilisable est absente, stale ou
  insuffisante.

## Checkpoint: accounting/finalization instrumentation

- Commit: `fa60fae`
- Message: `chore(follow): clarify Supabase persist timing instrumentation`
- Scope: instrumentation des appels Supabase accounting/finalization via
  `supabase_persist_step_started`, `supabase_persist_step_completed` et
  `supabase_persist_step_failed`.

Decision validee:

- instrumentation saine;
- `ok=true` par defaut quand l'appel termine sans exception;
- `ok=false` seulement en cas d'exception reelle ou de retour explicite
  `{"ok": false}`;
- fonctions Supabase success void/None, comme `insert_action_log` et
  `update_run_status`, correctement affichees en `ok=true`;
- no-leak OK sur les champs d'instrumentation;
- aucun changement UI/runtime;
- aucun changement d'ordre des writes;
- aucune async/parallellisation;
- aucun changement de run completion.

Run de mesure post-commit:

- Run: `8ba801d6-81b6-4eb0-a5a3-487fc21d91c7`
- Request: `7e7626b1-8664-448a-a5bc-1104e170c66f`
- Log: `runs/follow_1of1_i_m_your_traker_20260606T170220Z.log`
- Resultat fonctionnel: `total=1`, `success=1`, `failed=0`
- CT: `reveaustral`
- Candidat: `ventouxtravelcar`
- Follow: `follow_success_verified=true`, `follow_state_after=following`
- Mute: `muted_posts=true`, `muted_stories=true`
- Like: `liked_count=1/1`, verification OK
- Return CT: `compact_safe_back_then_list`, detection reuse OK
- Recovery: non utilisee
- Finalisation: `run_status_updated` en `completed`

Instrumentation observee:

- `supabase_persist_step_started`: `28`
- `supabase_persist_step_completed`: `28`
- `supabase_persist_step_failed`: `0`
- `insert_action_log`: tous `ok=true`
- `update_run_status`: `ok=true`, `duration_ms=273.75`
- aucun faux `ok=false` observe sur succes void/None.

## Decision: accounting/finalization

Pas de patch comportemental accounting maintenant.

Timing mesure:

- `post_follow_return_ct_success` -> `run_status_updated`: environ
  `12.6s` a `13.6s` selon les runs mesures, `12,643.85ms` sur
  `8ba801d6`.
- `post_follow_return_ct_success` -> `session_cleanup_apps_closed`:
  `12,965.18ms` sur `8ba801d6`.

Top couts observes:

- `_flush_follow_action_logs_to_supabase`: environ `4.2s` pour 14 inserts
  sequentiels (`4191.29ms` sur `8ba801d6`)
- `_persist_verified_follow_success_to_supabase`: environ `3.2s`
  (`3256.75ms`)
- `social_memory_updated`: environ `2.3s` (`2318.98ms`)
- `record_post_like_interaction_success`: environ `1.2s` (`1247.28ms`)
- `record_follow_source_follow_success`: environ `0.9s` (`936.45ms`)
- `unfollow_settings_loaded`: appels repetes, environ `2.0s` total
  (`1991.69ms`)
- `record_mute_interaction_success`: environ `0.8s` (`845.97ms`)
- `run_status_updated`: environ `0.27s` (`273.75ms`)

Raison de la decision:

- le gain potentiel existe, surtout sur les action logs sequentiels;
- batch / grouping / parallellisation touche l'ordre des writes, l'idempotence,
  l'accounting, le replay/debug des logs et la durabilite Supabase;
- tout changement comportemental accounting exige un design separe et des tests
  mock Supabase avant patch.

## Backlog: Accounting Finalization - Batched Action Log Flush

Objectif futur: reduire `_flush_follow_action_logs_to_supabase`, qui coute
environ `4.2s` sur 14 inserts sequentiels.

A faire plus tard:

- auditer le contrat exact des action logs;
- verifier si l'insertion batch Supabase est disponible et safe;
- garantir idempotence et deduplication;
- garantir ordre ou timestamps stables;
- conserver un fallback sequentiel si le batch est indisponible;
- maintenir le no-leak strict;
- ajouter des tests mock Supabase obligatoires:
  - batch success;
  - partial failure;
  - retry / idempotence;
  - no completed avant writes critiques;
  - fallback sequentiel si batch indisponible;
- lancer un run device seulement apres design et tests.

## Roadmap: other follow optimization candidates

### Post-Follow Like - Posts Count Fast Path

- Architecture faisable.
- Live non valide a cause du cas `no_numeric_stats_in_band`.
- Flags OFF par defaut.
- A reprendre plus tard avec validation dediee.

### Post-Follow Like - Fail-fast reveal when tabs RID absent and suggested overlay blocks grid

- Pattern reel observe: RID absent + `suggested_for_you=true` + absence de
  `tabs_bottom` autoritatif peut entrainer un reveal utile.
- Pas assez valide pour patcher maintenant.
- Ne pas appliquer si `tabs_bottom` est present mais ambigu / trop haut ou trop
  bas.
- Collecter plus de runs avant decision.
