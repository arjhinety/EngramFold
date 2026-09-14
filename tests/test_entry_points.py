"""Entry-point parity: every supported way of invoking validation must do the same work.

This module is the regression test for a specific, observed failure:

    ``python -m package.module`` imported the module, executed nothing, and exited ``0``.

That is indistinguishable from a passing validation, and it is why ``__main__`` guards exist
here. The guard is tested by running each entry point as a **real subprocess** and comparing its
verdict against the in-process result -- on a clean repository and on a broken one, so the test
cannot pass by pinning the answer either way.

A subprocess that exits 0 on a broken repository, or non-zero on a clean one, fails here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.conftest import disable_git_repo, write_text

from engramfold.registry.validate import audit, main, validate
from engramfold.validation import FAIL, GATE_CONTRACT, PASS


def run_module(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the validator the way an operator would, as a real process."""
    return subprocess.run(
        [sys.executable, "-m", "engramfold.registry.validate", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def run_console_script(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the ``engramfold-validate`` console script.

    Resolved through the installed entry point rather than through ``main()``, so the
    pyproject declaration itself is exercised: a console script pointing at the wrong target,
    or at a function that returns without validating, is a real way for this to break.
    """
    return subprocess.run(
        ["engramfold-validate", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def run_cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "engramfold", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def break_repository(root: Path) -> None:
    """Make the repository definitively invalid, in a way no gate can excuse.

    A dangling reference is used because it is immune to the population policy: the registry
    declares a record, and the document that record indexes does not exist.
    """
    write_text(
        root / "registry/datasets.yaml",
        "schema_version: 1\ndatasets:\n  - id: dangling\n"
        "    manifest_path: datasets/manifests/does-not-exist.json\n",
    )


# ── The clean case ───────────────────────────────────────────────────────────


def test_the_in_process_validator_passes_on_the_real_repository(repo_root: Path):
    assert validate(repo_root) == []


def test_the_real_repository_audit_is_a_full_clean_pass(repo_root: Path):
    report = audit(repo_root)
    assert report.overall == PASS, report.render()
    assert report.missing_gates() == []
    assert len(report.results) == len(GATE_CONTRACT)


def test_module_invocation_passes_on_the_real_repository(repo_root: Path):
    result = run_module(repo_root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "overall: PASS" in result.stdout


def test_the_console_script_passes_on_the_real_repository(repo_root: Path):
    result = run_console_script(repo_root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_cli_passes_on_the_real_repository(repo_root: Path):
    result = run_cli(repo_root, "validate")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "overall: PASS" in result.stdout


# ── The broken case ──────────────────────────────────────────────────────────


def test_the_in_process_validator_fails_on_a_broken_repository(cloned_repo: Path):
    break_repository(cloned_repo)
    errors = validate(cloned_repo)
    assert errors, "a dangling reference must be an error"


def test_module_invocation_fails_on_a_broken_repository(cloned_repo: Path):
    break_repository(cloned_repo)
    result = run_module(cloned_repo)
    assert result.returncode != 0, "a broken repository must not exit 0"
    assert result.stdout.strip(), "the subprocess printed nothing, so nothing was validated"
    assert "dangling" in result.stdout


def test_the_console_script_fails_on_a_broken_repository(cloned_repo: Path):
    break_repository(cloned_repo)
    result = run_console_script(cloned_repo)
    assert result.returncode != 0
    assert result.stdout.strip()


def test_the_cli_fails_on_a_broken_repository(cloned_repo: Path):
    break_repository(cloned_repo)
    result = run_cli(cloned_repo, "validate")
    assert result.returncode == 1
    assert "dangling" in result.stdout


# ── Parity ───────────────────────────────────────────────────────────────────


def test_every_entry_point_agrees_on_a_broken_repository(cloned_repo: Path):
    break_repository(cloned_repo)
    in_process = bool(validate(cloned_repo))
    module = run_module(cloned_repo)
    script = run_console_script(cloned_repo)
    cli = run_cli(cloned_repo, "validate")

    assert in_process is True
    assert (module.returncode != 0) is in_process
    assert (script.returncode != 0) is in_process
    assert (cli.returncode != 0) is in_process


def test_every_entry_point_agrees_on_a_clean_repository(cloned_repo: Path):
    in_process = bool(validate(cloned_repo))
    assert in_process is False
    assert run_module(cloned_repo).returncode == 0
    assert run_console_script(cloned_repo).returncode == 0
    assert run_cli(cloned_repo, "validate").returncode == 0


def test_module_invocation_and_console_script_produce_the_same_verdict(repo_root: Path):
    """The two are separately declared entry points, so they can drift apart."""
    module = run_module(repo_root, "--json")
    script = run_console_script(repo_root, "--json")
    assert module.returncode == script.returncode
    module_payload = json.loads(module.stdout)
    script_payload = json.loads(script.stdout)
    assert module_payload["overall"] == script_payload["overall"]
    assert module_payload["ran_gates"] == script_payload["ran_gates"]
    assert module_payload["totals"] == script_payload["totals"]


def test_importing_the_module_is_not_sufficient_to_produce_a_success_signal():
    """The exact bug: importing gives no verdict at all; only calling the validator does."""
    import importlib

    module = importlib.import_module("engramfold.registry.validate")
    assert callable(module.main)
    assert callable(module.validate)
    # `main` accepts an explicit root, so its verdict is a function of that root alone.
    assert module.main(["."]) == 0


def test_the_module_has_a_main_guard():
    """Without it, ``python -m`` imports and exits 0 -- the failure this test exists for."""
    import inspect

    module = __import__("engramfold.registry.validate", fromlist=["validate"])
    source = inspect.getsource(module)
    assert 'if __name__ == "__main__":' in source
    assert "raise SystemExit(main())" in source


def test_the_cli_module_has_a_main_guard():
    import inspect

    module = __import__("engramfold.cli.main", fromlist=["main"])
    source = inspect.getsource(module)
    assert 'if __name__ == "__main__":' in source


def test_the_preflight_module_has_a_main_guard():
    import inspect

    module = __import__("engramfold.preflight", fromlist=["main"])
    source = inspect.getsource(module)
    assert 'if __name__ == "__main__":' in source


def test_the_package_main_module_delegates_to_the_cli():
    import inspect

    module = __import__("engramfold.__main__", fromlist=["main"])
    source = inspect.getsource(module)
    assert 'if __name__ == "__main__":' in source
    assert "from engramfold.cli.main import main" in source


def test_every_declared_console_script_resolves_to_a_real_function(repo_root: Path):
    """A pyproject entry point naming a missing attribute is a broken entry point."""
    import tomllib

    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert scripts, "no console scripts declared"

    for name, target in scripts.items():
        module_name, _, attribute = target.partition(":")
        module = __import__(module_name, fromlist=[attribute])
        resolved = getattr(module, attribute)
        assert callable(resolved), f"{name} -> {target} is not callable"


def test_the_declared_console_scripts_are_the_expected_three(repo_root: Path):
    import tomllib

    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert set(pyproject["project"]["scripts"]) == {
        "engramfold",
        "engramfold-validate",
        "engramfold-preflight",
    }


# ── Exit-code meaning ────────────────────────────────────────────────────────


def test_exit_codes_are_distinct_and_meaningful(repo_root: Path, cloned_repo: Path):
    assert run_module(repo_root).returncode == 0
    break_repository(cloned_repo)
    assert run_module(cloned_repo).returncode == 1
    usage = run_cli(repo_root, "no-such-command")
    assert usage.returncode == 2


def test_a_blocked_verdict_exits_three_not_zero(cloned_repo: Path):
    """Blocked is never a pass, and the exit code must say so.

    The repository is complete and valid but has no git checkout, so every gate passes except
    provenance, which cannot establish the commit, branch or dirty state and reports BLOCKED.
    A definite failure elsewhere would take precedence -- a FAIL is more informative than a
    block -- so this fixture must be otherwise clean for the blocked verdict to be reachable.
    """
    disable_git_repo(cloned_repo)
    result = run_module(cloned_repo)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "BLOCKED" in result.stdout
    assert "overall: BLOCKED_DEPENDENCY" in result.stdout


def test_a_failure_takes_precedence_over_a_block(cloned_repo: Path):
    """A report containing both must read as a failure: it is the more informative verdict."""
    disable_git_repo(cloned_repo)
    break_repository(cloned_repo)
    result = run_module(cloned_repo)
    assert result.returncode == 1, result.stdout
    assert "overall: FAIL" in result.stdout


def test_module_invocation_without_an_argument_uses_the_working_directory(tmp_path: Path):
    """It must not default to the installed package's own repository.

    Defaulting there made the validator report a clean pass over a completely different tree
    than the one the operator was standing in -- a validation that had nothing to do with the
    directory it was asked about.
    """
    from tests.conftest import registry_repo

    root = registry_repo(tmp_path / "cwd-fixture")
    result = run_module(root)  # no positional argument; cwd is the fixture
    assert (
        "registry-fixture" in result.stdout or "FAIL" in result.stdout or "BLOCKED" in result.stdout
    )
    payload = json.loads(run_module(root, "--json").stdout)
    # The gates must be describing the fixture, which has no canonical documents at all.
    documents = next(gate for gate in payload["gates"] if gate["name"] == "documents")
    assert documents["status"] == FAIL


def test_main_returns_the_same_code_as_the_subprocess(repo_root: Path):
    assert main([str(repo_root)]) == run_module(repo_root).returncode


def test_json_output_is_canonical_and_complete(repo_root: Path):
    result = run_module(repo_root, "--json")
    payload = json.loads(result.stdout)
    for key in (
        "verifier_contract",
        "overall",
        "expected_gates",
        "ran_gates",
        "missing_gates",
        "totals",
        "gates",
    ):
        assert key in payload, key
    assert payload["overall"] == PASS
    assert payload["totals"]["checks_executed"] > 0


def test_the_json_report_shows_every_gate_executing(repo_root: Path):
    payload = json.loads(run_module(repo_root, "--json").stdout)
    assert len(payload["gates"]) == len(GATE_CONTRACT)
    for gate in payload["gates"]:
        assert gate["checks_executed"] > 0, gate["name"]
        assert gate["status"] == PASS, gate["name"]


def test_a_failing_gate_appears_in_the_json_report(cloned_repo: Path):
    break_repository(cloned_repo)
    payload = json.loads(run_module(cloned_repo, "--json").stdout)
    assert payload["overall"] == FAIL
    failing = [gate["name"] for gate in payload["gates"] if gate["status"] != PASS]
    assert failing, payload


def test_the_preflight_and_validation_entry_points_are_different_tools(
    repo_root: Path,
):
    """Pinned so the two cannot be confused for one another."""
    from engramfold.preflight import PREFLIGHT_STEPS

    assert set(PREFLIGHT_STEPS) == {
        "format",
        "lint",
        "types",
        "tests",
        "validation",
        "freeze-mechanism",
        "provenance-mechanism",
    }
    assert "validation" in PREFLIGHT_STEPS, (
        "preflight must run the validation gates rather than duplicating them"
    )
