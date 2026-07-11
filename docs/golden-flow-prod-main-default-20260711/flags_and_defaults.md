# Flags et defaults — Golden Flow Prod Main Default

**Checkpoint :** `0a6f62d` @ 2026-07-11

---

## Modes Golden / fallback (config.py)

| Variable env | Default code | Valeurs | Rôle |
|--------------|--------------|---------|------|
| `POST_FOLLOW_LIKE_STRICT_GRID_PROOF_MODE` | `fallback` | `off` \| `fallback` \| `always` | Strict grid proof **hors nominal** sauf échec ou `always` |
| `FOLLOWERS_ENTRY_SEARCH_SURFACE_RECOVERY_MODE` | `fallback` | `off` \| `fallback` \| `always` | Search Recovery **hors pre-entry nominal** ; secours après échec Golden |
| `POST_FOLLOW_RETURN_CT_STALE_ACTION_BAR_MODE` | `off` | `off` \| `fallback` \| `always` | Acceptation stale action bar **hors nominal** ; Golden CT strict d'abord |

---

## Comportement par mode

### `POST_FOLLOW_LIKE_STRICT_GRID_PROOF_MODE`

| Mode | Nominal réussi | Après échec nominal |
|------|----------------|---------------------|
| `off` | pas de strict proof | pas de strict proof |
| `fallback` | **pas de strict proof** | strict proof autorisé |
| `always` | strict proof toujours | strict proof toujours |

**Checkpoint :** default `fallback` — aligné Phase 1 Golden like restoration.

### `FOLLOWERS_ENTRY_SEARCH_SURFACE_RECOVERY_MODE`

| Mode | Pre-entry (`open_followers_list`) | Fallback |
|------|-----------------------------------|----------|
| `off` | jamais | jamais |
| `fallback` | **non** si `profile_verified=True` | oui après échec Golden / profil non vérifié |
| `always` | oui (comportement secours explicite) | oui |

**Checkpoint :** default `fallback` — Search Recovery **fallback only**.

### `POST_FOLLOW_RETURN_CT_STALE_ACTION_BAR_MODE`

| Mode | Confirmation CT nominale |
|------|------------------------|
| `off` | CT strict uniquement (**Golden**) |
| `fallback` | CT strict d'abord ; stale action bar si échec strict |
| `always` | stale action bar autorisé nominalement |

**Checkpoint :** default `off` — Golden return CT nominal.

---

## CT rotation contract (constants)

Définies dans `follow_source_rotation_settings.py` et `account_session_orchestrator.py` :

| Constante | Valeur |
|-----------|--------|
| `CONTRACT_MAX_FOLLOWS_PER_TARGET_PER_RUN` | `30` |
| `CONTRACT_MAX_TARGETS_PER_RUN` | `4` |

**Fallback config (sans row DB) :** `default 2/3` via `config.py` — **interdit silencieusement** pour comptes prod/scheduled (log guard Phase 1).

---

## Overrides env — règles

- Tout override env qui force `always` sur Search Recovery ou stale action bar = **déviation explicite** du checkpoint prod.
- Documenter et obtenir GO Liam avant déploiement permanent d'un override `always`.
- Ne pas modifier schedules/caps/packages pour compenser un changement de mode.

---

## Vérification rapide (sans run)

```bash
cd /Users/admin/phonefarm-worker-releases/b6fedca-plus-search-surface-recovery
python3 -c "import config; print('like', config.POST_FOLLOW_LIKE_STRICT_GRID_PROOF_MODE); print('search', config.FOLLOWERS_ENTRY_SEARCH_SURFACE_RECOVERY_MODE); print('return_ct', config.POST_FOLLOW_RETURN_CT_STALE_ACTION_BAR_MODE)"
```

Attendu :

```
like fallback
search fallback
return_ct off
```
