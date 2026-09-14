# `datasets/manifests/` — dataset manifests

**Class:** Source. **Owner:** the dataset adapter author. **Hand-authored.**

One JSON document per dataset. Each records where the data came from — pinned to something
immutable — under what licence, what processing produced the records, and the digests of the
result.

A manifest **describes**; it does not commit. The commitment is a freeze under
`datasets/freezes/`.

## Naming

`<dataset_id>.json`, where `dataset_id` matches `[a-z0-9][a-z0-9._-]*` and is referenced by
`registry/datasets.yaml`.

## Required fields

`dataset_manifest_version`, `schema_version`, `dataset_id`, `dataset_version`, `source_type`,
`split`, `license`, `record_count`, `adapter_version`, `deduplication_policy`,
`contamination_policy`.

Two version fields, deliberately not merged: `dataset_manifest_version` is the version of
*this document format*; `schema_version` is the version of *the records themselves*.
Changing a field name and changing the record layout are different events.

## Validation

`engramfold validate` checks each manifest against its schema, against its immutable-source
requirement for its `source_type`, and against `registry/datasets.yaml`. Unknown field names
are errors — a renamed field must fail rather than be silently ignored, because every
downstream check that reads it would otherwise quietly stop applying.

## Currently empty

No dataset exists. See [`datasets/README.md`](../README.md) and
[`registry/populations.yaml`](../../registry/populations.yaml).