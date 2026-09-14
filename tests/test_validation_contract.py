"""The gate contract: which gates must exist, and what it means for one to be allowed empty.

Three separate risks, each with its own tests:

1. a gate disappears, and the report gets shorter instead of failing;
2. a gate exists but executes nothing, and ``PASS`` means "no assertion was made";
3. a population policy declares a gate's inputs optional, and the declaration drifts away
   from what the repository actually contains.

The third is why :data:`PHASE_0_EMPTY_POPULATIONS` is pinned here. It is documentation only —
``validate.py`` reads ``registry/populations.yaml`` instead, which is the right design, because
data that the gates read cannot drift from the gates. But unpinned documentation drifts
silently, and then a reviewer reads an authorisation the gates are not applying.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import REQUIRED_POLICIES, registry_repo

from engramfold import STATUS
from engramfold.registry import validate as validate_module
from engramfold.registry.store import (
    DECLARED_POPULATIONS,
    GATE_POPULATION,
    OPTIONAL_POLICY_ALLOWED_PHASES,
    load_populations,
    load_registry,
    phase_errors,
    population_policy_errors,
)
from engramfold.registry.validate import SUBSTRATE_CHECKS, audit
from engramfold.schemas import SUPPORTED_SCHEMA_VERSIONS, VERSION_FIELD
from engramfold.validation import (
    CONDITIONALLY_REQUIRED,
    CONTRACT_PREFIX,
    COVERAGE_PREFIX,
    FAIL,
    GATE_CONTRACT,
    OPTIONAL,
    PASS,
    PHASE_0_EMPTY_POPULATIONS,
    POLICIES,
    REQUIRED_NONEMPTY,
    VERIFIER_CONTRACT,
    VerificationReport,
    build_result,
)


def passing_report(*names: str) -> VerificationReport:
    """A report containing exactly ``names`` as passing gates."""
    report = VerificationReport(contract=VERIFIER_CONTRACT, expected_gates=GATE_CONTRACT)
    for name in names:
        report.add(build_result(name, REQUIRED_NONEMPTY, [name], [], checks_executed=1))
    return report


# ── The contract is what audit() runs ────────────────────────────────────────


def test_the_contract_names_are_unique_ordered_and_non_blank():
    assert GATE_CONTRACT, "the contract names no gate, so validation would cover nothing"
    assert len(set(GATE_CONTRACT)) == len(GATE_CONTRACT)
    assert all(name.strip() == name and name == name.lower() for name in GATE_CONTRACT)
    assert VERIFIER_CONTRACT >= 1


def test_audit_runs_exactly_the_declared_gates(repo_root: Path):
    """The list that lives in the contract is the list that executes.

    Both orders are compared, because the report is read as a sequence and a reordered report
    is a changed contract.
    """
    report = audit(repo_root)
    assert list(report.expected_gates) == list(GATE_CONTRACT)
    assert list(report.ran_gates) == list(GATE_CONTRACT)
    assert report.missing_gates() == []
    assert report.unexpected_gates() == []
    assert len(report.results) == len(GATE_CONTRACT)


def test_every_contract_gate_has_an_implementation_and_executes(repo_root: Path):
    """A gate named by the contract must exist as code and must have run assertions.

    This is the check that makes the contract more than a list of words: ``checks_executed``
    is the count of assertions actually performed, so a gate that quietly stopped doing work
    fails here even though its own status might read as PASS.
    """
    report = audit(repo_root)
    for result in report.results:
        assert callable(getattr(validate_module, f"check_{result.name}", None)), (
            f"the contract names a gate {result.name!r} with no check_{result.name}() "
            f"implementation, so it cannot have executed"
        )
        assert result.policy in POLICIES, (result.name, result.policy)
        assert result.checks_executed > 0, f"{result.name}: {result.render()}"
        assert result.accounting_errors() == [], f"{result.name}: {result.render()}"
        assert result.status in (PASS, *("FAIL",)), f"{result.name}: {result.status}"


def test_the_report_totals_are_the_sum_of_the_gate_censuses(repo_root: Path):
    report = audit(repo_root)
    totals = report.totals()
    for key in ("discovered", "checked", "checks_executed", "passed", "failed"):
        assert totals[key] == sum(result.counts()[key] for result in report.results), key
    assert totals["checks_executed"] >= len(GATE_CONTRACT)


# ── A gate that does not execute fails coverage ─────────────────────────────


def test_a_report_missing_a_gate_is_a_coverage_failure():
    """Deleting a gate must make validation fail, not make the report shorter."""
    report = passing_report(*GATE_CONTRACT[:-1])
    assert report.missing_gates() == [GATE_CONTRACT[-1]]
    assert report.overall == FAIL
    assert any(COVERAGE_PREFIX in error for error in report.contract_errors())


def test_a_gate_the_contract_does_not_name_is_a_contract_failure():
    report = passing_report(*GATE_CONTRACT, "not_a_gate")
    assert report.unexpected_gates() == ["not_a_gate"]
    assert report.overall == FAIL
    assert any(CONTRACT_PREFIX in error for error in report.contract_errors())


def test_a_gate_executed_twice_is_a_contract_failure():
    report = passing_report(*GATE_CONTRACT[:-1], GATE_CONTRACT[0])
    assert report.duplicate_gates() == [GATE_CONTRACT[0]]
    assert report.overall == FAIL
    assert any("more than once" in error for error in report.contract_errors())


def test_a_report_with_no_gates_at_all_is_not_evidence():
    report = VerificationReport(contract=VERIFIER_CONTRACT, expected_gates=GATE_CONTRACT)
    assert report.overall == FAIL
    assert any(COVERAGE_PREFIX in error for error in report.contract_errors())


# ── The population policy is data, and it is pinned ────────────────────────


def test_the_gate_population_map_only_names_gates_and_declared_populations():
    """Gates and populations are different vocabularies, and the map is the bridge.

    The ``freezes`` gate is governed by the ``dataset_freezes`` population. That indirection
    exists because a gate once inherited the wrong policy by guessing at its own name, and
    reported a non-vacuity failure for a population the repository had authorised to be empty.
    """
    assert GATE_POPULATION, "no gate is mapped to a population, so none can be authorised empty"
    for gate, population in GATE_POPULATION.items():
        assert gate in GATE_CONTRACT, f"{gate!r} is not a gate the contract names"
        assert population in DECLARED_POPULATIONS, f"{population!r} is not a declared population"


def test_gates_without_a_mapped_population_are_required_by_default(repo_root: Path):
    """A gate with no population policy is required by construction, and says so.

    ``CONDITIONALLY_REQUIRED`` is admitted here with its precondition asserted, because that is
    the honest policy for the registry gate: the registries must exist, and a repository that
    has none must say why rather than pass quietly.
    """
    report = audit(repo_root)
    unmapped = [result for result in report.results if result.name not in GATE_POPULATION]
    assert unmapped, "every gate is mapped to a population, so the default is untested"
    for result in unmapped:
        assert result.policy in (REQUIRED_NONEMPTY, CONDITIONALLY_REQUIRED), (
            result.name,
            result.policy,
        )
        if result.policy == CONDITIONALLY_REQUIRED:
            assert (result.precondition or "").strip(), (
                f"{result.name} is conditionally required but states no precondition, so "
                f"nothing decides whether it applies"
            )


def test_the_shipped_policy_file_declares_every_population_and_the_phase(repo_root: Path):
    populations, errors = load_populations(repo_root)
    assert errors == [], errors
    assert populations is not None
    assert set(populations.policies) == set(DECLARED_POPULATIONS)
    assert populations.phase == STATUS
    assert phase_errors(repo_root, populations) == []
    assert STATUS in OPTIONAL_POLICY_ALLOWED_PHASES
    for name, entry in populations.policies.items():
        assert entry.policy in POLICIES, (name, entry.policy)
        if entry.policy == OPTIONAL:
            assert (entry.zero_allowed_reason or "").strip(), f"{name} may be empty without why"
        else:
            assert entry.zero_allowed_reason is None, (
                f"{name} is required but still carries an empty-authorisation"
            )


def test_no_gate_is_authorised_empty_without_being_named_in_the_constant(repo_root: Path):
    """``PHASE_0_EMPTY_POPULATIONS`` and the shipped policy describe the same gates.

    Equality in both directions, because either drift is a defect: a gate the policy allows to
    be empty but the constant does not name is an authorisation nobody reviewed, and a name in
    the constant the policy does not authorise is documentation of an exemption the repository
    does not actually grant.
    """
    populations, errors = load_populations(repo_root)
    assert populations is not None and errors == []
    authorised = {
        gate
        for gate, population in GATE_POPULATION.items()
        if populations.policy_for(population) == OPTIONAL
    }
    assert authorised == set(PHASE_0_EMPTY_POPULATIONS), (
        f"the policy authorises {sorted(authorised)} but the contract constant names "
        f"{sorted(PHASE_0_EMPTY_POPULATIONS)}"
    )
    for gate, reason in PHASE_0_EMPTY_POPULATIONS.items():
        assert gate in GATE_CONTRACT, gate
        assert reason.strip(), f"{gate} is authorised empty with no written reason"
        entry = populations.policies[GATE_POPULATION[gate]]
        assert entry.policy == OPTIONAL, (gate, entry.policy)
        assert (entry.zero_allowed_reason or "").strip(), gate


# ── A policy that contradicts the registry is an error ──────────────────────


def test_an_optional_population_that_contains_records_is_a_contradiction(tmp_path: Path):
    """``OPTIONAL`` must never be usable to skip work that exists."""
    root = registry_repo(
        tmp_path / "populated",
        datasets=[
            "  - id: example\n    manifest_path: datasets/manifests/example.json\n",
        ],
    )
    errors = population_policy_errors(load_registry(root))
    assert any("OPTIONAL" in error and "datasets" in error for error in errors), errors


def test_a_required_population_that_is_empty_is_a_contradiction(tmp_path: Path):
    """A required population that is empty means either the registry lost its contents or the
    policy is aspirational while nothing is enforced. Both are errors."""
    root = registry_repo(tmp_path / "required", policies=REQUIRED_POLICIES)
    errors = population_policy_errors(load_registry(root))
    assert errors, "a required population with no records was accepted"
    assert any("contains no records" in error for error in errors), errors


# ── The contract itself is registered and enforced ──────────────────────────


def test_the_contract_version_is_a_registered_format():
    assert "gate_contract" in SUPPORTED_SCHEMA_VERSIONS
    assert VERSION_FIELD["gate_contract"] == "schema_version"
    assert VERIFIER_CONTRACT in SUPPORTED_SCHEMA_VERSIONS["gate_contract"]


def test_a_declared_invariant_without_an_implementation_fails_the_substrate_gate(
    monkeypatch: pytest.MonkeyPatch, repo_root: Path
):
    """Deleting a substrate check must fail the gate, not silently shorten it."""
    missing = SUBSTRATE_CHECKS[0]
    monkeypatch.delitem(validate_module._SUBSTRATE_IMPLEMENTATIONS, missing)
    result = validate_module.check_substrate(repo_root)
    assert result.status == FAIL, result.render()
    assert result.checks_executed == len(SUBSTRATE_CHECKS) - 1
    assert any(missing in error for error in result.all_errors()), result.all_errors()
    assert any("no implementation" in error for error in result.all_errors()), result.all_errors()


def test_the_real_repository_satisfies_the_whole_contract(repo_root: Path):
    report = audit(repo_root)
    assert report.overall == PASS, report.render()
    assert report.contract_errors() == []
    assert report.all_errors() == []
    assert report.totals()["failed"] == 0
