# `experiments/` — ownership and purpose

**Owner:** whoever designs an experiment.
**Status:** mixed. `manifests/` is hand-authored source; `freezes/` is generated.

## Ownership

| Path | Class | Owner |
|---|---|---|
| `manifests/` | Source | The experiment author. Hand-authored definitions. |
| `freezes/` | Generated | `engramfold freeze experiment`. Never hand-edited. |

## What belongs here

Definitions and their frozen identities. **No results** — a result is an artifact, and
artifacts are recorded in `artifacts/manifests/`.

## Statuses and their obligations

A manifest's obligations grow with its status. `DRAFT` may be incomplete; once it claims the
experiment ran, the fields that make the run attributable become required:

| Status | Obligations |
|---|---|
| `DRAFT` | Identity, description, configuration, configuration hash |
| `FROZEN` | The above, plus a definition freeze before any compute is spent |
| `RUNNING` | Plus `seed`, `code_revision`, `environment_manifest`, `command`, `started_at` |
| `COMPLETED` | Plus `completed_at`, a non-empty artifact list and artifact hashes |
| `FAILED` / `INVALID` | Plus `completed_at`; `INVALID` preserves a non-result as evidence |
| `ARCHIVED` | Superseded or withdrawn; kept in the record |

An `INVALID` record must not carry an effective `COMPLETED` claim in its metadata. A run that
produced no real artifact is evidence about the pipeline, not a result.

## Definition drift

`configuration_hash` is recorded **and recomputed on load**. Editing the configuration block
after the fact makes the recomputation disagree with the recorded value and validation fails.
That is the mechanism behind "has an experiment definition changed since execution?" — the
recorded hash is not a note about the configuration, it is a check on it.

Definition freezes go further and pin the whole definition. See
[`docs/VALIDATION.md`](../docs/VALIDATION.md#definition-drift).

## Current state

Empty. No experiment definition exists, because EngramFold has deliberately not designed
Study 001. The hypothesis, the model family, the operational meaning of "expert function",
the consolidation baselines, the preservation metrics and the ablation design are all
undecided — see [`docs/METHODOLOGY.md`](../docs/METHODOLOGY.md), which marks the research
design NOT YET FROZEN.