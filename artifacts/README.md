# `artifacts/` — ownership and purpose

**Owner:** whoever produces a canonical result.
**Status:** generated manifests; no payloads, ever.

## What lives here

Only `manifests/` — one JSON document per canonical artifact, recording what produced it,
from what, under which code, configuration and environment, with its SHA-256.

**Large payloads are never canonical repository state.** Model weights, corpora and derived
shards are excluded by `.gitignore`. The manifest pins them by digest instead, so the
artifact remains identifiable after the bytes have moved or been deleted, and verification
reports a missing artifact as missing rather than passing quietly.

## Identity: ids and hashes, never filenames

Three distinct things, deliberately not merged:

| Field | Meaning |
|---|---|
| `artifact_id` | A stable human-readable identifier. References between documents use this, so a reference survives the file being moved. |
| `artifact_sha256` | The digest of the artifact's bytes. This is what makes the artifact itself identifiable, independently of where it lives. |
| `artifact_identity` | The identity of the manifest's semantic content. |

A filename is never an identity. Two artifacts with the same basename are different
artifacts; the same artifact at two paths is one artifact. Anything keyed on a path gets both
of those backwards.

## Every artifact answers the same questions

* what created me? — `created_by_experiment`, `command`
* from which code? — `code_revision`, a full provenance record, not a bare commit
* from which frozen inputs? — `input_dataset_freezes`
* from which model? — `model_id`, `model_revision`, `model_hashes`
* under which configuration? — `configuration_hash`
* in which environment? — `environment_manifest_hash`
* what is my SHA-256? — `artifact_sha256`
* when? — `created_at`

## Storage kinds

| `storage_kind` | Verifiability |
|---|---|
| `repository` | The bytes are in this repository and their digest is re-derived on every validation run. |
| `external` | `artifact_uri` names where they are. Reported as **unverifiable with a reason**; `bytes_verified` stays `false`. |
| `remote` | Same, for a fetched artifact. |

An artifact whose bytes cannot be re-read has not been checked. The verification object
distinguishes `ok` (well-formed manifest) from `verified` (bytes actually re-read and
matched), so the two can never be conflated in a report.

## Currently empty

Nothing has been produced. See [`registry/populations.yaml`](../registry/populations.yaml).