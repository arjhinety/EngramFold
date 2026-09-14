# Errata

**Ledger state:** empty. Nothing in this repository has been frozen, so nothing has needed
correcting.

This file is the permanent record of corrections to frozen artifacts and reports. An entry
here supersedes the artifact it names; the artifact itself is never edited. Where a frozen
file and this ledger disagree, this ledger is the correct one.

The policy that governs entries — including what counts as a typo, a metadata correction, a
numerical correction, a methodological correction and an invalidated result, and which of
those require a new artifact rather than an erratum — is
[`docs/ERRATA_POLICY.md`](docs/ERRATA_POLICY.md).

**What this ledger deliberately does not record.** Corrections made during Phase 0 to the
*substrate's documentation* — a claim about what a counter was called, or how far a determinism
guarantee reached, or the commit sequence — are not errata. Nothing had been frozen when they were
made, so there was no recorded artifact for them to supersede, and the entries above each require a
frozen artifact and its hash. They are recorded instead in
[`docs/PHASE_0_CLOSURE.md`](docs/PHASE_0_CLOSURE.md) §C.2, with the command that found each one. An
entry belongs here the moment a frozen artifact or a report needs correcting, and not before.

No entries yet.

---

## Entry format

Every entry must identify all of the following. An entry missing any of them is incomplete
and should not be merged.

```markdown
### E-0001 — <one-line summary>

- **Affected artifact:** `<path>`
- **Affected hash:** `<sha256 of the artifact as frozen>`
- **Original claim:** <what the artifact asserted, quoted or cited by line>
- **Corrected claim:** <what is true instead>
- **Reason:** <why the original was wrong>
- **Evidence:** <artifact ids / experiment ids / freeze ids / metric files that establish the correction>
- **Date:** <ISO-8601 date>
- **Correcting commit:** <git commit that added this entry>
- **Classification:** typo | metadata | numerical | methodological | invalidated
```

## How to verify an entry

Each entry names the machine-readable evidence its correction rests on. A reader should be
able to re-derive the corrected value from that evidence without trusting this file:

- metric corrections are recomputed from the committed metric artifact or the committed
  per-example predictions, not copied from another report;
- identity corrections cite a freeze id, whose digest can be recomputed with
  `engramfold verify-freeze`;
- provenance corrections cite a provenance record, whose commit and dirty state can be
  re-checked with `engramfold provenance`.

Where the evidence a correction would need does not exist — because per-example data was
never committed, or an artifact was deleted — the entry must say so explicitly and the
claim must be marked as unverifiable rather than quietly restated.