# `provenance/records/` — recorded provenance documents

**Class:** Generated. **Owner:** `engramfold provenance --write`. **Never hand-edited.**

One JSON document per captured provenance event. Each records the commit, branch, dirty flag,
the modified tracked file list, a digest of the modification, the untracked file list, the
last commit touching the relevant paths, and (unless `--git-only` was used) the environment
manifest.

## Naming

Free-form; a conventional choice is `<experiment_id>.<timestamp>.json` or
`<artifact_id>.json`. Validation discovers `provenance/records/*.json` and requires each to
carry `provenance_record_version`.

## The one rule that matters

A record must never present an unknowable state as a knowable one. `git_dirty: null` means the
state could not be determined, and validation treats a record carrying it as an **error**. A
dirty tree is a fact to record, not a fact to hide: an override is stored *next to* the dirty
flag, never instead of it.

## Currently empty

No artifact has been produced, so no record is required. See
[`registry/populations.yaml`](../../registry/populations.yaml).