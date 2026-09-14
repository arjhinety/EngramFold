# `datasets/freezes/` — dataset freezes

**Class:** Generated. **Owner:** `engramfold freeze dataset`. **Never hand-edited.**

A freeze states that this exact research stage consumed these exact bytes under this exact
manifest. It is the file that answers "has a frozen input changed?".

## Generated files

Every document here carries a `generated_by` field naming the function that wrote it. The
`substrate` validation gate fails if a generated document lacks the marker, because a
generated file that looks hand-authored invites an edit that the next regeneration silently
destroys.

## Naming

`<freeze_id>.json`, where `freeze_id` is the digest of the freeze's identity fields.

## What a freeze pins

In the identity: freeze format version, `dataset_id`, `dataset_version`, `manifest_path`,
`manifest_hash`, `record_schema_version`, `record_counts`, `artifact_role`, and every
artifact's path, SHA-256 and size.

Recorded but outside the identity: `created_at`, `repository_revision`, `repository_dirty`,
`frozen_by_command`. These describe the freeze *event*, not the data — which is what lets an
unchanged freeze be regenerated as the same freeze.

## Immutability

`engramfold freeze dataset` refuses to overwrite a freeze whose identity has changed. A
genuine change produces a new freeze id and a new file; the old one stays checkable. See
[`docs/ERRATA_POLICY.md`](../../docs/ERRATA_POLICY.md).

## Verification

```bash
engramfold verify-freeze                 # every freeze
engramfold verify-freeze <freeze_id>     # one, and finding none is a failure
```

Verification fails when a referenced file disappears, its contents change, its size changes,
the record count moves, the manifest's contents change, the manifest is now a different
dataset, the freeze is malformed, or the `freeze_id` no longer matches the document's own
contents.

## Currently empty

Nothing has been frozen, because no dataset exists. See
[`registry/populations.yaml`](../../registry/populations.yaml) for the written authorisation.