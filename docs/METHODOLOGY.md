# Methodology: research integrity

## Scope of this document

This document describes how EngramFold intends to keep itself honest. It does **not**
describe the research method, and it does not define the algorithm.

> **NOT YET FROZEN.** The scientific hypothesis, the relevant literature, the candidate
> pretrained MoE families, the operational meaning of "expert function", the consolidation
> baselines, the Engram integration, the preservation metrics, the ablation design, the
> compression frontier and the research questions are all **undecided**. They are the
> subject of a later phase, which begins only once this substrate is trusted.

Nothing in this document should be read as a prediction about the eventual design, and no
file in this repository records a research parameter.

## The integrity principles

These are the commitments the substrate exists to enforce. Each is paired with the
mechanism that makes it checkable rather than aspirational.

### 1. Identity is content, never location

Two artifacts are the same artifact when their bytes are the same bytes, not when they sit
at the same path. A digest is therefore taken over content; a recorded path is a locator,
never an identity.

*Mechanism:* one canonical hashing implementation (`engramfold/hashing.py`), which
validation re-exercises live on every run, and which is the only module permitted to import
a hash library.

### 2. Volatile facts are recorded but never identify

When something happened, and on which host, are facts worth recording and worthless as
identity. If a timestamp participated in a content identity, every regeneration of the same
data would be a different dataset, and "has this changed?" would become unanswerable.

*Mechanism:* identities are computed over an explicit field list, and volatile and
machine-local field names are removed recursively before hashing. See
`docs/REPRODUCIBILITY.md`.

### 3. A pointer to mutable state pins nothing

A branch, a tag, a URL without a revision, a repository path: each can hold different bytes
tomorrow. A provenance claim resting on one of them has recorded a hope, not an anchor.

*Mechanism:* a `huggingface` source must pin a full 40-character commit; a local source must
record file digests; a claim about a model must name an immutable revision. Short revisions
and branch names are rejected outright.

### 4. Absence and corruption are different facts

"The file is not there" and "the file is there and I could not use it" must never collapse
into the same handling. Collapsing them is how a malformed manifest becomes a silent skip.

*Mechanism:* `engramfold/documents.py` returns a state — `PRESENT`, `ABSENT`, `UNREADABLE`,
`MALFORMED`, `UNKNOWN_SCHEMA` — and only `ABSENT` is ever eligible to be treated as
"nothing to do".

### 5. A check that ran and found nothing must be distinguishable from a check that did not run

This is the most important principle here, and the one most often violated in practice.

*Mechanism:* every gate reports a census (targets discovered, targets checked, assertions
executed, passed, failed, skipped), the census is checked for internal consistency, and a
gate that executed zero assertions fails unless it declares in writing that executing none
was the legitimate outcome. See `docs/VALIDATION.md`.

### 6. Unknown schema versions fail

Interpreting a document written for a newer format with an older reader is how a field
silently changes meaning. Refusing is always cheaper than a wrong result.

*Mechanism:* a closed registry of document kinds and versions; a missing version field is an
error, not a default.

### 7. Frozen means frozen

Once an artifact or a report is formally frozen, a discovered mistake does not license
rewriting it. The correction is recorded alongside it.

*Mechanism:* freeze writers refuse to overwrite a freeze whose identity has changed, and the
errata ledger in `ERRATA.md` records corrections with the evidence they rest on. See
`docs/ERRATA_POLICY.md`.

### 8. Prose is not evidence

A result is supported by a machine-readable artifact, not by a sentence describing it.
Where prose and the canonical artifact disagree, the artifact wins until an explicit
correction is made.

*Mechanism:* `registry/claims.yaml` requires a `SUPPORTED` claim to cite at least one
resolvable, machine-readable reference, and prose is not among the permitted evidence kinds.

### 9. A dirty tree is recorded, never hidden

An artifact produced from uncommitted code cannot be attributed to a commit. The honest
response is to record that fact permanently, not to refuse to look.

*Mechanism:* provenance records commit, branch, dirty flag, the modified file list and a
digest of the modification. An override is permitted but never clears the dirty flag — it is
recorded next to it. See `docs/PROVENANCE.md#source-revision`.

### 10. Hear both directions

A gate that fails because it could not reach the network is lying in the opposite direction
from one that passes while doing nothing. So "could not evaluate" is its own verdict.

*Mechanism:* `BLOCKED_*` statuses, which are never `PASS` and produce a non-zero exit code.

## How a result will be required to arrive

Not yet used, because there are no results. Recorded here so that the requirements are fixed
before they can be shaped by an outcome:

1. The dataset manifest exists and is valid, and a freeze pins its bytes.
2. The experiment definition is frozen before any compute is spent, so drift is detectable.
3. The run records its code revision and environment, with dirty state visible.
4. Artifacts are recorded with a stable id, a content digest and their inputs.
5. Any claim rests on those artifacts by id, not on prose.
6. Every one of those steps is checked by a gate, and the gate's census shows it executed.

## Pre-registration checklist

The following must be committed **before** the first GPU hour is spent on a study. It is
recorded now so that it cannot be assembled afterwards to fit a result.

- [ ] Decision rule and gate version committed before any candidate is scored
- [ ] Promotion partition covers every response mode the model is deployed for
- [ ] A reference rerun planned, so a gate margin can be compared with rerun noise
- [ ] Training-data fingerprint recorded, and any later audit reads *that* fingerprint
- [ ] Every factor that differs between arms listed; exposure logging enabled
- [ ] Seeds and intervals planned, or the single-seed limitation stated in advance
- [ ] Regressions, final checkpoints and any selection on the evaluation set reported
- [ ] Every table states its partition and its `n`, and all rows use it
- [ ] Causal language restricted to designs that vary one factor

## Reporting rules

- Every number is generated or asserted by a test against a canonical artifact. No figure is
  retyped from another document.
- One quantity has one value everywhere it appears.
- A caveat travels with the number it qualifies, on every surface, including cards.
- A correction is applied to every copy, and an independent audit of claims precedes a
  freeze.
- Status words change in the same change as the status.

## What is deliberately not decided

Recorded as an explicit non-list, because the absence of these decisions is a feature of
Phase 0 rather than an omission:

- no target MoE model family;
- no operational definition of "expert function";
- no merging, pruning or clustering method;
- no preference between weight-space and function-space consolidation;
- no target expert count;
- no benchmark selection;
- no preservation metric;
- no ablation grid;
- no compression frontier;
- no statement about novelty or about prior work.