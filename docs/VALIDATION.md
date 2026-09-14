# Validation

Validation exists to make the repository's own claims checkable. It has one hard
requirement, and everything else follows from it:

> A validation command that checked nothing must **fail**, unless the caller explicitly
> selected an operation where zero targets is valid.

A gate that returns success without doing any work is indistinguishable, from the outside,
from one that did the work and found nothing wrong. That property converts "I verified this"
into a claim nothing supports, so it is treated here as the primary defect rather than an
edge case.

## The census

Every gate reports the same census:

| Counter | Meaning |
|---|---|
| `discovered` | targets this gate was pointed at |
| `checked` | targets actually evaluated |
| `checks_executed` | **individual assertions performed** — independent of target count |
| `passed` / `failed` | outcomes for checked targets |
| `blocked` | targets that could not be evaluated |
| `skipped` | discovered targets deliberately not evaluated, each with a reason |
| `warnings` | recorded, never failing |

`discovered`/`checked` count *targets*. `checks_executed` counts *assertions*. They are kept
apart because a gate can discover ten targets and execute zero checks (it is broken), or
discover zero targets and still execute real checks (a schema loads, a policy is
self-consistent, a directory exists).

## Non-vacuity rules

Enforced in `engramfold/validation/accounting.py`. Each produces a greppable error prefix so
a reader scanning a log can tell a vacuity failure from an accounting contradiction from a
content failure.

| Rule | Prefix |
|---|---|
| `checks_executed == 0` and the gate did not declare an authorised-empty reason | `FAIL_NONVACUOUS` |
| A `REQUIRED_NONEMPTY` gate discovered zero targets | `FAIL_NONVACUOUS` |
| A `CONDITIONALLY_REQUIRED` gate whose precondition holds discovered zero targets | `FAIL_NONVACUOUS` |
| A required gate discovered targets and evaluated none of them | `FAIL_NONVACUOUS` |
| `discovered != checked + blocked + skipped` | `FAIL_ACCOUNTING` |
| `checked != passed + failed` | `FAIL_ACCOUNTING` |
| A skip carries no reason | `FAIL_ACCOUNTING` |
| A counter is negative | `FAIL_ACCOUNTING` |
| Unknown population policy, unknown blocked status, blank authorisation, `OPTIONAL` gate claiming an empty authorisation | `FAIL_CONTRACT` |
| A declared gate did not execute | `FAIL_COVERAGE` |
| A gate executed that the contract does not name, or executed twice | `FAIL_CONTRACT` |

Zero executed checks is a failure **even for an `OPTIONAL` gate**, unless that gate sets a
non-empty `allowed_empty_reason` — and only an `OPTIONAL` gate is permitted to. The
constructor takes `policy` with no default, so a new gate cannot acquire optional semantics
by omission.

## Population policy

A gate must declare what it is, and the declaration is a required constructor argument:

| Policy | Meaning |
|---|---|
| `REQUIRED_NONEMPTY` | The repository contains these inputs by construction. Discovering none means discovery is broken. |
| `CONDITIONALLY_REQUIRED` | Required while a stated precondition holds. |
| `OPTIONAL` | May legitimately have no inputs; discovering none is recorded as a skip with a reason. |

At Phase 0 most populations are legitimately empty. Rather than hardcode that, the policy is
**data**: `registry/populations.yaml` declares the phase and, per population, whether an empty
population is authorised and why. Gates read it.

Two consistency rules stop that declaration from becoming an escape hatch:

1. **`OPTIONAL` while records exist is a contradiction.** A policy cannot authorise an empty
   validation run over a populated registry.
2. **`OPTIONAL` is only legal while `phase` matches `engramfold.STATUS`**, and that same phase
   string must appear as the `STATUS:` line in `README.md`. Relaxing the policy therefore
   requires changing what the repository publicly says it is.

## The eight gates

The set is declared in `engramfold/validation/contract.py`, separately from the code that
runs the gates. A gate named by the contract and absent from the results is a **failure**, not
a shorter report — so deleting a gate fails validation instead of shrinking it.

| Gate | The question it answers |
|---|---|
| `substrate` | Is the integrity infrastructure self-consistent and functional? |
| `documents` | Do the canonical documents exist, load, and advertise the right status? |
| `registry` | Are registries well-formed, resolvable, and consistent with their policy? |
| `datasets` | Do dataset manifests satisfy their schema and their registry entries? |
| `freezes` | Do dataset freezes still describe the bytes and manifests they pinned? |
| `experiments` | Are experiment manifests resolvable, and has any definition drifted? |
| `artifacts` | Do artifact manifests name resolvable inputs, and do their bytes still match? |
| `provenance` | Is the checkout's provenance knowable, and are records consistent? |

### `substrate`

Executes six named invariants, which are its targets, so "the infrastructure is
self-consistent" is an executed claim rather than a mood:

