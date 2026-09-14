# Errata policy

## The principle

Once an artifact or a report is formally **frozen**, a discovered mistake does not license
rewriting it. The frozen bytes stay exactly as they were, and the correction is recorded
beside them:

```text
frozen original  +  ERRATA.md  +  a new corrected artifact or report where necessary
```

Editing a frozen artifact to fix it destroys the only thing that made it useful: the ability
to check what was actually claimed at the time. A reader who cannot tell what was originally
said cannot tell whether a later summary is a correction or a rewrite. So frozen history is
append-only, and `ERRATA.md` is where the corrections live.

A freeze writer enforces one half of this mechanically: `write_freeze` and
`write_experiment_freeze` **refuse** to overwrite a freeze whose identity has changed.
Replacing a freeze is a rewrite of frozen history and raises rather than proceeding. A genuine
change produces a new freeze id and a new file, and the old one remains checkable.

## What counts as frozen

| Frozen | Not frozen |
|---|---|
| A dataset freeze document (`datasets/freezes/*.json`) | A dataset manifest before it is frozen |
| An experiment definition freeze (`experiments/freezes/*.json`) | An experiment manifest in `DRAFT` |
| An artifact recorded in `artifacts/manifests/` and referenced by a report | Working notes |
| A report listed in `registry/reports.yaml` with a recorded hash | Documentation prose |
| An entry in `ERRATA.md` | — |

An artifact is frozen when a report or a claim cites it. Until then it may be regenerated and
the freeze re-taken; after that, corrections go through this policy.

## Classification

The classification decides whether a new artifact is required. Misclassifying downward — a
numerical correction treated as a typo — is the failure mode this table exists to prevent.

| Class | Definition | New artifact? | New freeze? | Erratum required? |
|---|---|---|---|---|
| **Typo** | A spelling or formatting error in prose that changes no number, no identifier and no meaning. | No | No | Recommended |
| **Metadata** | A wrong path, author, date, licence string or reference; no measured value changes. | No | Only if a frozen field is affected | Yes |
| **Numerical** | A number is wrong, or a number is right but described as something it is not. | **Yes** if a canonical artifact recorded it | Yes if an artifact changed | Yes |
| **Methodological** | The procedure differed from what was recorded, or the recorded procedure does not support the stated inference. | Yes | Yes | Yes |
| **Invalidated** | The result does not hold: it cannot be reproduced, its inputs were wrong, or the analysis was unsound. | Yes, or the claim is withdrawn | Yes | Yes |

Two rules that are easy to get wrong:

- **A number in the right place with the wrong meaning is a numerical correction**, not a
  typo. "0.7638 recall" when the figure is accuracy is a numerical error even though the
  digits are correct.
- **"Cannot reproduce" is not established by one failed repeat.** One repeat that disagrees
  supports "not reproduced on this attempt", not "not reproducible". The distinction is part
  of the classification, not a stylistic preference.

## Required content of an entry

Every entry in `ERRATA.md` must identify all of the following. An entry missing any of them is
incomplete.

| Field | What it must contain |
|---|---|
| **Affected artifact** | The path, quoted exactly. |
| **Affected hash** | The SHA-256 of the artifact as frozen, so the correction names an immutable version. |
| **Original claim** | What the artifact asserted, cited by line or table row. |
| **Corrected claim** | What is true instead. |
| **Reason** | Why the original was wrong — a transcription slip, a wrong field, a confounded design. |
| **Evidence** | Artifact ids, experiment ids, freeze ids or metric file paths that establish the correction. |
| **Date** | ISO-8601. |
| **Correcting commit** | The commit that added the entry. |
| **Classification** | One of the five classes above. |

## Rules for writing an entry

1. **The entry never edits the frozen file.** If the frozen file is wrong in a way that makes
   it misleading on its own, that is what the entry is for.
2. **Every number in the entry is recomputed from the evidence**, never copied from another
   report. A correction that restates a second document's figure inherits that document's
   errors.
3. **If the evidence no longer exists, say so.** An entry whose correction cannot be
   re-derived — because per-example data was never committed, or an artifact was deleted —
   must state that explicitly and mark the claim unverifiable. Silently restating the claim
   with confidence is the worst available option.
4. **A correction is applied to every copy.** Before an entry is closed, search the
   repository, the documentation and any external surface for the same figure or phrase, and
   either fix it or add an erratum for it. A correction that reaches one of four surfaces has
   not been applied.
5. **Supersede, never delete.** A later entry may supersede an earlier one. Both stay. The
   ledger is a record of what was believed and when, not a tidy summary of the current view.
6. **Withdraw explicitly.** If a claim cannot be supported, say "withdrawn" rather than
   quietly weakening it. A weakened claim is more likely to be reused as if it still held.
7. **Status words change with the status.** A claim recorded as `SUPPORTED` in
   `registry/claims.yaml` moves to `CORRECTED` or `WITHDRAWN` in the same change as the
   erratum, with the erratum as evidence.

## Verifying an entry

A reader should be able to re-derive every corrected value without trusting `ERRATA.md`:

```bash
engramfold verify-freeze <freeze_id>     # recompute a frozen dataset's identity
engramfold hash <path>                   # recompute an artifact digest
engramfold provenance                    # re-check the commit and dirty state
engramfold validate                      # confirm the registry still resolves after the edit
```

## What this policy does not allow

- Editing a frozen file "just this once", because the error was small.
- Deleting a superseded artifact, so the history looks cleaner.
- Recording a correction in a commit message or a chat log rather than in `ERRATA.md`.
- Restating a claim more weakly without recording that it was restated.
- Treating a classification decision as obvious. When in doubt, classify higher: the cost of a
  new artifact is small, and the cost of an undisclosed methodological change is a result
  nobody can trust.