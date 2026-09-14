# `reports/` — ownership and purpose

**Owner:** whoever writes up a result.
**Status:** source and generated. Prose is source; figures are generated and marked.

## What belongs here

Reports that interpret artifacts, and generated figures derived from them. Nothing else.

Reports are listed in `registry/reports.yaml`, each naming the artifacts and experiments it
interprets. That index exists so a report whose evidence has moved is **detectable** rather
than merely wrong.

## The rules

1. **Prose is not evidence.** A report interprets canonical machine-readable artifacts; it is
   not itself one. Where a report and an artifact disagree, the artifact wins until an
   explicit correction is made.
2. **Cite by id.** Every number in a report either comes from a generated figure or is
   asserted by a test against a canonical artifact. No figure is retyped from another
   document — transcription is the most common source of wrong numbers, and the hardest to
   notice.
3. **One quantity, one value.** A quantity that appears in three places has one value in all
   three.
4. **State the population.** Every table states its partition and its `n`, and every row is
   computed on that partition. Numerator and denominator use the same counting unit.
5. **Report the whole result.** Regressions next to gains, the final checkpoint next to any
   selected one, and any selection made on the evaluation set labelled as such.
6. **Caveats travel with the number**, on every surface the number reaches.
7. **Frozen is frozen.** Once a report is cited by a claim or frozen, corrections go through
   [`docs/ERRATA_POLICY.md`](../docs/ERRATA_POLICY.md) and [`ERRATA.md`](../ERRATA.md).
   Frozen bytes are never edited.
8. **Generated files are generated.** A figure carries a `generated_by` marker and is
   regenerated, never hand-tuned. A hand-tuned figure is a figure that no longer matches its
   evidence.

## Currently empty

There are no reports, because there is nothing to report. EngramFold has no findings. See
[`docs/METHODOLOGY.md`](../docs/METHODOLOGY.md) and
[`registry/claims.yaml`](../registry/claims.yaml).