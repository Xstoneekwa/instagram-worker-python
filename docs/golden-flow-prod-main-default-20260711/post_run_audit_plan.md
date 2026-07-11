# Post-run audit plan — Prochain run naturel

**Objectif :** valider en production que Golden Flow main default + CT 30/4 se comportent comme attendu, en comparant au run Golden evidence `fc65bace`.

**Règle :** audit **post-run naturel uniquement** — pas de run manuel déclenché pour ce plan.

---

## Comptes cibles

| Compte | account_id | Priorité |
|--------|------------|----------|
| `mythyl_fitness` | `0d299d1e-46ee-49d2-8a84-4f928f2bb182` | P0 — row repairée 30/4 |
| `i_m_your_traker` | `83de9cc9-5c37-42d1-9edc-c924352b17b1` | P0 — row repairée 30/4 |

**Baseline Golden :** run `fc65bace` — completed, ~91.35s/follow, CT 30/4, 0 switch, like + return CT nominal.

**Références prod récentes (avant restauration) :**

| run_id | Compte | Note |
|--------|--------|------|
| `497d0c4a` | mythyl | completed mais default 2/3, +like time |
| `4333dbe2` | mythyl | failed, search drift |

---

## Checklist par follow complet

Pour chaque follow nominal réussi, vérifier dans les logs dispatcher/worker :

### 1. CT rotation settings (début session / par target)

- [ ] `follow_source_rotation_settings_loaded` avec `settings_source=account`
- [ ] `max_follows_per_target_per_run=30`
- [ ] `max_targets_per_run=4`
- [ ] **absent** : `follow_source_rotation_contract_missing_account_settings`
- [ ] **absent** : `settings_source=default` ou caps `2/3`

### 2. Open → follow

- [ ] `followers_list_open_started` / open profile nominal
- [ ] `perform_follow_safe` / follow verify success
- [ ] **absent nominal** : `followers_entry_search_surface_detected_before_profile_entry` (sauf vrai fallback documenté)

### 3. Follow → verify

- [ ] `visual_follow_post_action_reconcile` ou équivalent success
- [ ] pas de re-tap follow sur faux positif

### 4. Mute

- [ ] `run_mute_engine_v2` / mute toggles completed
- [ ] pas de boucle dismiss anormale

### 5. Like

- [ ] like nominal rapide (grid prep sans strict proof)
- [ ] **absent nominal** : `like_top_left_xml_probe_*`
- [ ] **absent nominal** : `post_follow_like_strict_post_grid_proof_completed`
- [ ] strict proof autorisé **uniquement** si événement d'échec nominal précède (mode fallback)

### 6. Return CT

- [ ] `post_follow_return_ct_visual_confirmed` ou `return_ct_fast_proof_reused`
- [ ] **absent nominal** (mode off) : `post_follow_return_ct_accept_stale_candidate_action_bar_own_unified`
- [ ] retour liste CT confirmée avant next candidate

### 7. CT rotation behavior

- [ ] **pas** de switch tous les 2 follows
- [ ] rotation cohérente avec budget 30/target et max 4 targets/run
- [ ] `target_budget_reached` seulement après ~30 follows sur target (ou stop session métier)

### 8. Search Recovery (fallback only)

- [ ] **absent nominal** : `followers_entry_search_surface_detected_before_profile_entry` en pre-entry
- [ ] présent **uniquement** si log `followers_entry_search_surface_recovery_fallback_succeeded` ou phase `*_fallback`
- [ ] `return_to_followers_list` : `reopen_from_source_profile` avant `reopen_after_search_profile_recovery`

---

## Métriques à comparer vs Golden `fc65bace`

| Métrique | Golden cible | Alerte si |
|----------|--------------|-----------|
| Temps moyen / follow complet | ~91s | > +20% sans cause documentée |
| Strict grid probes / follow | 0 nominal | > 0 nominal |
| Search recovery pre-entry / session | 0 nominal | > 0 nominal |
| CT switch frequency | 0 @ 2 follows | switch @ 2 follows |
| `settings_source` | `account` | `default` |
| Follows avant rotation target | ~30 | ~2 |

---

## Procédure audit (lecture seule)

1. Identifier le `run_id` naturel mythyl ou tracker (scheduler / auto-restart / play — pas manuel forcé).
2. Extraire logs : `/Users/admin/phonefarm-runtime/logs/run-control-dispatcher/dispatcher.log` + logs run account_session.
3. Filtrer par `run_id`, `account_username`, events listés ci-dessus.
4. Remplir checklist PASS/FAIL par section.
5. Si FAIL sur invariant verrouillé → ouvrir ticket régression, **pas de patch sans GO Liam**.

### Commandes utiles (grep logs — sans run)

```bash
RUN_ID="<run_id>"
grep "$RUN_ID" /Users/admin/phonefarm-runtime/logs/run-control-dispatcher/dispatcher.log | \
  rg 'follow_source_rotation_settings_loaded|settings_source|like_top_left_xml_probe|search_surface_detected|return_ct_accept_stale|return_ct_fast_proof_reused|target_budget_reached'
```

### Audit CT dry-run pré-run (optionnel)

```bash
PYTHONPATH=. python3 scripts/audit_follow_source_rotation_contract.py \
  --account-id 0d299d1e-46ee-49d2-8a84-4f928f2bb182 --account-username mythyl_fitness --json
```

Attendu : `contract_ok=true`.

---

## Verdict post-run attendu

| Verdict | Condition |
|---------|-----------|
| `POST_RUN_GOLDEN_ALIGNED` | Tous les invariants PASS, métriques dans tolérance |
| `POST_RUN_CT_CONTRACT_OK` | settings_source=account, 30/4 confirmé en logs |
| `POST_RUN_FALLBACK_ONLY_OK` | Search Recovery / strict proof uniquement sur vrais fallbacks |
| `POST_RUN_REGRESSION` | Tout invariant FAIL → STOP + GO Liam avant patch |
