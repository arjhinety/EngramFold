# EngramFold

**STATUS: PRE-EXPERIMENT / INFRASTRUCTURE ONLY**

EngramFold is an experimental research project investigating structural consolidation of
pretrained Mixture-of-Experts models and mechanisms for preserving behavior after
consolidation.

This repository currently contains **no research results**. It contains the integrity
substrate the research will have to run on: canonical hashing, dataset and experiment
manifests, freezes, git and environment provenance, a registry whose references must
resolve, and validation that fails when it checks nothing.

## What this repository does not claim

None of the following is established here, and nothing in this repository may be read as
asserting it:

- that Engram-style memory compensates for anything lost during consolidation;
- that experts can be consolidated to any particular number;
- that behavior or quality can be preserved;
- that any approach here is novel;
- that any method performs better than any prior work.

These are the questions the project intends to ask. They are recorded in
[`registry/claims.yaml`](registry/claims.yaml) as an empty list, which is the honest state
of a repository that has established nothing.

## Why the substrate comes first

A research repository that cannot prove what it ran on will eventually report something it
cannot support. The failure is rarely dramatic: a validation command that checked no
targets and printed `OK`; a freeze that pinned a manifest field the records never used, so
every corpus was skipped and the gate passed; a `python -m` invocation that imported a
module, ran nothing, and exited `0`. Each of those looks exactly like success.

This repository is built so that those specific outcomes are impossible:

| Question | Where it is answered |
|---|---|
| What files constitute the canonical research state? | [`registry/`](registry/README.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Where did every input dataset come from? | [dataset manifest](docs/PROVENANCE.md#dataset-manifests) |
| Has a frozen input changed? | [`engramfold verify-freeze`](docs/VALIDATION.md#freeze-verification) |
| What version of the repository created an artifact? | [`docs/PROVENANCE.md`](docs/PROVENANCE.md#source-revision) |
| What exact environment created it? | [`docs/PROVENANCE.md`](docs/PROVENANCE.md#environment) |
| What command created it? | artifact manifest `command`, freeze `frozen_by_command` |
| Has an experiment definition changed since execution? | [definition freezes](docs/VALIDATION.md#definition-drift) |
| Can validation accidentally pass while doing nothing? | [`docs/VALIDATION.md`](docs/VALIDATION.md#non-vacuity) |
| Can a validator silently skip every target? | [skip accounting](docs/VALIDATION.md#non-vacuity) |
| Can a result be published without traceable evidence? | [`registry/claims.yaml`](registry/claims.yaml) |
| Can a file be edited after a result was generated undetected? | [`docs/ERRATA_POLICY.md`](docs/ERRATA_POLICY.md) |
| Can historical mistakes be corrected without rewriting frozen history? | [`ERRATA.md`](ERRATA.md) |

## Install and use

```bash
python -m pip install -e ".[dev,yaml]"

engramfold identity            # what this project claims to be
engramfold validate            # every validation gate, with an execution census
engramfold hash <path>         # canonical content hash
engramfold hash --tree <dir>   # deterministic directory hash
engramfold provenance          # capture this checkout's git and environment provenance
engramfold verify-freeze       # verify frozen datasets
engramfold-preflight           # the canonical repository health gate
```

Every command has a `--json` form, and every command's exit code is meaningful: `0` pass,
`1` failure, `2` usage error, `3` blocked. Blocked is never zero — a gate that could not
evaluate something is not a gate that passed.

## Documentation

| Document | What it covers |
|---|---|
| [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) | Research *integrity* methodology. The research design is marked NOT YET FROZEN. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Every directory, its ownership and its purpose. |
| [`docs/PROVENANCE.md`](docs/PROVENANCE.md) | Source revision, environment capture, redaction, dataset lineage. |
| [`docs/REPRODUCIBILITY.md`](docs/REPRODUCIBILITY.md) | What determinism means here, and what is deliberately excluded from identity. |
| [`docs/VALIDATION.md`](docs/VALIDATION.md) | The eight gates, the census, and the non-vacuity rules. |
| [`docs/ERRATA_POLICY.md`](docs/ERRATA_POLICY.md) | How a mistake is corrected without rewriting frozen history. |
| [`docs/PHASE_0_CLOSURE.md`](docs/PHASE_0_CLOSURE.md) | The Phase 0 closure record: what was built, what was verified, what verification found, and the closure decision. |
| [`ERRATA.md`](ERRATA.md) | The errata ledger. Empty, because nothing has been frozen. |

## Repository layout

```text
src/engramfold/     the integrity substrate
  hashing.py        the single canonical hashing implementation
  documents.py      document loading, with absence and corruption held apart
  validation/       execution accounting and the gate contract
  provenance/       git state and environment capture
  datasets/         dataset manifests and freezes
  experiments/      experiment manifests and definition freezes
  artifacts/        artifact manifests
  registry/         registry loading and the eight validation gates
  cli/              the command line
  preflight.py      the canonical health gate
registry/           the index: records, references, claims, population policy
datasets/           manifests (hand-authored) and freezes (generated)
experiments/        manifests (hand-authored) and freezes (generated)
artifacts/          manifests (generated); large payloads are never canonical state
provenance/         recorded provenance records
reports/            reporting surface
docs/               documentation
tests/              the adversarial test suite
```

## License

Apache-2.0. See [`LICENSE`](LICENSE).