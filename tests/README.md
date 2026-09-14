# `tests/` — ownership and purpose

**Class:** Substrate. **Owner:** the maintainers.

## The organising principle

An exit code of `0` is not sufficient evidence that validation occurred. So wherever possible
these tests assert on **counts and content**, not on exit status:

```python
assert result.checked == expected_count  # preferred
assert result.exit_code == 0  # not sufficient on its own
```

A test that only asserts `exit_code == 0` cannot distinguish "the check ran and found nothing
wrong" from "the check did not run". That distinction is the entire subject of this suite.

## What each module covers

| File | Covers |
|---|---|
| `test_hashing.py` | Determinism, one-byte sensitivity, directory-hash ordering and exclusions, volatile-field exclusion, streaming |
| `test_documents.py` | `ABSENT` vs `MALFORMED` vs `UNREADABLE` vs `UNKNOWN_SCHEMA`; discovery accounting |
| `test_validation_accounting.py` | Every non-vacuity and accounting rule, in both directions |
| `test_validation_contract.py` | The gate contract, the population policy, and the documented-empty authorisations |
| `test_non_vacuity.py` | The reproducibility cases: zero manifests, zero freezes, renamed fields, broken lookups, malformed documents, unknown versions, all-skipped |
| `test_dataset_manifest.py` | Schema, immutability requirements, and the adversarial cases for each source type |
| `test_dataset_freeze.py` | The mutation matrix: change one thing at a time and prove the freeze fails |
| `test_experiment_manifest.py` | Schema, status obligations, configuration-hash drift |
| `test_experiment_freeze.py` | Definition drift, field by field |
| `test_artifacts.py` | Manifest schema and byte verification, including unverifiable storage |
| `test_registry.py` | Reference resolution, duplicate ids, dangling references, population/phase coupling |
| `test_provenance_git.py` | Clean → modified → restored, real `git` subprocesses |
| `test_environment.py` | Secret redaction, determinism, hardware differences as informational |
| `test_entry_points.py` | CLI and `python -m` parity, `__main__` guards, equivalent verdicts |
| `test_cli.py` | Command behaviour, exit codes, dry-run and JSON output |
| `test_preflight.py` | The health gate's steps, including its live mechanism self-tests |
| `test_determinism.py` | Run twice, compare byte-for-byte |
| `test_anti_patterns.py` | Source scanning: no swallowed exceptions, one hashing implementation, generated markers |

## Running

```bash
python -m pytest                       # the suite
engramfold-preflight                   # the suite, plus format, lint, types and gates
```

## The adversarial cases are first-class

The cases in `test_non_vacuity.py` are not edge cases. Each is the direct regression test for
a failure that has actually occurred in a research repository of this shape:

- a validation command that checks nothing and reports success;
- freeze validation that skips every target because it looked for a field the records do not
  use;
- `python -m pkg.module` importing, running nothing and exiting `0`;
- counters that do not add up being read as a pass;
- a malformed manifest treated as an absent one.