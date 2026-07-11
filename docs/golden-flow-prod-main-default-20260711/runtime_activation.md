# Runtime activation — Checkpoint 2026-07-11

**État capturé au verrouillage checkpoint. Ne pas restart dispatcher dans le cadre de ce commit docs.**

---

## Release et symlink

| Champ | Valeur |
|-------|--------|
| **Symlink** | `/Users/admin/phonefarm-worker-current` |
| **Cible** | `/Users/admin/phonefarm-worker-releases/b6fedca-plus-search-surface-recovery` |
| **Commit disque** | `0a6f62d0452f34ae43e884497d496a116b06ff53` |
| **Phase 1** | `4f7f8730a1fc5b6b27987616acfe0bca4df3e809` |
| **Branche** | `release/b6fedca-plus-search-surface-recovery` |

---

## Dispatcher @ checkpoint

| Champ | Valeur |
|-------|--------|
| **PID** | `75050` |
| **Démarrage** | `2026-07-11 21:46:54` (local) |
| **CWD** | `/Users/admin/phonefarm-worker-releases/b6fedca-plus-search-surface-recovery` |
| **Commit runtime chargé** | `0a6f62d` (process redémarré post-Phase 2 + CT repair) |
| **Status** | `running` |
| **ok** | `true` |
| **preflightOk** | `true` |
| **queueActiveCount** | `0` |
| **requests** | `[]` |
| **Launchd label** | `com.boost.phonefarm.dispatcher` |

---

## Confirmations opérationnelles @ checkpoint

| Contrainte | Statut |
|------------|--------|
| Run manuel lancé | **NON** |
| Tick manuel | **NON** |
| Android touché | **NON** |
| DB mutation (post repair mythyl/tracker) | repair contrôlé terminé ; pas de nouvelle mutation @ checkpoint |
| Restart dispatcher (ce commit docs) | **NON** — interdit |

---

## Commandes de vérification (lecture seule)

```bash
readlink /Users/admin/phonefarm-worker-current
/Users/admin/phonefarm-runtime/bin/phonefarm-runtimectl dispatcher status --json
pgrep -fl account_session || echo "no account_session"
```

---

## Historique activation (référence)

| Étape | Commit | Action |
|-------|--------|--------|
| Phase 1 commit | `4f7f873` | like nominal + CT log guard |
| Phase 1 runtime | `4f7f873` | dispatcher restart PID `62076` @ 21:06 |
| Phase 2 commit | `0a6f62d` | Golden defaults + CT helpers |
| CT repair | — | mythyl + tracker → 30/4 |
| Phase 2 runtime | `0a6f62d` | dispatcher restart PID `75050` @ 21:46 |
