# Scheduler / CP4 / Preflight / BotApp Observability — Interim Checkpoint

```text
Status: INTERIM CHECKPOINT — block not fully closed yet.
```

**Checkpoint date (UTC+2):** 2026-07-10 ~17:00  
**Scope:** Daily Scheduler + CP4 preflight hardening + BotApp pipeline observability  
**Language:** operator UI remains English-only (BotApp)

---

## 1. État global actuel

| Signal | État observé |
|--------|----------------|
| Scheduler global | **ON** (`auto_restart_settings.auto_restart_enabled=true`) |
| Daily engine `dry_run` | **false** (enqueue réel autorisé quand gates passent) |
| BotApp runtime gate | **passing** (heartbeat `botapp-scheduler-runtime:{host}` frais) |
| Dispatcher canonique | `run-dispatcher:mac-admin-01` |
| No blind start | **strictement préservé** — pas de `account_session` sans `preflight_ready` |
| Comptes programmés (growth) | `mythyl_fitness` **12:00–18:00** ; `i_m_your_traker` **18:00–00:00** |

**Clarification Auto Restart vs Daily Scheduler**

- `resume_plan_missing` dans **Auto Restart Recent decisions** = bruit Auto Restart (pas d’échec Daily Scheduler).
- L’ancienne vue « Runs enqueued (24h) » reflétait surtout Auto Restart, pas les tentatives Daily Scheduler complètes.

---

## 2. Corrections backend / CP4 (boost-ai-frontend)

### Dispatcher / prod env

- `INSTAGRAM_RUN_CONTROL_DISPATCHER_WORKER_ID` prod aligné sur `run-dispatcher:mac-admin-01` (corrige `dispatcher_unconfigured` côté tick/cron).

### CP4.1 late preflight expiry

- **T-10:** `expires_at = session_start`
- **Late:** `expires_at = business_action_deadline`
- RPC `get_valid_scheduled_session_preflight` accepte `preflight_ready` jusqu’à la business deadline pour les rows late.

### CP4.1 late retry idempotency

- Clé déterministe `:retry:{preflightId}:{supersededRequestId}` sur la base late idempotency key.
- Cap anti-boucle: `MAX_LATE_PREFLIGHT_RETRY_GENERATIONS = 3` par fenêtre logique.

### Retry scoping

- **Non retryable (identity/challenge):** `login_challenge`, `checkpoint`, mismatch identity, etc.
- **Retryable (technical):** `preflight_expired`, `preflight_invalidated`, `preflight_lease_unavailable`, `device_serial_missing`.

### Dashboard action mapping

- `preflight_blocked` → action dashboard **pending** (opérateur)
- Terminal ready/expired → **resolved** via transition RPC / `resolvePreflightDashboardActionStatus`
- `reason_code` réel propagé dans metadata_safe + read-model

### Stale device lock self-heal

- `reconcileStaleDeviceLockBeforePreflight` avant enqueue CP4
- Release RPC canonique ; migration `20260710160100_drop_ambiguous_release_device_lock_overloads.sql` (overload ambiguë supprimée)

### BotApp observability read-model (deploy prod)

- `lib/instagram-dashboard/daily-scheduler-pipeline.ts` — projection tick → preflight → account_session (read-only)
- `scheduler-status.ts` expose `daily_scheduler_pipeline` + `daily_runtime_gate`
- `scheduler-reasons.ts` — codes keyguard + preflight enrichis

---

## 3. Corrections worker (instagram-worker-python)

### Preflight run type

- Support `scheduled_session_preflight` dans consumer + assignment resolver
- Claim dispatcher/worker pour preflight requests

### Hydration device / package

- ADB serial hydration pour `scheduled_session_preflight` dans `assignment_dispatch_resolver`
- `config.INSTAGRAM_PACKAGE = package_name` (corrige package mismatch clone)

### Preflight Hygiene Lite

- `force-stop` uniquement sur le package attendu
- Home → relaunch expected package
- Pas de « Close All »

### Guard Recovery Niveau A (preflight only)

- Classification login/challenge/checkpoint/popup/loading
- Screenshot/XML artifacts locaux + metadata_safe (pas de secrets)
- Identity guard avant décision terminal

### Keyguard handling (release active)

- `ensure_preflight_device_unlocked()` — wake + swipe-up **swipe-only**
- Reason codes: `device_locked`, `device_locked_requires_operator`
- Metadata: `screen_type=device_keyguard`, `detection_reason=android_keyguard_detected`, `unlock_attempted`, `unlock_result`
- **Aucun** bypass PIN/password/pattern

---

## 4. Corrections BotApp — Daily Scheduler Pipeline Observability

**Repo:** `BotApp-clean`  
**Backup installé:** `/Users/admin/phonefarm-botapp-releases/daily-scheduler-observability-20260710T145509Z.app` → `/Applications/BotApp.app`

- Séparation UI **Daily Scheduler Pipeline** / **Auto Restart Engine**
- Détails preflight visibles (phase T-10/late, reason_code, keyguard metadata)
- Détails account_session visibles dans le pipeline
- `resume_plan_missing` documenté comme bruit Auto Restart
- Badges Profiles corrigés (`profile-growth-badge.ts`):
  - `connected` ≠ growth ready
  - `scheduler_launch_blocked` ≠ waiting slot
  - `device_locked` visible
  - preflight blocked visible
- Copy diagnostics enrichi (`App.tsx` — daily_scheduler + auto_restart sections)

