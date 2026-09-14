# `provenance/` — ownership and purpose

**Owner:** `engramfold provenance`.
**Status:** `records/` is generated.

## What lives here

Recorded provenance documents. Each answers: which commit, on which branch, with what dirty
state, and in what environment.

| Path | Class | Written by |
|---|---|---|
| `records/` | Generated | `engramfold provenance --write <path>` |

## Why this is a separate directory

Provenance is recorded, not inferred. A result that cannot name its commit and its environment
is not reproducible evidence, and reconstructing either afterwards is guesswork. Writing them
down at the moment of production is the only cheap moment to do it.

## Not a source of truth about the past

A provenance record describes the checkout **at the moment it was captured**. Validation
re-captures the current checkout and validates the recorded records against
`PROVENANCE_RECORD_VERSION`, but it does not claim a historical record still matches the
current tree — that would be a category error. What is checked is that the record is
well-formed, that its dirty state is not hidden, and that an unknowable state (`git_dirty:
null`) is treated as a failure rather than as evidence.

## Redaction

Environment capture never records secret-shaped variables, and records only variables matching
a documented relevance pattern. The *names* of withheld variables are recorded, so the record
shows that something was withheld without showing what. See
[`docs/PROVENANCE.md`](../docs/PROVENANCE.md#redaction-is-by-allowlist-not-by-scrub).

## Currently empty

No artifact has been produced, so no provenance record is required. The `provenance` gate still
captures and validates the current checkout's git provenance on every run, which is why it is
never vacuous even with zero recorded records.