# CT Rotation Contract — Checkpoint 2026-07-11

---

## Contrat attendu (prod/scheduled)

| Champ | Valeur |
|-------|--------|
| Table | `account_follow_source_settings` |
| `settings_source` (runtime) | `account` |
| `max_follows_per_target_per_run` | `30` |
| `max_targets_per_run` | `4` |

**Symptôme default 2/3 :** switch CT tous les ~2 follows, `target_budget_reached` rapide, log `settings_source=default`.

---

## Repairs contrôlés effectués (GO Liam — 2026-07-11)

### mythyl_fitness

| Champ | Valeur |
|-------|--------|
| **account_id** | `0d299d1e-46ee-49d2-8a84-4f928f2bb182` |
| **Avant** | row absente → fallback `default 2/3` |
| **Après** | row `30/4`, `settings_source=account`, `contract_ok=true` |
| **Action** | `created_row_30_4` via `ensure_follow_source_rotation_contract_row` |
| **updated_by** | `worker:controlled_repair_liam_go` |

### i_m_your_traker

| Champ | Valeur |
|-------|--------|
| **account_id** | `83de9cc9-5c37-42d1-9edc-c924352b17b1` |
| **Avant** | row `1/2` |
| **Après** | row `30/4`, `settings_source=account`, `contract_ok=true` |
| **Action** | `repaired_row_to_30_4` via `repair_follow_source_rotation_contract(repair_go=True)` |
| **updated_by** | `worker:controlled_repair_liam_go` |

**Périmètre :** uniquement ces 2 comptes. **Pas de repair global.**

---

## Comportement futur attendu

### Provisioning (`provisioning_status=ready`)

Hook dans `instagram_login_provisioner_orchestrator._finalize` :

- appelle `maybe_provision_follow_source_rotation_on_ready`
- crée idempotemment row `30/4` si absente
- **ne met pas à jour** une row existante non conforme (nécessite repair explicite)

### Pendant `account_session` run

- `_resolve_follow_source_rotation_settings` lit la row DB
- `_log_follow_source_rotation_contract_if_needed` logue si gap (`repair_required=True`, `db_mutation_performed=False`)
- **aucune mutation DB silencieuse** pendant le run

### Repair explicite

```python
repair_follow_source_rotation_contract(
    account_id,
    dry_run=False,
    repair_go=True,  # GO Liam requis
)
```

---

## Audit dry-run (lecture seule)

```bash
cd /Users/admin/phonefarm-worker-releases/b6fedca-plus-search-surface-recovery
set -a && source /Users/admin/phonefarm-runtime/env/run-control-dispatcher.env && set +a

PYTHONPATH=. python3 scripts/audit_follow_source_rotation_contract.py \
  --account-id 0d299d1e-46ee-49d2-8a84-4f928f2bb182 \
  --account-username mythyl_fitness --json

PYTHONPATH=. python3 scripts/audit_follow_source_rotation_contract.py \
  --account-id 83de9cc9-5c37-42d1-9edc-c924352b17b1 \
  --account-username i_m_your_traker --json
```

Attendu post-repair : `contract_ok=true`, `repair_required=false`.

---

## Guard Phase 1 (inchangé)

Log event : `follow_source_rotation_contract_missing_account_settings`

Champs clés :

- `expected_max_follows_per_target_per_run=30`
- `expected_max_targets_per_run=4`
- `repair_table=account_follow_source_settings`
- `repair_required=True`
- `db_mutation_performed=False`

---

## Rotation engine

`_run_follow_target_rotation` : **inchangé** depuis restauration Golden.

Les caps résolus (`max_follows_per_target_per_run`, `max_targets_per_run`) proviennent de la row DB quand `settings_source=account`.
