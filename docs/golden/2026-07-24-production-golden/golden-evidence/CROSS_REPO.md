# Cross-repository evidence

The Golden checkpoint is authoritative for the Worker repository. External
artifacts are recorded as boundaries, not silently folded into Worker Git
provenance.

## Backend / Frontend production

Read-only Vercel inspection on `2026-07-24`:

| Item | Value |
|---|---|
| deployment ID | `dpl_Ab6AKB5rXxvuGyuUiXe7f2tZc5K4` |
| project | `boost-my-businesses-ai-frontend-vercel` |
| target / state | `production` / `Ready` |
| production alias | `https://www.boostmybusinesses.com` |
| immutable deployment URL | `https://boost-my-businesses-ai-frontend-vercel-33f1kwsq0.vercel.app` |

The live deployment metadata did not expose a source Git SHA. The last
coordination references were Backend code `108f7de` and docs `bd37a1e`; they
are not asserted as proven provenance of the inspected Vercel artifact.

## BotApp installed artifact

| Item | Value |
|---|---|
| bundle | `com.boostmybusinesses.botapp` |
| version/build | `0.1.0` |
| installed `app.asar` SHA-256 | `8b956c792bfdc08a0663bc952a883fe77d4010a2dcb7de5c95ce5cd523c38253` |
| code-directory hash | `31d2cc5eef57a1452845da97d21f340a881c7435` |
| signing provenance | ad-hoc; no TeamIdentifier |

The installed package did not contain a source-provenance manifest. The last
coordination references were BotApp code `5f5b6a8` and docs `9bc2697`; they are
not asserted as an exact build-source proof.

No Backend, Frontend or BotApp repository or deployment was changed by this
checkpoint.
