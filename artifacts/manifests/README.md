# `artifacts/manifests/` — artifact manifests

**Class:** Generated. **Owner:** the tool that produced the artifact. **Never hand-edited.**

One JSON document per canonical artifact. Its job is to make an artifact identifiable and
checkable after the fact, without its producer present.

## Naming

`<artifact_id>.json`, where `artifact_id` matches `[a-z0-9][a-z0-9._-]*` and is referenced by
`registry/artifacts.yaml`.

## Required fields

`artifact_manifest_version`, `artifact_id`, `artifact_sha256`, `storage_kind`,
`created_by_experiment`, `command`, `code_revision`, `environment_manifest_hash`,
`input_dataset_freezes`, `created_at`.

`code_revision` must be a full provenance record — commit, branch, dirty flag — not a bare
commit, because a bare commit cannot distinguish a clean checkout from one with uncommitted
edits. A dirty tree must carry a `dirty_override_reason` here, so the fact is visible in the
manifest itself and not only in the run logs.

## Inputs and outputs are pinned by digest

`input_dataset_freezes` is a list of freeze ids. A `repository` artifact is pinned by
`artifact_sha256` and re-verified on every validation run. An `external` or `remote` artifact
is reported as unverifiable with the reason, never as verified.

## Currently empty

Nothing has been produced. See [`artifacts/README.md`](../README.md).