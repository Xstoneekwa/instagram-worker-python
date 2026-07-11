# Invariants verrouillés — Golden Flow Prod Main Default

**Checkpoint :** 2026-07-11 @ commit `0a6f62d`  
**Modification de tout invariant ci-dessous : GO Liam explicite requis.**

---

## 1. Flow principal Golden

Le chemin nominal d'un follow complet doit rester :

```
candidate selected → follow → mute → post open → like verify → return CT → next candidate
```

**Règle :** les ajouts post-Golden ne doivent **pas** remplacer ce chemin. Ils restent fallback/secours documenté.

| Bloc | Invariant nominal | Fallback autorisé |
|------|-------------------|-------------------|
| Open candidate profile | Golden followers entry / open profile | Search Recovery après échec Golden |
| Follow tap/wrapper | `perform_follow_safe` + wrappers prod | probes extra uniquement après échec |
| Follow verification | reconcile multi-signal | inchangé |
| Mute entry + toggles | `run_mute_engine_v2` | legacy mute sheet si échec V2 |
| Like | chemin nominal rapide | strict grid proof si `fallback`/`always` et échec nominal |
| Return CT | CT verify strict + fast proof reuse | stale action bar si mode `fallback`/`always` |
| Return followers list | `verify_profile` → reopen Golden d'abord | Search Recovery après échec reopen |

---

## 2. Like strict grid proof

- **Default :** `POST_FOLLOW_LIKE_STRICT_GRID_PROOF_MODE=fallback`
- **Invariant :** les probes `like_top_left_xml_probe_*` et `post_follow_like_strict_post_grid_proof_completed` **ne doivent pas** apparaître sur un chemin nominal réussi.
- **Autorisé :** strict proof uniquement après échec nominal (dynamic row / variance insuffisante) ou si mode `always`.

---

## 3. Search Recovery

- **Default :** `FOLLOWERS_ENTRY_SEARCH_SURFACE_RECOVERY_MODE=fallback`
- **Invariant :** `_followers_entry_maybe_recover_ct_profile_from_search_surface` **ne doit pas** être appelé en pré-entry nominal quand `profile_verified=True`.
- **Invariant :** dans `return_to_followers_list`, Golden reopen (`verify_profile` → `open_followers_list_from_profile`) **avant** Search Recovery.
- **Autorisé :** Search Recovery en secours documenté (`fallback` après échec Golden, `always` pour pre-entry explicite).

---

## 4. Return CT stale action bar

- **Default :** `POST_FOLLOW_RETURN_CT_STALE_ACTION_BAR_MODE=off`
- **Invariant :** `post_follow_return_ct_accept_stale_candidate_action_bar_own_unified` **ne doit pas** être le chemin nominal de confirmation CT.
- **Autorisé :** stale action bar uniquement après échec CT strict si mode `fallback` ou `always`.

---

## 5. CT rotation contract

| Champ | Valeur prod attendue |
|-------|---------------------|
| `settings_source` | `account` |
| `max_follows_per_target_per_run` | `30` |
| `max_targets_per_run` | `4` |

**Invariants :**

- Aucun compte prod/scheduled ne doit tomber silencieusement sur `default 2/3`.
- Le log `follow_source_rotation_contract_missing_account_settings` doit rester actif (Phase 1 guard).
- `_run_follow_target_rotation` **ne doit pas être modifié** sans GO Liam.
- Provisioning `ready` crée idempotemment la row 30/4 si absente.
- Repair existant (valeurs incorrectes) = GO Liam + `repair_go=True` uniquement.

---

## 6. Couches prod modernes (ne pas casser)

Ces composants restent la base — Golden s'y adapte, ne les remplace pas :

- `scheduled_session_preflight_runner`
- keyguard / preflight dispatcher
- `account_run_request_consumer` / device leases
- `account_session_orchestrator` wrappers (welcome, reporting, cancel boundaries)
- BotApp runtime gate

---

## 7. Interdictions explicites

- Pas de rollback global vers tag Golden brut
- Pas de cherry-pick Golden → prod
- Pas de suppression de Search Recovery
- Pas de mutation DB silencieuse pendant un `account_session` run
- Pas de modification `_run_follow_target_rotation` sans GO Liam
- Pas de changement schedules/caps/packages dans le cadre de ce checkpoint

---

## 8. Signaux d'alerte post-run (régression)

Si un run prod affiche l'un de ces signaux sur le chemin nominal, traiter comme régression :

- `settings_source=default` ou `account_with_fallback` avec caps ≠ 30/4
- `like_top_left_xml_probe_*` sur follow nominal réussi
- `followers_entry_search_surface_detected_before_profile_entry` sans échec Golden préalable
- `post_follow_return_ct_accept_stale_candidate_action_bar_own_unified` avec mode `off`
- switch CT tous les 2 follows (symptôme default 2/3)
- temps/follow >> +20% vs Golden `fc65bace` sans cause métier documentée
