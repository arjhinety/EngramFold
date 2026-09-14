# `datasets/` — ownership and purpose

**Owner:** whoever authors a dataset adapter.
**Status:** mixed. `manifests/` is hand-authored source; `freezes/` is generated.

## Ownership

| Path | Class | Owner |
|---|---|---|
| `manifests/` | Source | The adapter author. Hand-authored, one JSON file per dataset. |
| `freezes/` | Generated | `engramfold freeze dataset`. Never hand-edited. |

## What belongs here

Only the *description* of data: manifests and freezes. **Never the bytes.** Raw corpora and
derived shards are large, are not canonical research state, and are excluded by
`.gitignore`. What is canonical is the manifest that describes them and the freeze that pins
their digests.

That separation is deliberate. If a corpus were committed, a clone would carry it and a
deletion would be a history rewrite. If a corpus is *absent* while its freeze is present,
verification reports the artifact as missing and names the digest it should have had —
which is strictly more useful than either silence or a broken clone.

## The rules a manifest must satisfy

- Required: both version fields, `dataset_id`, `dataset_version`, `source_type`, `split`,
  `license`, `record_count`, `adapter_version`, `deduplication_policy`,
  `contamination_policy`.
- `source_type == "huggingface"` requires a full 40-character `source_revision`. Branches
  and abbreviated revisions are rejected: they pin nothing.
- Processing that produced files must record the command, the code revision, the
  configuration hash and the seed, or the processed bytes cannot be re-derived.
- `record_count: 0` requires an `empty_dataset_rationale`, because zero is usually a filter
  that matched nothing.
- Unknown field names are errors, not warnings. A renamed field is not silently ignored.

See [`docs/PROVENANCE.md`](../docs/PROVENANCE.md#dataset-manifests) for the full schema and
[`docs/REPRODUCIBILITY.md`](../docs/REPRODUCIBILITY.md) for why the version fields are two
fields and not one.

## The rules a freeze must satisfy

- Frozen bytes are pinned by digest, not by path alone.
- A freeze refuses to overwrite a freeze whose identity has changed; a genuine change means a
  new freeze id and a new file.
- Regenerating an unchanged freeze is a no-op that leaves the file byte-for-byte alone.

See [`docs/ERRATA_POLICY.md`](../docs/ERRATA_POLICY.md) for why.

## Current state

Both directories are empty of data. No dataset has been selected, downloaded, adapted or
frozen; choosing one is a research decision reserved for the phase after this substrate is
trusted. The emptiness is declared and reasoned in
[`registry/populations.yaml`](../registry/populations.yaml), not implied by an absent file.