"""The health gate's steps, and the two things that keep it from being decorative.

The gate itself is one command, ``engramfold-preflight``. This module tests the parts a
one-command gate cannot test about itself:

**A step that executed nothing fails.** A linter that linted no files passes; a test run that
collected no tests passes. Both are indistinguishable from a healthy repository, so each step
carries an executed count and zero is a failure. That is asserted here against inputs where the
expected outcome is a failure, because that is the direction that matters and the direction that
is cheap to check.

**The two mechanism self-tests can fail.** ``freeze-mechanism`` builds a freeze, mutates one
byte, and asserts verification now fails; ``provenance-mechanism`` asserts a clean checkout
reads clean, a modified tracked file reads dirty, and restoring it reads clean again. A self-test
that cannot fail is decoration, so both are pointed at a deliberately broken version of the
mechanism they exercise and must report FAIL.

The expensive steps are not re-run here: ``format``, ``lint``, ``types`` and ``tests`` over the
real repository are exactly what ``engramfold-preflight`` does, and running the whole suite
inside a unit test would make the suite take twice as long for no additional evidence. What is
tested here is the part that can be wrong in a way nobody would notice -- the parsing of what the
tools said.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.conftest import REPO_ROOT, registry_repo

from engramfold import preflight
from engramfold.preflight import (
    FAIL,
    PASS,
    PREFLIGHT_STEPS,
    PreflightReport,
    StepResult,
    collected_test_count,
    main,
    python_population,
    resolve_tool,
    run_preflight,
    step_format,
    step_freeze_mechanism,
    step_lint,
    step_tests,
    step_types,
    step_validation,
)


def stub_steps(monkeypatch: pytest.MonkeyPatch, *, failing: str | None = None) -> None:
    """Replace every step with a stub, so the report's aggregation can be tested cheaply."""

    def make(name: str):
        def step(*args: Any, **kwargs: Any) -> StepResult:
            status = FAIL if name == failing else PASS
            return StepResult(name, status, 1, "stub")

        return step

    for name in PREFLIGHT_STEPS:
        monkeypatch.setattr(preflight, f"step_{name.replace('-', '_')}", make(name))


def collected_test_function_count(path: Path) -> int:
    """How many top-level ``test_`` functions the file defines, from its own source."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sum(
        1
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    )


# ── The report's aggregation ───────────────────────────────────────────────


def test_run_preflight_runs_every_step_in_the_declared_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The step list is the contract, so the steps that ran are compared against it."""
    stub_steps(monkeypatch)
    report = run_preflight(tmp_path)
    assert report.ran() == PREFLIGHT_STEPS
    assert report.missing_steps() == []
    assert report.overall == PASS
    assert report.to_dict()["total_executed"] == len(PREFLIGHT_STEPS)


def test_a_step_that_does_not_run_is_reported_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A missing step must fail the report rather than shorten it."""
    stub_steps(monkeypatch)
    report = run_preflight(tmp_path)
    report.steps = report.steps[:-1]
    assert report.missing_steps() == [PREFLIGHT_STEPS[-1]]
    assert report.overall == FAIL
    assert "missing steps" in report.render()


def test_one_failing_step_fails_the_whole_report():
    report = PreflightReport(root="x", steps=[StepResult("format", FAIL, 1, "findings")])
    assert report.overall == FAIL


def test_a_suite_of_steps_that_executed_nothing_fails():
    """Every step green and nothing executed is not a healthy repository."""
    report = PreflightReport(
        root="x", steps=[StepResult(name, PASS, 0) for name in PREFLIGHT_STEPS]
    )
    assert report.overall == FAIL


def test_an_empty_report_fails():
    assert PreflightReport(root="x").overall == FAIL


def test_a_step_dict_carries_what_it_ran_and_what_it_covered():
    step = StepResult("lint", FAIL, 3, "3 file(s) checked, lint findings", errors=["E501"])
    payload = step.to_dict()
    assert payload["name"] == "lint"
    assert payload["executed"] == 3
    assert payload["errors"] == ["E501"]
    assert not step.ok


# ── What the steps are pointed at ──────────────────────────────────────────


def test_the_population_is_enumerated_explicitly_and_is_not_empty():
    population = python_population(REPO_ROOT)
    assert population, "the population is empty, so format and lint would check nothing"
    assert all(path.suffix == ".py" for path in population)
    relative = {path.relative_to(REPO_ROOT).as_posix() for path in population}
    assert "src/engramfold/preflight.py" in relative
    assert "tests/test_preflight.py" in relative


def test_an_empty_population_is_empty_on_a_fixture_with_no_python(tmp_path: Path):
    assert python_population(tmp_path) == []


def test_resolve_tool_prefers_the_executable_and_falls_back_to_the_module(
    monkeypatch: pytest.MonkeyPatch,
):
    """Both paths are exercised, because only one of them is what a developer's shell uses.

    ``ruff`` is installed as an importable module here but is not on ``PATH``, so the fallback
    is the path that actually runs -- a fallback nobody tests is a fallback that breaks.
    """
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert resolve_tool("ruff") == ["/usr/bin/ruff"]

    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    fallback = resolve_tool("ruff")
    assert fallback == [sys.executable, "-m", "ruff"], fallback


def test_resolve_tool_returns_none_for_a_tool_that_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(preflight.shutil, "which", lambda name: None)
    assert resolve_tool("a-tool-that-does-not-exist") is None


# ─ Reading what the tools said ────────────────────────────────────────────


def test_the_collected_count_parser_handles_every_shape_pytest_prints():
    assert collected_test_count("tests/test_a.py: 12\ntests/test_b.py: 3\n") == 15
    assert collected_test_count("471 tests collected in 3.20s\n") == 471
    assert collected_test_count("tests/test_a.py::test_one\ntests/test_a.py::test_two\n") == 2
    assert collected_test_count("no tests ran\n") == 0
    assert collected_test_count("") == 0


def test_the_collected_count_parser_agrees_with_real_pytest_output():
    """The parser is pointed at this module's own collection, and against the source of truth.

    ``--collect-only -q`` prints a per-file tally rather than node ids, which is the shape that
    made the previous implementation count zero collected tests and fail a healthy suite.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_preflight.py",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    counted = collected_test_count(f"{result.stdout}\n{result.stderr}")
    expected = collected_test_function_count(Path(__file__))
    assert counted == expected, (counted, expected, result.stdout)


