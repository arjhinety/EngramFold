# `experiments/manifests/` — experiment definitions

**Class:** Source. **Owner:** the experiment author. **Hand-authored.**

One JSON document per experiment definition. Each records the configuration, the dataset
freezes it consumes, the model revision, the code and environment provenance a run needs, and
the command that would execute it.

## Naming

`<experiment_id>.json`, where `experiment_id` matches `[a-z0-9][a-z0-9._-]*` and is referenced
by `registry/experiments.yaml`.

## Inputs are pinned by freeze id

`dataset_freezes` is a list of freeze ids — digests of frozen content — not paths and not
dataset names. A path can hold different bytes tomorrow, so referencing an input by path pins
nothing. Validation resolves every id against `datasets/freezes/`.

## The configuration hash is checked, not trusted

`configuration_hash` must equal the recomputed identity of the `configuration` block. If the
block is edited after the hash was written, validation fails and names both digests. See
[`docs/VALIDATION.md`](../../docs/VALIDATION.md#definition-drift).

## Immutable revisions only

`model_revision` must be a full 40-character commit. A branch or tag resolves to different
weights over time and pins no model.

## Currently empty

No experiment has been designed. See [`experiments/README.md`](../README.md) for why, and
[`registry/populations.yaml`](../../registry/populations.yaml) for the written authorisation.