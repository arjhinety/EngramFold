"""Validation accounting: every rule that stops a green result from meaning nothing.

Each test asserts on a status or an error prefix, never on an exit code. A test asserting
"the gate returned without raising" would pass against an implementation that returns PASS
unconditionally, which is precisely the defect this module exists to make impossible.
"""

from __future__ import annotations

import pytest

from engramfold.validation import (
    ACCOUNTING_PREFIX,
    BLOCKED_DEPENDENCY,
    BLOCKED_NETWORK,
    BLOCKED_STATUSES,
    BLOCKED_TARGETS,
    CONDITIONALLY_REQUIRED,
    CONTRACT_PREFIX,
    COVERAGE_PREFIX,
    FAIL,
    NONVACUOUS_PREFIX,
    OPTIONAL,
    PASS,
    POLICIES,
    REQUIRED_NONEMPTY,
    Skip,
    ValidationResult,
    VerificationReport,
    require_policy,
    skipped_all,
)
from engramfold.validation.census import (
    attribute_errors,
    build_result,
    empty_population_result,
    summarise,
)


def _has(errors: list[str], prefix: str) -> bool:
    return any(error.startswith(prefix) for error in errors)


# ── The primary rule: zero executed checks ───────────────────────────────────


def test_zero_checks_executed_fails_even_for_an_optional_gate():
    """The central guarantee: doing nothing is never a pass by default."""
    result = ValidationResult(name="g", policy=OPTIONAL, checks_executed=0)
    assert result.status == FAIL
    assert _has(result.all_errors(), NONVACUOUS_PREFIX)


def test_zero_checks_executed_passes_only_with_a_written_authorisation():
    result = ValidationResult(
        name="g",
        policy=OPTIONAL,
        checks_executed=0,
        allowed_empty_reason="this gate has no inputs at this phase, by declaration",
    )
    assert result.status == PASS


def test_a_blank_authorisation_does_not_count():
    result = ValidationResult(
        name="g", policy=OPTIONAL, checks_executed=0, allowed_empty_reason="   "
    )
    assert result.status == FAIL
    assert _has(result.all_errors(), CONTRACT_PREFIX)
    assert _has(result.all_errors(), NONVACUOUS_PREFIX)


def test_only_an_optional_gate_may_be_authorised_to_execute_nothing():
    for policy in (REQUIRED_NONEMPTY, CONDITIONALLY_REQUIRED):
        result = ValidationResult(
            name="g",
            policy=policy,
            checks_executed=0,
            allowed_empty_reason="we decided this one is fine",
        )
        assert result.status == FAIL, policy
        assert _has(result.all_errors(), CONTRACT_PREFIX), policy


def test_one_executed_check_is_enough_to_clear_the_threshold():
    result = ValidationResult(name="g", policy=OPTIONAL, checks_executed=1)
    assert result.status == PASS


# ── Required populations ─────────────────────────────────────────────────────


def test_required_population_with_zero_targets_fails():
    result = ValidationResult(name="g", policy=REQUIRED_NONEMPTY, discovered=0, checks_executed=3)
    assert result.status == FAIL
    assert _has(result.vacuity_errors(), NONVACUOUS_PREFIX)


def test_required_population_with_zero_discovery_reports_the_trigger():
    result = ValidationResult(
        name="g",
        policy=CONDITIONALLY_REQUIRED,
        discovered=0,
        checks_executed=2,
        precondition="inputs are present",
    )
    assert result.status == FAIL
    assert any("inputs are present" in error for error in result.vacuity_errors())


def test_conditional_requirement_does_not_bind_without_a_precondition():
    result = ValidationResult(
        name="g", policy=CONDITIONALLY_REQUIRED, discovered=0, checks_executed=2
    )
    assert result.status == PASS


def test_discovering_targets_and_evaluating_none_fails():
    result = ValidationResult(
        name="g",
        policy=REQUIRED_NONEMPTY,
        discovered=3,
        checked=0,
        skipped=[Skip(id=str(i), reason="deferred") for i in range(3)],
        checks_executed=2,
    )
    assert result.status == FAIL
    assert _has(result.vacuity_errors(), NONVACUOUS_PREFIX)


def test_optional_population_with_zero_targets_and_real_checks_passes():
    """The authorised-empty outcome, with the gate having actually executed something."""
    result = ValidationResult(
        name="g",
        policy=OPTIONAL,
        discovered=0,
        checks_executed=4,
        allowed_empty_reason="no such documents exist at this phase, by declaration",
    )
    assert result.status == PASS
    assert result.counts()["checks_executed"] == 4


