# Golden Flow — Production Main Default Checkpoint

**Date :** 2026-07-11  
**Verdict checkpoint :** `GOLDEN_FLOW_PROD_CHECKPOINT_LOCKED`

---

## Release active

| Champ | Valeur |
|-------|--------|
| **Release label** | `b6fedca-plus-search-surface-recovery` |
| **Chemin release** | `/Users/admin/phonefarm-worker-releases/b6fedca-plus-search-surface-recovery` |
| **Symlink worker** | `/Users/admin/phonefarm-worker-current` → release ci-dessus |
| **Commit actif runtime** | `0a6f62d0452f34ae43e884497d496a116b06ff53` |
| **Phase 1** | `4f7f8730a1fc5b6b27987616acfe0bca4df3e809` — like nominal + log guard CT |
| **Phase 2** | `0a6f62d0452f34ae43e884497d496a116b06ff53` — Golden defaults + CT contract |
| **Branche** | `release/b6fedca-plus-search-surface-recovery` |

---

## Objectif

Golden Follow / Mute / Like / Return CT redevient le **flow principal par défaut** dans la base prod moderne, sans rollback global ni cherry-pick brut.

Les couches prod modernes restent en place :

- preflight / keyguard
- dispatcher moderne (`account_run_request_consumer`)
- `account_session` orchestration
- runtime control / BotApp gate
- welcome env
- scheduler / device leases
- summaries / reporting prod

---

## Différences avec Golden original (tag `golden-follow-mute-like-returnct-110s-20260615`)

| Sujet | Golden original | Prod restaurée (ce checkpoint) |
|-------|-----------------|--------------------------------|
| **Base runtime** | ligne Golden isolée | base `b6fedca` + preflight/keyguard/dispatcher |
| **Search Recovery** | absent | **fallback only** (`FOLLOWERS_ENTRY_SEARCH_SURFACE_RECOVERY_MODE=fallback`) |
| **Like strict grid proof** | absent du nominal | **fallback only** (`POST_FOLLOW_LIKE_STRICT_GRID_PROOF_MODE=fallback`) |
| **Return CT stale action bar** | strict CT verify | **off** par défaut ; fallback optionnel |
| **CT rotation** | account 30/4 attendu | contract explicite + provisioning + repair contrôlé |
| **Rotation engine** | P1b | `_run_follow_target_rotation` **inchangé** depuis restauration |

---

## Référence Golden (lecture seule)

| Artefact | Commit / ID |
|----------|-------------|
| Tag sanctuary | `golden-follow-mute-like-returnct-110s-20260615` (`86a01a6`) |
| Run evidence Golden | `fc65bace` — ~91s/follow, CT 30/4, 0 switch |
| Guard sanctuary | `docs/golden-flow/` (ne pas modifier) |

---

## Fichiers de ce checkpoint

| Fichier | Rôle |
|---------|------|
| [`invariants.md`](invariants.md) | Règles verrouillées anti-régression |
| [`locked_files_manifest.json`](locked_files_manifest.json) | Fichiers cœur + SHA256 @ `0a6f62d` |
| [`flags_and_defaults.md`](flags_and_defaults.md) | Defaults env et modes fallback |
| [`runtime_activation.md`](runtime_activation.md) | État dispatcher @ checkpoint |
| [`ct_rotation_contract.md`](ct_rotation_contract.md) | Contrat 30/4 + repairs + audit |
| [`post_run_audit_plan.md`](post_run_audit_plan.md) | Plan audit prochain run naturel |

---

## Guard existant

Le guard sanctuary historique reste valide :

```bash
scripts/check_golden_flow_untouched.sh
```

Pour toute modification intentionnelle des fichiers cœur listés dans `locked_files_manifest.json` :

1. Lire `invariants.md`
2. Obtenir GO Liam explicite
3. Patch minimal et local
4. Relancer les tests listés dans le manifest
5. Mettre à jour ce checkpoint si la release change

---

## Tests de verrouillage obligatoires

```bash
python3 -m pytest tests/test_golden_flow_open_follow_mute_return.py \
  tests/test_follow_source_rotation_contract.py \
  tests/test_post_follow_like_samsung_fast.py \
  tests/test_followers_entry_search_surface_recovery.py \
  tests/test_follow_targets_runtime_p1b.py \
  tests/test_preflight_keyguard_handling.py \
  tests/test_scheduled_session_preflight_runner.py \
  tests/test_scheduled_session_preflight_dispatcher.py -q
```
