# `docs/` — ownership and purpose

**Owner:** the maintainers.
**Status:** source, hand-authored.

## What lives here

| Document | Covers |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Every directory, its ownership and its purpose. The authority on layout. |
| [`METHODOLOGY.md`](METHODOLOGY.md) | Research *integrity* methodology. The research design is marked NOT YET FROZEN. |
| [`PROVENANCE.md`](PROVENANCE.md) | Source revision, environment capture, redaction, dataset lineage. |
| [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) | What determinism means, the identity kinds, and what is excluded from identity. |
| [`VALIDATION.md`](VALIDATION.md) | The eight gates, the census, the non-vacuity rules, entry-point parity. |
| [`ERRATA_POLICY.md`](ERRATA_POLICY.md) | How a mistake is corrected without rewriting frozen history. |

## The rule about documentation

Documentation here describes **mechanisms**, and each mechanism is enforced by a gate or a
test. A paragraph describing a check that does not exist is worse than no paragraph, because
it is believed. Where a document and the code disagree, the code is right and the document is
a bug.

Consequently:

- `docs/ARCHITECTURE.md` is checked: the `substrate` gate fails if a canonical directory is
  not named in it.
- `docs/VALIDATION.md` describes gates that `tests/test_validation_contract.py` asserts exist
  and execute.
- Anything in `docs/METHODOLOGY.md` about the research design is marked NOT YET FROZEN, and
  nothing here records a research parameter.

## Where research design will go

Not here, and not yet. The scientific hypothesis, literature, candidate model families,
operational definitions, baselines, metrics and ablations are the subject of the phase that
begins after this substrate is trusted. When they are decided they will be frozen, and only
then will a document in this directory describe them as decided.