# ── Accounting consistency ───────────────────────────────────────────────────


def test_discovered_must_equal_checked_plus_blocked_plus_skipped():
    result = ValidationResult(
        name="g", policy=OPTIONAL, discovered=3, checked=1, passed=1, checks_executed=1
    )
    assert _has(result.accounting_errors(), ACCOUNTING_PREFIX)
    assert result.status == FAIL


def test_checked_must_equal_passed_plus_failed():
    result = ValidationResult(
        name="g",
        policy=OPTIONAL,
        discovered=3,
        checked=3,
        passed=1,
        failed=1,
        checks_executed=3,
    )
    assert _has(result.accounting_errors(), ACCOUNTING_PREFIX)


def test_a_skip_without_a_reason_is_an_accounting_error():
    result = ValidationResult(
        name="g",
        policy=OPTIONAL,
        discovered=1,
        checked=0,
        skipped=[Skip(id="a", reason="   ")],
        checks_executed=1,
    )
    assert _has(result.accounting_errors(), ACCOUNTING_PREFIX)


def test_a_skip_with_a_reason_accounts_correctly():
    result = ValidationResult(
        name="g",
        policy=OPTIONAL,
        discovered=1,
        checked=0,
        skipped=[Skip(id="a", reason="deferred to the remote identity gate")],
        checks_executed=1,
    )
    assert result.accounting_errors() == []
    assert result.status == PASS


def test_negative_counters_are_an_accounting_error():
    result = ValidationResult(name="g", policy=OPTIONAL, checks_executed=-1)
    assert _has(result.accounting_errors(), ACCOUNTING_PREFIX)


def test_inconsistent_counters_cannot_reach_a_pass_through_a_report():
    bad = ValidationResult(name="g", policy=REQUIRED_NONEMPTY, discovered=2, checked=0)
    report = VerificationReport(contract=1, expected_gates=("g",), results=[bad])
    assert report.overall == FAIL


# ── Contract misuse ──────────────────────────────────────────────────────────


def test_unknown_policy_is_a_contract_error():
    result = ValidationResult(name="g", policy="MAYBE", checks_executed=1)
    assert _has(result.contract_errors(), CONTRACT_PREFIX)
    assert result.status == FAIL


def test_require_policy_raises_rather_than_degrading():
    """A gate with a misspelled policy must not quietly become OPTIONAL."""
    from engramfold.errors import ValidationContractError

    for policy in POLICIES:
        assert require_policy(policy) == policy
    with pytest.raises(ValidationContractError):
        require_policy("optional")


def test_unknown_blocked_status_is_a_contract_error():
    result = ValidationResult(
        name="g",
        policy=OPTIONAL,
        checks_executed=1,
        blocked_status="SOMETHING_ELSE",
        blocked_reasons=["reason"],
    )
    assert _has(result.contract_errors(), CONTRACT_PREFIX)


def test_blocked_status_without_a_reason_is_a_contract_error():
    result = ValidationResult(
        name="g", policy=OPTIONAL, checks_executed=1, blocked_status=BLOCKED_DEPENDENCY
    )
    assert _has(result.contract_errors(), CONTRACT_PREFIX)


def test_blocked_status_with_a_reason_is_legitimate_and_not_a_pass():
    result = ValidationResult(
        name="g",
        policy=OPTIONAL,
        discovered=1,
        checked=0,
        blocked=1,
        checks_executed=1,
        blocked_status=BLOCKED_DEPENDENCY,
        blocked_reasons=["the dependency is not installed"],
    )
    assert result.status == BLOCKED_DEPENDENCY
    assert result.status != PASS


# ─ Blocked ─────────────────────────────────────────────────────────────────


def test_blocked_targets_yield_a_blocked_status_not_a_pass():
    result = ValidationResult(
        name="g",
        policy=REQUIRED_NONEMPTY,
        discovered=8,
        checked=0,
        blocked=8,
        checks_executed=1,
        blocked_reasons=["network unavailable"],
    )
    assert result.status == BLOCKED_TARGETS
    assert result.discovered == 8, "a blocked target is still a discovered target"


def test_blocked_statuses_are_all_non_pass():
    for status in BLOCKED_STATUSES:
        assert status != PASS
        assert status != FAIL


# ─ skipped_all ─────────────────────────────────────────────────────────────


