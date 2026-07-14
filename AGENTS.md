# Architecture officielle — Phone Farm Instagram

Ce document fixe la **direction technique obligatoire** pour tout développement sur ce dépôt (navigation Instagram, workers, phone farm). Toute évolution doit s’y conformer.

**Index détaillé** : les sujets longs (navigation engine, vision, follow action, post-follow, recovery, état de session) sont décrits dans le dossier [`docs/`](docs/) — voir la section *Project documentation map* en fin de fichier.

---

## 1. Couches et rôles

### XML / Accessibility

- **Rôle** : vitesse, lecture UI rapide, actions simples, extraction structurée, navigation standard.
- **Principe** : le XML **ne doit plus** être considéré comme **source unique de vérité**. C’est un signal **prioritaire pour la vitesse**, pas pour la décision finale seule.

### Vision Layer

- **Rôle** : vérification, fallback, navigation sur surfaces fragiles, gestion des **XML stale**, détection des **faux positifs**, confirmation des **transitions d’écran**, interaction sur surfaces dynamiques.
- **Capacités attendues** : la vision peut **confirmer**, **corriger** ou **remplacer temporairement** le XML lorsque celui-ci est insuffisant ou trompeur.

### Navigation State Machine

- **Rôle** : **décision finale**, compréhension du **vrai** écran courant, éviter les mauvaises décisions, **empêcher les actions sur le mauvais écran**, gérer les transitions de façon explicite.
- **Principe** : toute **action critique** doit être **validée** par l’état courant (machine d’état / observation hybride), pas uniquement par un seul widget XML.

### Recovery Engine (centralisé ou réutilisable)

- **Rôle** : récupération automatique, retries intelligents, retour vers un **état stable**, gestion des erreurs silencieuses, **anti-boucle**, **anti-faux positifs**, auto-healing.
- **Principe** : aucun flow critique ne doit dépendre d’un **seul signal fragile**.
  Fallback + recovery sont attendus là où ils sont déjà autorisés par les
  décisions verrouillées ; ils ne doivent jamais être inventés pour contourner
  la preuve Golden préalable.

### Scalabilité / production

Le système doit rester :

- scalable ;
- **fault-tolerant** ;
- **observable** (logs, raisons d’échec, corrélation run/device/compte) ;
- modulaire ;
- **recovery-first** ;
- compatible **multi-device** ;
- compatible **multi-clones** ;
- compatible **sessions longues** stables et industrialisables.

---

## 2. Priorité technique (ordre d’usage)

1. **XML / accessibilité** — vitesse et extraction structurée quand le signal est fiable.  
2. **Vision** — vérification, fallback, surfaces instables, XML stale.  
3. **Navigation State Machine** — **décision finale** avant action critique.  
4. **Recovery Engine** — sécurité production, retries, retour état stable.

L’ordre n’implique pas l’exclusion : plusieurs couches peuvent et doivent coopérer sur un même chemin.

---

## 3. Règles obligatoires de développement

- **Pas** de dépendance unique au XML pour la vérité UI ou la navigation critique.
- **Pas** de logique fragile **one-shot** (un seul tap, une seule assertion, un seul timeout sans branche de secours).
- **Pas** de validation basée sur **un seul signal** lorsque l’action est critique ou irréversible.
- Toute **navigation importante** doit combiner **plusieurs signaux** lorsque c’est possible : typiquement **XML + vision + validation d’état** (ou équivalent documenté).
- Toute **action critique** doit prévoir les fallbacks et recoveries déjà autorisés
  par le flow protégé. Pour Follow/latence, aucun nouveau timeout, fallback ou
  recovery ne peut être ajouté avant la preuve sous-étape Golden exigée dans
  [`docs/LOCKED_DECISIONS.md`](docs/LOCKED_DECISIONS.md).
- Toute **nouvelle feature** doit respecter cette architecture, les décisions
  verrouillées et documenter les signaux utilisés et les chemins de recovery
  autorisés.
