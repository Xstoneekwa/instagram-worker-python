# Metrics Summary

## Golden Run: `fb3dbc0`

- request_id: `b93cb2d4-86d6-4411-b7bf-3fd23d1db745`
- run_id: `fc65bace-78f9-4d20-bd4d-c6ec29da583e`
- account/device: `lorielebras_autom` / `RFGL145LZHE`
- CT/source: `betongdesign`
- status: `stopped` via `botapp_manual_stop`
- started_at: `2026-06-15T10:42:36.277967Z`
- manual_stop_requested_at: `2026-06-15T10:48:26.959712Z`
- finished_at: `2026-06-15T10:48:27.259Z`

| Metric | Value |
|---|---:|
| Candidates opened | 4 |
| Complete candidates | 3 |
| Follows | 3 |
| Mutes | 3 |
| Likes verified | 3 |
| Return CT success | 3 |
| Multi-like | 0 |
| Return CT avg | `14.70s` |
| Like/failure → CT success avg | `16.51s` |
| Normal CT success → next selected avg | `11.42s` |
| Selected → CT success avg | `91.35s` |
| Selected → next selected avg | `110.73s` |
| DB useful productive avg | `116.89s/candidate` |

## Candidate Cycles

| Candidate | selected → CT | selected → next | Outcome |
|---|---:|---:|---|
| `flexhus_official` | `92.77s` | `103.80s` | complete |
| `bokochtankar` | `90.13s` | `101.93s` | complete |
| `malmobyggbolag` | `91.15s` | `126.47s` | complete, next window stop-affected |
| `villan.brorslund` | n/a | n/a | opened after manual stop/graceful shutdown |

## Post-Open

| Candidate | legacy open | reveal → open success | open → like tap | tap → verify |
|---|---:|---:|---:|---:|
| `flexhus_official` | `7.76s` | `9.37s` | `6.97s` | `2.18s` |
| `bokochtankar` | `6.98s` | `8.57s` | `6.42s` | `2.11s` |
| `malmobyggbolag` | `6.55s` | `8.18s` | `6.86s` | `1.98s` |

Average legacy open: `7.10s`.
Average reveal → open success: `8.71s`.

## Return CT

| Candidate | Fast proof | Return start → CT | Like → CT | CT → next |
|---|---|---:|---:|---:|
| `flexhus_official` | reused | `14.76s` | `16.63s` | `11.03s` |
| `bokochtankar` | reused | `14.48s` | `16.26s` | `11.80s` |
| `malmobyggbolag` | reused | `14.86s` | `16.63s` | `35.32s` stop-affected |

## Comparisons

| Reference | Role | Metric |
|---|---|---:|
| `fc65bace` / `fb3dbc0` | Golden Return CT validation | Return CT avg `14.70s` |
| `272f6925` / `b935653` | Post-like rollback reference | useful complete candidate about `110.01s`; Return CT before patch about `23.72s` |
| `bebf74a7` / `7fbe001` | mute/flow stable reference | 3 follows / 3 mutes / 3 likes / 3 Return CT OK |
| `5ad58174-95a0-45f2-91d7-33fcfdc3b5a0` / `f22c83c` | historical 9/3/3 baseline | `992s / 9 = 110.22s/candidate` |