1. every registered document kind has a version field and a known version;
2. only `hashing.py` imports a hash library;
3. no module discards an exception silently — detected by parsing the AST, so a bare
   `except:` or a handler whose body is only `pass`/`continue`/`...` fails;
4. generated documents carry a `generated_by` marker and hand-authored documents do not;
5. every canonical directory has a `README.md` and is named in `docs/ARCHITECTURE.md`;
6. the canonical hashing implementation is exercised live, asserting repeatability, key-order
   independence, volatile-field exclusion, one-byte sensitivity, and location-independent
   directory hashing.

### `documents`

Every canonical document must exist, load, and be non-empty; `README.md` must carry the
`STATUS:` line; and `README.md` must not contain claim-shaped phrasing, since it is not
evidence.

### `registry`

Registry files parse; records have valid unique ids and required fields; references resolve in
both directions; claims cite resolvable machine-readable evidence; the population policy is
consistent with what the registry contains; and the advertised phase matches the declared one.

A dangling reference is an **error**, not a skip. Deleting a record silently orphans
everything that pointed at it, and the gates that would have checked those targets would
simply have fewer targets — which is how a validation suite shrinks toward nothing.

### `freezes`

For each freeze: the document is structurally valid and its `freeze_id` matches its own
contents; the manifest it names exists and has the recorded identity; the freeze and the
manifest agree about which dataset this is; the frozen artifact set matches what the manifest
declares; the record count is unchanged; and every artifact exists, hashes to its recorded
digest, and matches its recorded size.

Freeze generation and verification are exercised live by `engramfold-preflight`, which builds a
fixture freeze, mutates one byte, and **asserts verification now fails**.

### `experiments`

Manifest schema and obligations; `configuration_hash` recomputed against the configuration
block; input freeze references resolved; and every definition freeze re-verified against its
manifest, so definition drift is reported as the specific field that moved.

### `artifacts`

Manifest schema; references resolved; and byte verification for repository-local artifacts. An
artifact whose bytes are external or remote is reported as **unverifiable with a reason** —
`bytes_verified` stays `false`, because a manifest whose bytes cannot be re-read has not been
checked.

### `provenance`

Captures the current checkout's git state, and validates recorded provenance records. A dirty
tree is a **warning** (uncommitted work is normal during development). An *unknowable* tree —
git absent, or its status unreadable — **blocks** the gate with exit code `3`, because nothing
about attribution can be established and silence would be worse than a refusal.

## Statuses and exit codes

`PASS` is the only success. `BLOCKED_*` is explicitly not success: a gate that could not
evaluate something reports that it could not, and the report's overall verdict becomes blocked.

| Verdict | Exit code |
|---|---|
| `PASS` | 0 |
| `FAIL` | 1 |
| `BLOCKED_TARGETS` / `BLOCKED_DEPENDENCY` / `BLOCKED_NETWORK` | 3 |
| usage error | 2 |

## Entry points

Validation is reachable three ways, and all three must do the same work:

```bash
engramfold validate
engramfold-validate            # console script -> engramfold.registry.validate:main
python -m engramfold.registry.validate
```

The module form is called out because its absence was a real defect in the repository this
design learned from: without a `__main__` guard, `python -m pkg.module` imports the module,
executes nothing, and exits `0` — indistinguishable from a passing validation.
`tests/test_entry_points.py` runs every entry point as a real subprocess and asserts they agree
with each other and with the in-process verdict, on both a valid and an invalid repository.

## Adversarial cases that are first-class tests

Not edge cases. Each is the direct regression test for a failure that has been observed in
practice:

| Case | Expected |
|---|---|
| zero dataset manifests | authorised-empty, and the gate still executed checks |
| zero freezes | authorised-empty, and the gate still executed checks |
| broken manifest lookup | fails; a wrong field name is not silent absence |
| renamed manifest field | fails; unknown fields are errors |
| missing referenced file | fails, naming the missing path |
| empty registry | the population policy must declare it; anything populated and declared `OPTIONAL` fails |
| malformed JSON / YAML | fails as `MALFORMED`, never as `ABSENT` |
| unknown schema version | fails as `UNKNOWN_SCHEMA` |
| every target skipped | fails for a required population; an `OPTIONAL` one may skip with written reasons, and the skips stay inside `discovered` |
| every target skipped *and* no authorisation written | fails as `FAIL_NONVACUOUS` |
| validator module does not execute | detected by entry-point parity |
| a gate removed from the contract | `FAIL_COVERAGE` |

## The canonical health gate

```bash
engramfold-preflight
```

One entry point for repository health: format, lint, types, tests, every validation gate,
plus two live mechanism self-tests (freeze and provenance) that work even when the repository
contains no freezes. Each step reports how much it **executed**, and zero is a failure. A
linter that linted no files passes; a test run that collected no tests passes; neither is
evidence of a healthy repository.