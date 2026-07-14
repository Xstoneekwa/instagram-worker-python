# Golden Evidence — Follow / Mute / Like / Return CT

> Evidence pack documentaire créé le 2026-07-14. Aucun log brut, screenshot,
> XML, secret ou donnée client n'est inclus. Les preuves non disponibles sont
> marquées `UNKNOWN` ou `NOT PHYSICALLY VALIDATED`.

## Graphe des commits

```text
fb3dbc0  physical Golden source used for code comparison
   │
   └── 86a01a6  sanctuary tag and sanitized evidence archive

476c8e1  modern production baseline
   ├── 6ec8270  controlled copy of 3 Golden blocks (not deployed)
   └── 74de873 → 9b7fa2f  active Welcome/Suggestions lineage
```

Important : `6ec8270` et `9b7fa2f` sont deux descendants distincts de la base
moderne. Git confirme que `6ec8270` n'est pas ancêtre de `9b7fa2f`.

## Références

| Rôle | Référence | Preuve | État |
|---|---|---|---|
| Source physique Golden | `fb3dbc057fdbaecf7e4c11fe4847020a4515a201` | commit Git | conservée |
| Archive/tag sanctuary | `86a01a6d4bfcee1b761966f173f7069759ce4325` | tag `golden-follow-mute-like-returnct-110s-20260615` | conservée |
| Run Golden | `fc65bace-78f9-4d20-bd4d-c6ec29da583e` | docs contrôlées + synthèse Codex | logs bruts complets `UNKNOWN` |
| Copie moderne | `6ec8270a51d5a7c6fdd4c40ee0982155588766a2` | commit + tests Codex | poussé, non déployé |
| Runtime actif | `9b7fa2f216448b5f3106468b2f3569a644f87080` | symlink + PID/CWD | actif, sans `6ec8270` |

## Timings connus

| Mesure | Valeur | Source | Confiance |
|---|---:|---|---|
| sélection candidat → Return CT/étape comparable | ~`91.35 s` | post-run audit plan versionné | documenté |
| cycle jusqu'au candidat suivant | ~`110 s` | tag/README Golden | documenté |
| CT contract | `30/4` | docs + repair contrôlé | verrouillé |
| écart Mythyl moderne observé | environ `+28 s` par cycle | analyse Codex du 2026-07-13 | historique, non revalidé |
| gain théorique `6ec8270` | jusqu'à ~`6 s`, dont `2.52 s` calculables Tier-1 | rapport de tests Codex | théorique uniquement |

Ne jamais convertir un gain théorique en gain production sans run physique.

## Invariants Golden

Le nominal doit rester :

```text
candidate selected
→ follow
→ mute posts/stories
→ open proven post
→ like + verify
→ strict Return CT
→ next candidate or CT rotation
```

- Follow : `perform_follow_safe`, preuve forte, vérification multi-signal.
- Mute : `run_mute_engine_v2`, niveau de sheet confirmé.
- Like : chemin nominal rapide, viewer/already-liked/verify conservés.
- Return CT : preuve CT stricte ; aucune stale action bar nominale.
- Search Recovery : fallback seulement après échec Golden prouvé.
- Toute extension doit conserver private gate, social memory, screen guard,
  quotas, identité et anti-boucle.

La liste détaillée reste dans
`docs/golden-flow-prod-main-default-20260711/invariants.md`.

## Trois différences copiées dans `6ec8270`

### Follow

- Ajout du plumbing Golden `pre_follow_context` et de la preuve profil forte
  vers `perform_follow_safe`.
- `probe_reused=true` n'est plus un motif de rejet isolé.
- Aucun nouveau timeout, retry ou probe ajouté.
- Verdict de copie : `DIRECT_GOLDEN_COPY_SAFE` rapporté dans Codex.

### Mute

- Transmission de `confirmed_sheet_level="mute_toggles"`.
- Utilisation du dismissal Golden level-aware.
- Comparaison fonctionnelle rapportée sans divergence.
- Verdict : `DIRECT_GOLDEN_COPY_SAFE`.

### Like Tier-1

- Restauration de `_visual_profile_no_posts_tier1_direct_check` depuis Golden.
- Suppression des probes séquentiels non Golden sur ce chemin.
- Verdict : `DIRECT_GOLDEN_COPY_SAFE`.

## Validation automatisée de `6ec8270`

Rapport Codex du 2026-07-13 :

- `py_compile` : OK ;
- Follow/Mute/Like ciblés : `175 passed` ;
- Golden Flow + Return CT + Search Surface Recovery : `166 passed` ;
- Golden guard avec autorisation explicite : OK ;
- `git diff --check` : OK ;
- no-leak : OK.

Ces résultats sont conservés dans Codex ; les sorties brutes complètes ne sont
pas incluses dans ce dossier. État : `TESTED`, `PUSHED`, `NOT DEPLOYED`,
`NOT PHYSICALLY VALIDATED`.

## Runtime actif et écart restant

- `9b7fa2f` contient les correctifs Welcome/Suggestions issus de `476c8e1`.
- Il ne contient pas `6ec8270`.
- Le runtime actif ne peut donc pas être présenté comme la copie moderne des
  trois blocs Golden.
- Le prochain run physique ne doit pas être lancé sans GO explicite.

## Preuve encore manquante

1. Logs bruts complets nettoyés du run `fc65bace` : `UNKNOWN`.
2. Mesures sous-étape par sous-étape sur `9b7fa2f` : `NOT PHYSICALLY VALIDATED`.
3. Mesures physiques sur `6ec8270` : `NOT PHYSICALLY VALIDATED`.
4. Verdict d'intégration ou de déploiement de `6ec8270` : `NOT DECIDED`.
5. Comparaison Golden vs prochain run naturel : `PENDING EXPLICIT GO`.
