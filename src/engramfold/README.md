# `src/engramfold/` — the integrity substrate

**Class:** Substrate. **Owner:** the maintainers of the substrate.

## Module map

| Module | Owns |
|---|---|
| `hashing.py` | The **single** canonical hashing implementation. No other module may import a hash library; the `substrate` gate enforces this. |
| `schemas.py` | The closed registry of document kinds and versions, and the rule that an unknown version fails safely. |
| `documents.py` | Document loading, keeping `ABSENT` apart from `MALFORMED`, `UNREADABLE` and `UNKNOWN_SCHEMA`. |
| `errors.py` | Error types, split by whether the failure is absence or corruption. |
| `validation/` | Execution accounting, the gate contract, and census construction. |
| `provenance/` | Git state and environment capture, with secret redaction. |
| `datasets/` | Dataset manifest schema and the freeze system. |
| `experiments/` | Experiment manifest schema and definition freezes. |
| `artifacts/` | Artifact manifest schema and byte verification. |
| `registry/` | Registry loading, reference resolution, and the eight validation gates. |
| `cli/` | The command line. |
| `preflight.py` | The canonical repository health gate. |

## The three invariants this package is built around

1. **One hashing implementation.** Two hashing routines that disagree by one byte of
   normalisation produce two incompatible notions of identity, and every downstream freeze
   becomes unverifiable for a reason nobody can see. Enforced by the `substrate` gate, which
   scans the tree for a second import of a hash library.

2. **Absence is not corruption.** `documents.load_document` returns a state, and only
   `ABSENT` may ever be treated as "nothing to do". A malformed or version-incompatible
   document is always an error, so a broken manifest can never become a silent skip.

3. **A gate must prove it ran.** Every gate reports how many assertions it executed, and zero
   is a failure unless the gate declares in writing that executing none was legitimate — and
   only an `OPTIONAL` gate may make that declaration. See
   [`docs/VALIDATION.md`](../../docs/VALIDATION.md).

## No research code

There is deliberately nothing here that trains, merges experts, prunes, clusters, downloads
weights or generates synthetic data. This package is the substrate the research will run on,
not the research.