# Architecture: directories, ownership, purpose

Every directory in this repository has an owner and a reason to exist. None is present for
symmetry. This file is the authority on that: `engramfold validate` fails if a canonical
directory is missing from this document, so adding a directory without documenting its
ownership is not possible.

## Ownership classes

| Class | Meaning | Who may edit it |
|---|---|---|
| **Source** | Hand-authored canonical state. The research record itself. | Humans, by pull request. |
| **Generated** | Produced by a tool. Never hand-edited; regenerate instead. | Tools only. |
| **Substrate** | The integrity code. | Humans, by pull request. |
| **Working** | Provides a place for output. Not canonical. | Anything. |

Generated documents are marked. A document written by a tool must carry a `generated_by`
field naming the function that wrote it; a hand-authored document must not. Validation
enforces both directions — see `docs/VALIDATION.md#generated-versus-source`.

## Directories

### `src/engramfold` — Substrate

The integrity substrate. Owner: the maintainers of the substrate.

| Module | Owns |
|---|---|
| `hashing.py` | The **single** canonical hashing implementation. No other module may import a hash library; `engramfold validate` enforces this. |
| `schemas.py` | The closed registry of schema versions, and the rule that an unknown version fails. |
| `documents.py` | Document loading, keeping `ABSENT` apart from `MALFORMED`/`UNREADABLE`/`UNKNOWN_SCHEMA`. |
| `errors.py` | Error types, split by whether the failure is absence or corruption. |
| `validation/` | Execution accounting, the gate contract, and census construction. |
| `provenance/` | Git state and environment capture, with secret redaction. |
| `datasets/` | Dataset manifest schema, and the freeze system that pins bytes. |
| `experiments/` | Experiment manifest schema, and definition freezes that detect drift. |
| `artifacts/` | Artifact manifest schema and byte verification. |
| `registry/` | Registry loading, reference resolution, and the eight validation gates. |
| `cli/` | The command line. |
| `preflight.py` | The canonical repository health gate. |

### `registry` — Source

The index of the canonical research record: records, references between them, canonical
claims with their evidence, and the population policy that declares which populations may
legitimately be empty. See `registry/README.md`.

### `datasets/manifests` — Source

One JSON document per dataset, hand-authored by the adapter author. Records the upstream
source pinned to an immutable revision, the licence, the processing that produced the
records, and the digests of the result.

### `datasets/freezes` — Generated

Freeze documents. A freeze states that a specific research stage consumed specific bytes
under a specific manifest. Written only by `engramfold freeze dataset`; never hand-edited.

### `experiments/manifests` — Source

One JSON document per experiment definition. Hand-authored. Records the configuration, the
dataset freezes it consumes, the model revision, and the code and environment provenance
that a run would need.

### `experiments/freezes` — Generated

Definition freezes. One per experiment, written when the definition is fixed and before
compute is spent. Verification recomputes the definition hash, so editing a frozen
definition after seeing results is detectable.

### `artifacts/manifests` — Generated

Artifact manifests: what produced an artifact, from which frozen inputs, under which code,
configuration and environment, with what SHA-256. Large payloads are **not** canonical
state and are never committed; `.gitignore` excludes them, and the manifest pins them by
digest instead. See `artifacts/README.md`.

### `provenance/records` — Generated

Recorded provenance documents: commit, branch, dirty state, dirty diff digest, and the
environment manifest. Written by `engramfold provenance --write`.

### `reports` — Source and Generated

The reporting surface. A report interprets artifacts and must cite them by id; the
canonical artifact wins over any prose that disagrees with it. Generated figures belong
here and carry the generated marker; prose does not.

### `docs` — Source

Documentation. `METHODOLOGY.md` covers research integrity only — the research design is
explicitly not yet frozen.

### `tests` — Substrate

The adversarial test suite. Half of it exists to prove things *ran* rather than that they
exited zero. See `tests/README.md`.

### `configs` — Source

Hand-authored configuration. Nothing here defines a research decision yet.

### `scripts` — Substrate

Small operational scripts that are not part of the installed package.

### `.github/workflows` — Substrate

Continuous integration. Mirrors `engramfold-preflight`; the local gate is canonical and CI
is the same gate in a different place.

## The rule about directories

A directory that holds canonical state must have a `README.md` stating its ownership and
purpose, and must be named in this file. Both are enforced by the `substrate` gate. The
point is that the layout cannot drift away from its documentation: a new directory is a
documented act or it is a validation failure.