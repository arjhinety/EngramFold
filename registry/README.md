# `registry/` — ownership and purpose

**Owner:** maintainers of the EngramFold integrity substrate.
**Status:** canonical, hand-authored source. Never generated.

## What lives here

| File | What it is |
|---|---|
| `populations.yaml` | The declared phase, and per-population policy on whether an empty population is legitimate. Validated against `README.md` and against what the registry actually contains. |
| `datasets.yaml` | Index of dataset records: id → manifest path → freeze ids. |
| `models.yaml` | Index of pinned model revisions. |
| `experiments.yaml` | Index of experiment records: id → manifest path → dataset/artifact ids. |
| `artifacts.yaml` | Index of artifact records: id → manifest path. |
| `reports.yaml` | Index of report records: id → the artifacts and experiments it interprets. |
| `claims.yaml` | Canonical claims and the machine-readable evidence each one cites. |
| `schemas/` | JSON Schemas for the registry documents, for readers outside this codebase. |

## Rules

1. **Hand-authored.** No file here is produced by a generator, and none may carry a
   `generated_by` marker. The substrate gate fails if one does, because a source file that
   looks generated is a source file nobody will dare edit.
2. **References must resolve.** A record naming a manifest, freeze, artifact or experiment
   that does not exist is an error. Deleting a record silently orphans everything that
   pointed at it, which is why this is enforced rather than warned about.
3. **`populations.yaml` is load-bearing.** It is how a gate knows whether discovering zero
   targets is an authorised outcome. Two consistency rules keep it honest: `OPTIONAL` while
   records exist is a contradiction, and `OPTIONAL` is only legal while the phase matches
   `engramfold.STATUS` — which must also appear as the `STATUS:` line in `README.md`.
4. **A claim is not a result.** `claims.yaml` is empty and must stay empty until there is
   machine-readable evidence to cite. A `SUPPORTED` claim with no resolving evidence fails
   validation.

## What is deliberately empty

Every list in this directory is empty. EngramFold is pre-experiment: it has chosen no
dataset, no model, no experiment and no metric. The emptiness is declared and reasoned in
`populations.yaml`, not implied by a missing file.