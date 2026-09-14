# `experiments/freezes/` — experiment definition freezes

**Class:** Generated. **Owner:** `engramfold freeze experiment`. **Never hand-edited.**

One freeze per experiment, written when the definition is **fixed** and before any compute is
spent. Its purpose is to make "has the definition changed since execution?" answerable, so
that a pre-registered rule cannot quietly become a rule chosen to fit an answer.

## Naming

`<experiment_id>.json`. Keyed by experiment rather than by freeze id, because the question is
"what was this experiment's definition?" — one answer per experiment. A second freeze for the
same experiment is a rewrite of frozen history and is **refused**, not filed alongside.

## What it pins

`definition_hash` (the whole manifest), `configuration_hash` (the parameters specifically, so
a change to parameters is distinguishable from a change to prose), `dataset_freezes` (the
inputs pinned at freeze time), `status_at_freeze`, and `code_revision_at_freeze`.

## Verification

Verification recomputes each pinned field against the manifest as it is now and reports the
**specific field that moved**, not merely that something did. It also reports how many fields
it compared, so a comparison function that silently compared nothing cannot return "no drift"
and look like success.

A status advance is expected, and is still reported: it must be recorded as a lifecycle event
rather than discovered by comparing two files.

## Currently empty

No definition has been frozen, because none exists. See
[`registry/populations.yaml`](../../registry/populations.yaml).