def test_skipped_all_requires_a_reason():
    from engramfold.errors import ValidationContractError

    with pytest.raises(ValidationContractError):
        skipped_all(["a", "b"], "  ")
    skips = skipped_all(["a", "b"], "deferred")
    assert [skip.id for skip in skips] == ["a", "b"]


# ── build_result: attribution ────────────────────────────────────────────────


def test_build_result_derives_passed_and_failed():
    result = build_result("g", OPTIONAL, ["a", "b"], ["a: bad"], checks_executed=2)
    assert result.checked == 2
    assert result.passed == 1
    assert result.failed == 1
    assert result.accounting_errors() == []


def test_an_unattributable_error_becomes_a_failed_sentinel():
    """Losing an error is the mechanism by which a failing check becomes a passing gate."""
    result = build_result(
        "g", OPTIONAL, ["a"], ["something went wrong at the gate level"], checks_executed=1
    )
    assert result.failed >= 1
    assert result.status == FAIL
    assert any(
        "gate:g" in error or "something went wrong" in error for error in result.all_errors()
    )


def test_an_unattributable_error_increases_the_census_rather_than_reducing_it():
    without = build_result("g", OPTIONAL, ["a"], [], checks_executed=1)
    with_orphan = build_result("g", OPTIONAL, ["a"], ["orphan"], checks_executed=1)
    assert with_orphan.discovered > without.discovered


def test_gate_level_errors_also_produce_a_failed_target():
    result = build_result(
        "g",
        OPTIONAL,
        ["a"],
        [],
        checks_executed=1,
        gate_level_errors=["the registry file would not parse"],
    )
    assert result.failed >= 1
    assert result.status == FAIL


def test_all_targets_failing_is_reported_as_such():
    result = build_result("g", OPTIONAL, ["a", "b"], ["a: x", "b: y"], checks_executed=2)
    assert result.passed == 0
    assert result.failed == 2


def test_skipped_targets_stay_in_the_discovered_population():
    result = build_result(
        "g",
        OPTIONAL,
        ["a", "b"],
        ["a: x"],
        checks_executed=1,
        skipped=[Skip(id="b", reason="deferred")],
    )
    assert result.discovered == 2
    assert result.checked == 1
    assert result.skipped_count == 1
    assert result.accounting_errors() == []


def test_a_skipped_target_not_listed_in_the_population_is_folded_in():
    """Bookkeeping slips must not turn into a bogus accounting failure."""
    result = build_result(
        "g", OPTIONAL, ["a"], [], checks_executed=1, skipped=[Skip(id="z", reason="deferred")]
    )
    assert result.discovered == 2
    assert result.accounting_errors() == []


def test_blocked_targets_stay_in_the_population():
    result = build_result(
        "g",
        OPTIONAL,
        ["a"],
        [],
        checks_executed=1,
        blocked_ids=["b"],
        blocked_reasons=["could not reach it"],
    )
    assert result.discovered == 2
    assert result.blocked == 1
    assert result.accounting_errors() == []


def test_duplicate_target_ids_are_an_error_not_a_double_count():
    result = build_result("g", OPTIONAL, ["a", "a"], [], checks_executed=1)
    assert result.status == FAIL
    assert any("duplicate target ids" in error for error in result.all_errors())


def test_build_result_rejects_an_unknown_policy():
    from engramfold.errors import ValidationContractError

    with pytest.raises(ValidationContractError):
        build_result("g", "NOPE", ["a"], [], checks_executed=1)


def test_attribute_errors_prefers_the_longest_matching_id():
    attributed, unattributed = attribute_errors(["a", "a.b"], ["a.b: boom"], gate="g")
    assert attributed["a.b"] == ["a.b: boom"]
    assert attributed["a"] == []
    assert unattributed == []


def test_attribute_errors_returns_unmatched_errors_separately():
    attributed, unattributed = attribute_errors(["a"], ["unrelated"], gate="g")
    assert attributed == {"a": []}
    assert unattributed == ["unrelated"]


# ── empty_population_result ──────────────────────────────────────────────────


def test_empty_population_result_requires_a_reason():
    with pytest.raises(ValueError):
        empty_population_result("g", checks_executed=1, reason="  ")


def test_empty_population_result_is_optional_and_executed():
    result = empty_population_result("g", checks_executed=3, reason="nothing exists yet by design")
    assert result.policy == OPTIONAL
    assert result.status == PASS
    assert result.counts()["checks_executed"] == 3


# ── VerificationReport ───────────────────────────────────────────────────────