- **Codex / contributeurs** : avant de modifier le moteur, **consulter d’abord** les fichiers pertinents sous [`docs/`](docs/) (carte en section 8) plutôt que de s’appuyer uniquement sur le contexte du chat. Codex est l'agent de développement unique depuis le 2026-07-14.

---

## 4. Critères de PR (checklist)

- [ ] La feature n’assume pas le XML comme vérité unique pour les décisions critiques.
- [ ] Les chemins sensibles utilisent **plusieurs signaux** (XML, vision, état) quand c’est raisonnable.
- [ ] Les actions critiques utilisent uniquement des **fallbacks** et/ou
  **recoveries** autorisés et identifiables ; Follow/latence respecte le gate
  de preuve Golden.
- [ ] Les échecs exposent une **reason explicite** (chaîne stable ou enum documentée), pas un échec opaque.
- [ ] Les points importants produisent des **logs structurés** (champs clairs : phase, `reason`, identifiants compte/candidat, etc.).
- [ ] Pas d’anti-boucle oublié sur les retries (limites, compteurs, abandon propre).
- [ ] Impact observabilité / multi-device pris en compte (pas de couplage implicite à un seul modèle d’écran).

---

## 5. Interdictions explicites

- Flows **one-shot fragiles** sans alternative ni recovery.
- Décisions critiques sur **un seul** sélecteur XML ou **un seul** timeout sans validation d’état.
- Masquage d’erreurs : tout échec significatif doit être **observable** avec une **reason** exploitable en prod.

---

## 6. Observabilité (obligatoire pour l’action critique)

- **Logs structurés** : événements nommés, champs cohérents entre appels (ex. `source_profile_username`, `visual_candidate_id`, `failure_reason`, `phase`).
- **Reason d’échec explicite** : valeur stable et lisible pour filtrage / alertes / post-mortem.
- Corrélation souhaitable : `run_id`, compte, device, quand le pipeline le permet.

---

## 7. Objectif final du moteur hybride

Construire une navigation **production-grade** capable de :

- survivre aux changements UI Instagram ;
- survivre aux **XML stale** ;
- fonctionner sur une **vraie phone farm** ;
- fonctionner sur de **nombreux comptes** ;
- **réduire fortement les faux positifs** ;
- maintenir des **sessions longues** stables ;
- rester **industrialisable** (scalabilité, clones, observabilité, recovery).

---

## 8. Project documentation map

| Document | Sujet |
|----------|--------|
| [docs/navigation-engine.md](docs/navigation-engine.md) | Rôle du Navigation Engine, observe / états, fingerprint, anti false-positive |
| [docs/vision-layer.md](docs/vision-layer.md) | Fallback visuel followers, row mapping, multi tap points, screenshot, staleness |
| [docs/follow-action-engine.md](docs/follow-action-engine.md) | Follow Action Engine V2/V3, fast path, harvester, CTA, exclusions Contact, anti-faux tap |
| [docs/post-follow-flow.md](docs/post-follow-flow.md) | Phase post-follow, reconciliation, mute, privé, return CT, partial, drift |
| [docs/recovery-engine.md](docs/recovery-engine.md) | Recovery-first, retries, anti-dérive, abort, logs, reasons |
| [docs/dev-session-latest.md](docs/dev-session-latest.md) | Mémoire courte de l’état actuel du projet et priorités |

## 9. Règle documentation-first (futurs changements)

Pour les **futures modifications** sur ce dépôt, Codex (ou tout contributeur explicitement autorisé) doit **lire en priorité** les documents `docs/` applicables au périmètre touché, puis le code source. Le contexte seul du chat **ne suffit pas** comme seule source de vérité sur l’architecture hybride (XML + vision + état + recovery).

---

*Document d’architecture — à maintenir lors des évolutions majeures du moteur de navigation. Détails d’implémentation : voir aussi `docs/`.*