# ── A step with nothing to check fails ─────────────────────────────────────


def test_format_and_lint_fail_when_the_population_is_empty(tmp_path: Path):
    empty = python_population(tmp_path)
    formatted = step_format(tmp_path, empty)
    linted = step_lint(tmp_path, empty)
    assert formatted.status == FAIL and formatted.executed == 0
    assert linted.status == FAIL and linted.executed == 0
    assert "no Python files" in formatted.detail + linted.detail


def test_types_fails_when_the_package_is_absent(tmp_path: Path):
    result = step_types(tmp_path)
    assert result.status == FAIL
    assert result.executed == 0
    assert "src/engramfold does not exist" in result.detail


def test_tests_fails_when_there_are_no_tests(tmp_path: Path):
    result = step_tests(tmp_path)
    assert result.status == FAIL
    assert result.executed == 0
    assert "tests/ does not exist" in result.detail


# ── Reading the validator's report ─────────────────────────────────────────


def test_step_validation_runs_the_gates_and_reports_the_count(repo_root: Path):
    result = step_validation(repo_root)
    assert result.status == PASS, result.render()
    assert result.executed > 0, "the validator executed no checks across its gates"
    assert "gate(s)" in result.detail and "check(s)" in result.detail


def test_step_validation_fails_when_the_gates_fail(tmp_path: Path):
    """It must parse a real report and report the verdict, not merely fail to start."""
    root = registry_repo(tmp_path / "fixture")
    result = step_validation(root)
    assert result.status == FAIL
    assert result.executed > 0, "it gave up before parsing the report"
    assert "overall FAIL" in result.detail or "FAIL" in result.detail


def test_step_validation_fails_when_the_validator_prints_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Unparseable output must fail loudly: it cannot be shown that anything ran."""
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda command, cwd: subprocess.CompletedProcess(
            args=command, returncode=0, stdout="not a report", stderr=""
        ),
    )
    result = step_validation(tmp_path)
    assert result.status == FAIL
    assert result.executed == 0
    assert "no parseable report" in result.detail


# ── The two mechanism self-tests ───────────────────────────────────────────


def test_the_freeze_mechanism_step_passes_against_a_live_fixture(tmp_path: Path):
    """The live self-test: build, verify, mutate, restore -- four executed assertions."""
    result = step_freeze_mechanism(tmp_path)
    assert result.status == PASS, result.render()
    assert result.executed == 4, result.render()


def test_the_freeze_mechanism_step_fails_when_verification_cannot_detect_a_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A self-test that cannot fail is decoration, so verification is broken on purpose."""

    def always_ok(root: Path, freeze: Any, *, path: str = "") -> Any:
        return SimpleNamespace(ok=True, artifacts_checked=1, artifacts_changed=0, errors=[])

    monkeypatch.setattr(preflight, "verify_freeze", always_ok)
    result = step_freeze_mechanism(tmp_path)
    assert result.status == FAIL
    assert result.executed > 0
    assert any("did NOT fail verification" in error for error in result.errors), result.errors


def test_the_provenance_mechanism_step_passes_against_a_live_git_fixture(tmp_path: Path):
    result = preflight.step_provenance_mechanism(tmp_path)
    if preflight.shutil.which("git") is None:  # pragma: no cover - git is required here
        assert result.status == FAIL
        assert "git is not installed" in result.detail
        return
    assert result.status == PASS, result.render()
    assert result.executed == 3, result.render()


def test_the_provenance_mechanism_step_fails_when_provenance_never_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """If a modified tracked file does not change the recorded state, the step must fail."""

    def unchanging_state(root: Path) -> Any:
        return SimpleNamespace(
            available=True,
            commit="0" * 40,
            branch="main",
            dirty=False,
            clean=True,
            dirty_file_list=[],
            dirty_diff_hash="0" * 64,
            error=None,
        )

    monkeypatch.setattr(preflight, "capture_git_state", unchanging_state)
    result = preflight.step_provenance_mechanism(tmp_path)
    assert result.status == FAIL
    assert result.executed > 0
    assert result.errors, result.render()


# ── The entry point ────────────────────────────────────────────────────────


def test_main_exits_zero_only_when_every_step_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    stub_steps(monkeypatch)
    assert main([str(tmp_path)]) == 0
    assert "overall: PASS" in capsys.readouterr().out

    stub_steps(monkeypatch, failing="lint")
    assert main([str(tmp_path)]) == 1


def test_main_json_output_is_canonical_and_lists_every_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    stub_steps(monkeypatch)
    assert main([str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["overall"] == PASS
    assert payload["expected_steps"] == list(PREFLIGHT_STEPS)
    assert payload["ran_steps"] == list(PREFLIGHT_STEPS)
    assert payload["missing_steps"] == []
    assert len(payload["steps"]) == len(PREFLIGHT_STEPS)
    assert payload["total_executed"] == len(PREFLIGHT_STEPS)


def test_the_preflight_module_cannot_be_imported_and_exit_zero():
    """Without the guard, ``python -m engramfold.preflight`` runs nothing and exits 0."""
    source = (REPO_ROOT / "src/engramfold/preflight.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "SystemExit(main())" in source
