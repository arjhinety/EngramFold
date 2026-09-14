# Reproducibility and determinism

## What determinism means here

Determinism in this repository is a property of **identity**, not of timing. The rule:

> Two computations over the same content produce the same identity. Facts that change for
> reasons unrelated to the content — timestamps, hostnames, absolute paths — are recorded but
> excluded from identity.

Applying that rule consistently is what makes "has this changed?" answerable. If a timestamp
participated in a content identity, every regeneration of identical data would be a new
dataset, and the question would have no answer.

## The identity kinds

These are distinct fields with distinct meanings. Overloading one field with two meanings is
forbidden, because a reader could then not tell which identity a check had just verified.

| Field | Computed over | Changes when |
|---|---|---|
| `content_hash` | the exact bytes of one file | the file's bytes change |
| `directory_hash` | an ordered census of covered paths, sizes and digests | a covered file is added, removed or changed |
| `manifest_hash` | a manifest's semantic content, volatile fields removed | a meaningful field changes |
| `configuration_hash` | a configuration document, volatile fields removed | a configuration value changes |
| `artifact_hash` | the bytes of a produced artifact | the artifact's bytes change |
| `freeze_id` | an explicit list of freeze identity fields | the pinned dataset or manifest changes |
| `source_revision` | *nothing* — it is a git commit pointer | the checkout moves |

`source_revision` is deliberately not a digest. A revision is a pointer into history; storing
it in a hash-shaped field would be a category error, and it would make dirty-tree state
invisible.

## What is excluded from identity, and why

Excluded recursively by exact field name, at every depth:

**Timestamps** — `created_at`, `updated_at`, `retrieved_at`, `captured_at`, `generated_at`,
`recorded_at`, `started_at`, `completed_at`, `frozen_at`, `freeze_timestamp`, `timestamp`.

**Machine-local** — `hostname`, `hostname_class`, `absolute_path`, `resolved_path`,
`local_path`, `root_path`, `machine`.

Over-removal is possible in principle: a data payload containing a literal key called
`timestamp` would lose that field from the identity. That is why the list is short, explicit
and documented rather than heuristic, and why identity functions return or expose the payload
they hashed — a caller can always inspect what was covered.

For freezes, the separation is explicit rather than by name. A freeze declares its identity
fields, and everything else it records is provenance for the freeze *event*:

| In the identity | Excluded from the identity |
|---|---|
| freeze format version | `freeze_id` |
| dataset id and version | `created_at` |
| manifest path and manifest hash | `repository_revision` |
| record schema version and counts | `repository_dirty` |
| artifact role and artifact digests | `frozen_by_command` |

So two freezes of identical content are the same freeze whenever they were taken, while each
still records when it happened and which commit was checked out.

## Determinism guarantees, and how each is tested

| Guarantee | Test |
|---|---|
| Hashing the same bytes twice agrees | `tests/test_hashing.py`, and live in the `substrate` gate |
| A one-byte change changes the digest | `tests/test_hashing.py`, and live in the `substrate` gate |
| JSON key order does not change identity | `tests/test_hashing.py`, `tests/test_determinism.py` |
| Reformatting a manifest does not change its `manifest_hash` | `tests/test_determinism.py` |
| Identical trees at different paths hash identically | `tests/test_hashing.py`, and live in the `substrate` gate |
| Directory hashing covers a stable, sorted population | `tests/test_hashing.py` |
| Directory exclusions are explicit and recorded | `tests/test_hashing.py` |
| A manifest serialises to identical bytes twice | `tests/test_determinism.py` |
| Registry ordering is stable | `tests/test_determinism.py` |
| Regenerating a freeze is idempotent and byte-identical | `tests/test_determinism.py` |
| The same dataset described twice has one identity | `tests/test_determinism.py` |
| The same experiment described twice has one identity | `tests/test_determinism.py` |
| Environment capture is stable across two captures | `tests/test_determinism.py` |
| Artifact metadata is byte-stable | `tests/test_determinism.py` |

## Streaming

`content_hash` reads in fixed chunks (`1 << 20` bytes), so a 200 GB artifact hashes in
constant memory. The chunk size is part of the module, not of any call site: two call sites
choosing different sizes is harmless for the digest, but it is the first symptom of a second
implementation growing. `tests/test_hashing.py` asserts the streamed path and a single-shot
hash of the same payload agree.

## Directory hashing rules

- Entries sort by normalised relative POSIX path. The ordering is total and explicit.
- Paths are repository-relative and forward-slashed, so a digest is comparable across
  machines. Absolute location is never part of the digest.
- Exclusions are explicit and reported in the digest's `excluded` list, so a reader can see
  what was *not* covered. The default set is `.git`, VCS metadata, Python caches, virtual
  environments, `.cache`, `.scratch`, `.DS_Store`, `Thumbs.db`.
- Symlinks are not followed and not covered, and are recorded as excluded. Following them
  would make the digest depend on whatever the link resolved to at hash time.
- Empty directories are not covered. Git does not track them, so including them would make a
  digest taken on a working tree differ from the same tree freshly cloned — a digest of the
  checkout rather than of the content.

## Deliberate non-determinism

Some facts are genuinely not reproducible and are recorded rather than suppressed:

- **Timestamps.** Recorded in `created_at`, `captured_at`, `frozen_at`; excluded from every
  identity.
- **Hardware.** GPU model, driver version, RAM and CPU are recorded and classified as
  informational differences. EngramFold will run on heterogeneous hardware.
- **Container tags.** A tag is mutable; a digest is not. `pinned_by_digest` records which of
  the two was all that was available.

The rule is that anything that varies is either excluded from identity or recorded explicitly
— never silently dropped, and never allowed to invalidate a content identity.

## Reproducibility claims

A reproducibility claim is only permitted for a run that has a matching repeat. A
preservation claim is only permitted for paths that exist in the repository. Both are
recorded conventions rather than gates, because neither can be decided mechanically — but a
claim made without them is a claim this repository's methodology forbids.

## Command-level determinism

Every command that produces output is deterministic given its inputs:

- `engramfold hash` and `engramfold hash --tree` are pure functions of the filesystem.
- Freeze generation accepts an explicit `--created-at` / `--frozen-at`, so byte-for-byte
  identical output is achievable. Re-running without one leaves an existing freeze untouched
  rather than rewriting it.
- `engramfold validate --json`, `engramfold provenance --json` and every other `--json` form
  emit canonical JSON: key-sorted, compact, stable.

### One measured qualification, stated rather than glossed

A dataset freeze regenerates byte-identically once `--created-at` is fixed: the document records
the manifest, the artifacts and the passed-in provenance, and nothing else.

A *definition* freeze embeds a provenance record of the checkout it was taken from, and that
record includes the list of untracked files. Writing the freeze creates a file, so regenerating
the document afterwards necessarily differs in exactly one place — `untracked_file_list` — while
its `freeze_id` is unchanged, its recorded file is left untouched, and the difference is confined
to an observation about the working tree rather than a fact about the definition. This was found
by running `engramfold freeze experiment` twice and diffing the two documents, not by reading the
code; `tests/test_cli.py` pins the difference to that one field, and
`tests/test_determinism.py` asserts that the *identity* is stable across the two builds.

The volatile capture timestamp inside that embedded record was removed for the same reason: the
instant of the capture is already recorded in `frozen_at`, and a second, unfixable wall-clock
input made the earlier claim in this section unreachable.