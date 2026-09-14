# Provenance

Provenance answers four questions that must never be guessed at:

1. **Which code** produced this? (`source_revision`)
2. **Which machine and environment** produced it? (`environment_manifest`)
3. **Which bytes** did it consume? (`dataset_freeze`)
4. **Which command** ran? (`command`, `frozen_by_command`)

Each has a separate record type and a separate field. They are not merged, because a
question that can be answered two ways will eventually be answered two ways.

## Source revision

### Why `git HEAD` is not enough

A checkout at commit `abc123` with four tracked files modified in the working tree is *not*
the state `abc123` describes. An artifact produced from it cannot be attributed to
`abc123`. Recording only the commit hash therefore does not merely lose information — it
manufactures a false attribution, which is worse than recording nothing, because it looks
complete.

So `engramfold provenance` records, together:

| Field | Meaning |
|---|---|
| `git_commit` | The commit the checkout is at. |
| `git_branch` | The branch, or `<detached HEAD>`. |
| `git_dirty` | Whether any **tracked** file differs from that commit. `null` means *unknown*, never *clean*. |
| `dirty_file_list` | Which tracked paths differ. |
| `dirty_diff_hash` | A digest over the modification itself: for each dirty file, the digest of the committed blob and of the working file. |
| `untracked_file_list` | Untracked paths, recorded but not part of the cleanliness verdict. |
| `last_commit_affecting_relevant_file` | The most recent commit touching the paths this artifact depends on. |
| `last_commit_timestamp` | When that commit was made. |

### Untracked files do not make a tree dirty

Every run creates untracked outputs — its own manifests, logs and artifacts. If
`git status` as a whole decided cleanliness, every successful run would report as dirty and
"produced from a clean checkout" would be unsatisfiable. What matters is whether the tracked
code and configuration matched a known commit. Untracked paths are recorded separately, so
the fact is visible without being conflated.

### Failing closed

When git cannot be queried, `git_dirty` is `null` — unknown. An unreadable repository is
never presented as clean:

- `git_dirty: null` → `engramfold validate` **blocks**, exit code `3`;
- `git_dirty: true` → a **warning** during development, so the gate is not switched off;
- `git_dirty: true` on an artifact → an **error** unless a written override is recorded.

### Overrides never hide the dirty state

A dirty-tree override is permitted for deliberately exploratory work. It does **not** clear
the flag. The record always contains the true `git_dirty`, the full `dirty_file_list` and the
`dirty_diff_hash`; the override is recorded *next to* them, and an artifact manifest whose
`code_revision.git_dirty` is `true` must carry a `dirty_override_reason` or validation fails.
A result produced from dirty code therefore permanently contains enough information to
establish that fact.

### Why committed bytes, not working-tree bytes

Digests of evidence are taken over the **committed blob**, read from git's object store, not
over the file on disk. This matters more than it looks. With `core.autocrlf` active — which
this repository's `.gitattributes` handles by forcing `eol=lf` on every hashed text format —
a file checked out on Windows has CRLF where the blob has LF. A digest over the file on disk
would then be a claim about the verifier's operating system rather than about the artifact.
Hashing the blob makes the digest platform-independent.

It also means a provenance claim can only rest on a file that is actually part of the
research record. A digest computed over a gitignored build directory pins nothing that a
clone will contain.

## Environment

`engramfold provenance` captures, where available:

| Group | Fields |
|---|---|
| OS | platform, system, release, kernel, version, machine, architecture |
| Interpreter | version, implementation, executable name |
| Packages | every installed distribution and version, sorted; plus a core subset |
| Accelerators | GPU model, memory, driver, compute capability (per device); CUDA version; ROCm version |
| Host | CPU model, RAM in bytes, container image and digest, coarse hostname class |
| Environment | relevant variables only, with secrets withheld |

### Redaction is by allowlist, not by scrub

`os.environ` routinely contains API tokens, cloud credentials and session cookies.
Recording it wholesale and then trying to scrub it is a losing game, because the scrub has
to anticipate every secret. Instead:

1. only variables matching a documented relevance pattern (`CUDA_*`, `OMP_*`, `HF_*`,
   `SLURM_*`, `ENGRAMFOLD_*`, …) are considered at all;
2. a variable whose **name** matches a secret marker (`TOKEN`, `SECRET`, `PASSWORD`,
   `CREDENTIAL`, `API_KEY`, `AUTH`, `SESSION`, `COOKIE`, …) is withheld entirely;
3. a value that **looks** like a credential — a URL with inline credentials, or a long
   opaque token — is withheld even under an innocent name;
4. the **names** of withheld variables are recorded, so the record shows that something was
   withheld without showing what.

An unlisted variable is not recorded. The default for anything unrecognised is silence.

### Hardware differences are recorded, not treated as failures

EngramFold will run on heterogeneous hardware. A different GPU model is a fact about a run,
not evidence that the run is wrong. `compare_environments` therefore *classifies*
differences as **material** (interpreter version, installed packages) or **informational**
(CPU, GPU, CUDA, ROCm, RAM, container, kernel) and reports both without returning a verdict.

Whether a difference invalidates a comparison is a research judgment. This module's job is
to make the difference visible, not to make the judgment.

## Dataset manifests

The answer to "where did this input come from?" is the dataset manifest, and it is the only
permitted answer. Required fields: both version fields, `dataset_id`, `dataset_version`,
`source_type`, `split`, `license`, `record_count`, `adapter_version`,
`deduplication_policy`, `contamination_policy`.

Conditionally required, by `source_type`:

| `source_type` | What must be pinned |
|---|---|
| `huggingface` | `source_repository`, `source_revision` (full 40-character commit), `source_files` |
| `local` | `source_files` and `source_file_hashes` |
| `remote_url` | `source_url`, `retrieved_at`, `source_file_hashes` |
| `synthetic` | a `synthetic` block: generator model, model revision, generation config hash, prompt-template version, generation code revision, raw generation artifact |

The conditions encode the claim each source type makes. A manifest cannot say "this came
from a pinned upstream release" and then not pin one.

Three further rules close specific holes:

- **Processing must be reproducible.** If `processed_files` is non-empty, the manifest must
  also record `processing_command`, `processing_code_revision` (an immutable commit),
  `processing_config_hash` and `random_seed`. Without all four, the processed bytes exist but
  the transformation that made them is unrecoverable.
- **An empty dataset must be declared.** `record_count: 0` is usually a filter that matched
  nothing, so it requires an `empty_dataset_rationale`. A count of zero can no longer be
  discovered after a training run has consumed nothing.
- **Unknown licences must be declared as unknown.** `license: "UNKNOWN"` is permitted, but
  only with a written rationale, so "we did not check" is distinguishable from "the licence
  is permissive".

### Synthetic data: schema only

The `synthetic` source type is **not implemented**. No generation code exists in this
repository. The schema exists so that when generated data is eventually introduced it cannot
enter the canonical record without identifying its generator, and `random_seed` is required
as soon as the generation configuration implies sampling.

## Citing provenance in prose

Do not restate provenance from memory. A lineage, reference, launch commit or dirty flag
written in prose must be copied from the record or omitted. The canonical record wins over
any prose that disagrees with it.