---

## 5. Observations production — progression diagnostique

La chaîne Daily Scheduler a progressé par couches de blocage successives :

1. Blocages très haut niveau (scheduler off / gates / runtime)
2. `dispatcher_unconfigured` (worker id prod)
3. Missing preflight claim (worker run type)
4. `device_serial_missing` (resolver hydration)
5. Idempotency retry collisions (late CP4.1)
6. Package mismatch clone (`INSTAGRAM_PACKAGE`)
7. Stale device lock (lease CP3)
8. Keyguard / `device_locked` (Samsung swipe-to-open)

**Aujourd’hui:** la chaîne tick → preflight → session est **visible** dans BotApp + tables Supabase ; diagnostics beaucoup plus actionnables qu’en début de bloc CP4.

---

## 6. Tables / sources de vérité

| Source | Usage |
|--------|--------|
| `scheduled_session_preflights` | État CP4 canonique par fenêtre |
| `account_run_requests` | Queue preflight + account_session |
| `account_dashboard_actions` | Actions opérateur (preflight_blocked pending) |
| `auto_restart_decisions` | **Auto Restart only** — ne pas confondre avec Daily Scheduler attempts |
| `auto_restart_device_locks` | Locks restart (distinct preflight lease) |
| `device_ui_lease_events` | CP3 lease preflight / handoff |
| `account_session_resume_plans` | Plans Auto Restart (`resume_plan_missing` noise) |
| BotApp **Scheduler** view | Pipeline read-model + runtime gate |
| Slack `phones_run_incidents` | Incidents phone farm |

```text
Auto Restart Recent decisions ≠ Daily Scheduler attempts.
Runs enqueued (24h) in old view was Auto Restart only.
```

---

## 7. Ce qui reste ouvert

### Validation naturelle finale (pas encore close en prod)

- [ ] `preflight_ready` réel en fenêtre active
- [ ] `account_session` créée au tick suivant
- [ ] Dispatcher claim `account_session`
- [ ] Phone quitte idle
- [ ] Run terminalise proprement
- [ ] Dashboard action résolue
- [ ] Positive path `get_valid_scheduled_session_preflight → account_session` validé end-to-end

### Backlog technique explicite

- T-10 observability **P1**
- T-10 robustness **P2**
- Recovery Guard **Niveau B/C** non faits (dismiss popups, Not Now/Skip, vision fallback)
- Auto Restart noise/dedup `resume_plan_missing` — amélioration future
- Worker keyguard + autres patches locaux non commités dans ce checkpoint (voir git status worker)

---

## 8. Critères de validation finale restants

```text
[ ] fenêtre active
[ ] preflight T-10 ou late créé
[ ] dispatcher claim preflight
[ ] serial + package OK
[ ] identity guard OK
[ ] preflight_ready
[ ] tick suivant crée account_session
[ ] dispatcher claim account_session
[ ] worker démarre session
[ ] BotApp phone non-idle
[ ] run terminalise proprement
[ ] dashboard action résolue
[ ] no blind start respecté
```

---

## 9. Snapshot technique (IDs réels — vérifiés 2026-07-10)

| Composant | ID / chemin |
|-----------|-------------|
| Backend Vercel prod | `dpl_2rXNt8A1eebFRdV5PSgdMudkrVF2` (Ready, 2026-07-10 ~16:55 UTC+2) |
| Backend git (pré-commit local) | `a97c876` — late CP4 expiry + dashboard reconcile |
| Worker release active | `/Users/admin/phonefarm-worker-releases/b6fedca-preflight-keyguard` |
| Worker git HEAD (repo) | `b6fedca` — late CP4 + dashboard reconcile |
| Dispatcher worker id | `run-dispatcher:mac-admin-01` |
| BotApp package backup | `daily-scheduler-observability-20260710T145509Z.app` |
| BotApp git (pré-commit local) | `95dd4f1` + patch observability non commité |

### Migrations Supabase pertinentes (CP4 / scheduler)

- `20260707180000_device_ui_lease_cp3.sql`
- `20260707220000_cp4_scheduled_session_preflights.sql`
- `20260709120000_cp4_late_preflight_expiry_fix.sql`
- `20260710120000_auto_restart_settings.sql`
- `20260710120100_auto_restart_v1_execution_foundation.sql`
- `20260710120200_auto_restart_device_lock_lifecycle.sql`
- `20260710160000_cp0_scheduler_toggle_gates_automatic_run_requests.sql`
- `20260710160100_drop_ambiguous_release_device_lock_overloads.sql` (local, à committer backend)

### Dernier checkpoint / doc de référence antérieur

- `docs/dev-session-latest.md` — mémoire volatile (derniers jalons 2026-06-xx)
- Backend `6971ed9` — `docs(admin): record production CP6 and Scheduler activation`
- Pas de dossier `docs/checkpoints/` avant ce fichier

---

## 10. Repos touchés par ce bloc

| Repo | Rôle |
|------|------|
| `boost-ai-frontend-clean` | CP4 cron/RPC, read-model pipeline, migrations |
| `instagram-worker-python` | Preflight runner, identity/keyguard, dispatcher consumer |
| `BotApp-clean` | Scheduler UI observability, badges Profiles, diagnostics |

**Hors scope checkpoint:** Android direct, schedules/caps/packages/clones/assignments/credentials/DM non modifiés pour ce commit doc.

---

*Interim checkpoint — ne pas interpréter comme GO production end-to-end Daily Scheduler.*
