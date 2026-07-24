# Golden release registry

## Canonical Worker

| Layer | Reference | Restore authority |
|---|---|---|
| Code | `e7f54a9ee750e9fa4c5e5d081649fa4da6bc4f8f` | Git commit |
| Auto Login base | `abf0ebf90bcccd063355b41505d4d6541e870047` | Parent commit |
| Navigation source | `f93c501c334b6c5b87ab04b72759bc60a452f5a8` | Source patch provenance |
| Release | `abf0ebf-f93c501-navigation-consolidated-v1` | Immutable worktree |
| Runtime pointer | `/Users/admin/phonefarm-worker-current` | Atomic symlink |
| Docs branch | `docs/golden-phonefarm-production-20260724` | Documentation only |
| Golden tag | `golden-phonefarm-production-2026-07-24` | Signed-off annotated reference |

The integration commit contains exactly nine navigation-scope files. Source and
destination patch IDs match. The docs commit contains only `docs/**`.

## External artifacts

- Vercel production: `dpl_Ab6AKB5rXxvuGyuUiXe7f2tZc5K4`, live URL
  `https://boost-my-businesses-ai-frontend-vercel-33f1kwsq0.vercel.app`, alias
  `https://www.boostmybusinesses.com`, status `READY`.
- BotApp installed asar SHA-256:
  `8b956c792bfdc08a0663bc952a883fe77d4010a2dcb7de5c95ce5cd523c38253`.

External artifacts are evidence dependencies, not objects tagged by the Worker
repository. See [Technical due diligence](TECHNICAL_DUE_DILIGENCE.md).