def test_a_report_missing_a_declared_gate_fails_coverage():
    report = VerificationReport(
        contract=1,
        expected_gates=("a", "b"),
        results=[ValidationResult(name="a", policy=OPTIONAL, checks_executed=1)],
    )
    assert report.missing_gates() == ["b"]
    assert _has(report.contract_errors(), COVERAGE_PREFIX)
    assert report.overall == FAIL


def test_an_empty_report_is_a_failure():
    report = VerificationReport(contract=1, expected_gates=("a",), results=[])
    assert report.overall == FAIL
    assert _has(report.contract_errors(), COVERAGE_PREFIX)


def test_a_gate_outside_the_contract_is_a_contract_error():
    report = VerificationReport(
        contract=1,
        expected_gates=("a",),
        results=[
            ValidationResult(name="a", policy=OPTIONAL, checks_executed=1),
            ValidationResult(name="surprise", policy=OPTIONAL, checks_executed=1),
        ],
    )
    assert report.unexpected_gates() == ["surprise"]
    assert _has(report.contract_errors(), CONTRACT_PREFIX)


def test_a_gate_executed_twice_is_a_contract_error():
    report = VerificationReport(
        contract=1,
        expected_gates=("a",),
        results=[
            ValidationResult(name="a", policy=OPTIONAL, checks_executed=1),
            ValidationResult(name="a", policy=OPTIONAL, checks_executed=1),
        ],
    )
    assert report.duplicate_gates() == ["a"]
    assert _has(report.contract_errors(), CONTRACT_PREFIX)


def test_a_proper_report_passes_and_aggregates_totals():
    report = VerificationReport(
        contract=1,
        expected_gates=("a", "b"),
        results=[
            build_result("a", OPTIONAL, ["x"], [], checks_executed=2),
            build_result("b", OPTIONAL, ["y"], ["y: bad"], checks_executed=3),
        ],
    )
    assert report.overall == FAIL, "one gate failed"

    clean = VerificationReport(
        contract=1,
        expected_gates=("a", "b"),
        results=[
            build_result("a", OPTIONAL, ["x"], [], checks_executed=2),
            build_result("b", OPTIONAL, ["y"], [], checks_executed=3),
        ],
    )
    assert clean.overall == PASS
    assert clean.totals()["checks_executed"] == 5
    assert clean.totals()["checked"] == 2


def test_a_report_whose_gates_all_executed_nothing_fails():
    """A suite of gates that each legitimately did nothing is not evidence of anything."""
    report = VerificationReport(
        contract=1,
        expected_gates=("a",),
        results=[ValidationResult(name="a", policy=OPTIONAL, checks_executed=0)],
    )
    assert report.overall == FAIL


def test_a_blocked_gate_makes_the_report_blocked_not_passing():
    report = VerificationReport(
        contract=1,
        expected_gates=("a",),
        results=[
            ValidationResult(
                name="a",
                policy=OPTIONAL,
                discovered=1,
                blocked=1,
                checks_executed=1,
                blocked_status=BLOCKED_NETWORK,
                blocked_reasons=["no network"],
            )
        ],
    )
    assert report.overall == BLOCKED_NETWORK


def test_summarise_reports_the_failing_gates_and_totals():
    summary = summarise(
        [
            build_result("a", OPTIONAL, ["x"], [], checks_executed=1),
            build_result("b", OPTIONAL, ["y"], ["y: no"], checks_executed=1),
        ]
    )
    assert summary["gates"] == 2
    assert summary["failed_gates"] == ["b"]
    assert summary["totals"]["failed"] == 1


# ── Rendering ───────────────────────────────────────────────────────────────


def test_render_includes_the_census_and_every_error():
    result = build_result("g", OPTIONAL, ["a"], ["a: broken"], checks_executed=1)
    text = result.render()
    assert "[FAIL]" in text
    assert "checks_executed" in text or "exec" in text.lower()
    assert "broken" in text


def test_report_render_shows_coverage_and_overall():
    report = VerificationReport(
        contract=1,
        expected_gates=("a",),
        results=[build_result("a", OPTIONAL, ["x"], [], checks_executed=1)],
    )
    text = report.render()
    assert "EXECUTION COVERAGE" in text
    assert "overall: PASS" in text


def test_to_dict_is_json_serialisable():
    import json

    report = VerificationReport(
        contract=1,
        expected_gates=("a",),
        results=[build_result("a", OPTIONAL, ["x"], [], checks_executed=1)],
    )
    json.dumps(report.to_dict())
    json.dumps(report.results[0].to_dict())
