"""The canonical repository health gate.

One command, :func:`main`, reachable as ``engramfold-preflight``. It is the single entry
point that answers "is this repository structurally healthy?" -- so that "the checks pass"
is one checkable statement rather than a claim assembled from whichever commands someone
happened to remember.

Two design choices make it non-vacuous
--------------------------------------

**Every step reports how much it executed, and zero is a failure.** A linter that linted no
files passes; a test run that collected no tests passes; a type checker with nothing to
check passes. All three are indistinguishable from a healthy repository and none of them
are evidence of one. So each step carries an executed count, and a step that cannot
demonstrate it executed anything fails the gate.

**The mechanisms are exercised, not merely invoked.** The freeze step builds a freeze,
verifies it, mutates one byte of the frozen artifact, and asserts verification now fails.
The provenance step creates a temporary git repository, asserts a clean tree reports clean,
modifies a tracked file, asserts the state changed, and restores it. Both are live
adversarial self-tests of the paths this repository depends on, and both work even when the
repository contains no freezes yet -- which is exactly when a naive "verify all freezes"
step would pass by doing nothing.

Steps
-----

``format``   ``ruff format --check`` over the enumerated Python population
``lint``     ``ruff check`` over the same population
``types``    ``mypy`` over the package
``tests``    ``pytest``, with the collected count required to be non-zero
``validation``   every validation gate, run through the module entry point
``freeze-mechanism``    live: build, verify, mutate, assert failure, restore
``provenance-mechanism``  live: clean, modify, assert change, restore
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engramfold import STATUS
from engramfold.datasets.freeze import build_freeze, verify_freeze
from engramfold.errors import EngramFoldError
from engramfold.hashing import content_hash, pretty_json_text
from engramfold.provenance.git_state import capture_git_state

PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"

#: The steps, in order. The list is the contract: a step that does not execute is reported
#: as missing rather than omitted, so deleting a step cannot shorten the report silently.
PREFLIGHT_STEPS: tuple[str, ...] = (
    "format",
    "lint",
    "types",
    "tests",
    "validation",
    "freeze-mechanism",
    "provenance-mechanism",
)

_TOOL_TIMEOUT_SECONDS = 900


@dataclass
class StepResult:
    """One preflight step: what it ran, how much it covered, and its verdict."""

    name: str
    status: str
    executed: int
    detail: str = ""
    errors: list[str] = field(default_factory=list)
    stdout_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "executed": self.executed,
            "detail": self.detail,
            "errors": list(self.errors),
            "stdout_tail": self.stdout_tail,
        }

    def render(self) -> str:
        line = f"  [{self.status:<7}] {self.name:<22} executed={self.executed:<5} {self.detail}"
        if self.errors:
            line += "\n" + "\n".join(f"            {error}" for error in self.errors)
        return line


@dataclass
class PreflightReport:
    """Every step's verdict, and the overall one."""

    root: str
    steps: list[StepResult] = field(default_factory=list)

    def ran(self) -> tuple[str, ...]:
        return tuple(step.name for step in self.steps)

    def missing_steps(self) -> list[str]:
        return [name for name in PREFLIGHT_STEPS if name not in self.ran()]

    @property
    def overall(self) -> str:
        if self.missing_steps():
            return FAIL
        if not self.steps:
            return FAIL
        if any(step.status == FAIL for step in self.steps):
            return FAIL
        # A suite of steps that each executed nothing is not a healthy repository.
        if sum(step.executed for step in self.steps) == 0:
            return FAIL
        return PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "overall": self.overall,
            "expected_steps": list(PREFLIGHT_STEPS),
            "ran_steps": list(self.ran()),
            "missing_steps": self.missing_steps(),
            "total_executed": sum(step.executed for step in self.steps),
            "steps": [step.to_dict() for step in self.steps],
        }

    def render(self) -> str:
        header = f"engramfold preflight  root={self.root}  status={STATUS}"
        body = "\n".join(step.render() for step in self.steps)
        missing = self.missing_steps()
        missing_line = f"\n  missing steps: {sorted(missing)}" if missing else ""
        return (
            f"{header}\n{body}{missing_line}\n\n"
            f"total executed: {sum(step.executed for step in self.steps)}\n"
            f"overall: {self.overall}"
        )


def python_population(root: Path) -> list[Path]:
    """Every Python file this repository owns, as the tools' population.

    Enumerated here rather than left to each tool's defaults, so that "how many files were
    checked?" is answerable and non-zero. A tool given no files reports success, which is
    the vacuous pass this gate is designed to refuse.
    """
    roots = [root / "src", root / "tests", root / "scripts"]
    population: list[Path] = []
    for directory in roots:
        if directory.is_dir():
            population.extend(sorted(directory.rglob("*.py")))
    return population


def resolve_tool(name: str) -> list[str] | None:
    """How to invoke a tool, preferring the executable and falling back to ``-m``.

    A tool installed only as an importable module is still a tool that can run. Refusing to
    find it would make the preflight gate fail for a reason that says nothing about the
    repository, and a gate that fails for irrelevant reasons gets switched off.
    """
    found = shutil.which(name)
    if found is not None:
        return [found]
    try:
        import importlib.util

        if importlib.util.find_spec(name) is not None:
            return [sys.executable, "-m", name]
    except (ImportError, ValueError, ModuleNotFoundError):
        return None
    return None


def _run(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=_TOOL_TIMEOUT_SECONDS,
    )


def _tail(text: str, lines: int = 12) -> str:
    kept = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


#: How pytest reports a collected count, and why more than one shape is parsed.
#:
#: ``--collect-only -q`` prints a per-file tally (``tests/test_x.py: 12``) and *not* the
#: node ids; without ``-q`` it prints node ids; and on some versions and flags it prints a
#: summary line (``471 tests collected in 3.20s``). Parsing only one of the three is how a
#: collection count silently becomes zero -- which this step correctly reports as a failure,
#: but for the wrong reason, and a gate that fails for the wrong reason is a gate nobody
#: trusts. All three are therefore recognised, and recognition is the only thing that
#: counts: an unparsed count stays zero and stays a failure.
_COLLECTED_SUMMARY = re.compile(r"^(\d+)\s+tests?\s+collected", re.MULTILINE)
_COLLECTED_PER_FILE = re.compile(r"^[^\s:]+\.py:\s*(\d+)\s*$", re.MULTILINE)
_COLLECTED_NODE_IDS = re.compile(r"^[^\s:]+\.py::\S+", re.MULTILINE)


def collected_test_count(text: str) -> int:
    """The number of tests pytest said it collected, from whichever shape it printed.

    Zero means "no recognised count", which the caller must treat as a failure rather than
    as an empty suite: the two are different facts and only one of them is a defect in the
    suite.
    """
    summary = _COLLECTED_SUMMARY.search(text)
    if summary:
        return int(summary.group(1))
    per_file = _COLLECTED_PER_FILE.findall(text)
    if per_file:
        return sum(int(count) for count in per_file)
    return len(_COLLECTED_NODE_IDS.findall(text))


def step_format(root: Path, population: list[Path]) -> StepResult:
    """``ruff format --check`` over an explicit file list."""
    executed = len(population)
    if executed == 0:
        return StepResult(
            "format",
            FAIL,
            0,
            "no Python files were enumerated, so formatting was not checked",
        )
    ruff = resolve_tool("ruff")
    if ruff is None:
        return StepResult("format", FAIL, 0, "ruff is not installed, so formatting was not checked")
    result = _run([*ruff, "format", "--check", *[str(p) for p in population]], root)
    if result.returncode != 0:
        return StepResult(
            "format",
            FAIL,
            executed,
            f"{executed} file(s) checked, formatting differences found",
            errors=[_tail(result.stdout) or _tail(result.stderr)],
        )
    return StepResult("format", PASS, executed, f"{executed} file(s) already formatted")


def step_lint(root: Path, population: list[Path]) -> StepResult:
    """``ruff check`` over the same explicit file list."""
    executed = len(population)
    if executed == 0:
        return StepResult(
            "lint", FAIL, 0, "no Python files were enumerated, so linting was not run"
        )
    ruff = resolve_tool("ruff")
    if ruff is None:
        return StepResult("lint", FAIL, 0, "ruff is not installed, so linting was not run")
    result = _run([*ruff, "check", *[str(p) for p in population]], root)
    if result.returncode != 0:
        return StepResult(
            "lint",
            FAIL,
            executed,
            f"{executed} file(s) checked, lint findings",
            errors=[_tail(result.stdout) or _tail(result.stderr)],
        )
    return StepResult("lint", PASS, executed, f"{executed} file(s) clean")


def step_types(root: Path) -> StepResult:
    """``mypy`` over the package, requiring a non-zero checked-file count."""
    package = root / "src/engramfold"
    if not package.is_dir():
        return StepResult("types", FAIL, 0, "src/engramfold does not exist, so nothing was checked")
    result = _run([sys.executable, "-m", "mypy", "--no-incremental", str(package)], root)
    text = f"{result.stdout}\n{result.stderr}"
    if "No module named mypy" in text:
        return StepResult("types", FAIL, 0, "mypy is not installed, so types were not checked")
    match = re.search(r"no issues found in (\d+) source file", text)
    checked = int(match.group(1)) if match else 0
    if result.returncode != 0:
        return StepResult(
            "types",
            FAIL,
            checked,
            "type errors found",
            errors=[_tail(text)],
        )
    if checked == 0:
        # Succeeding over zero files is not a type check.
        return StepResult(
            "types",
            FAIL,
            0,
            "mypy reported success but named no source files, so it checked nothing",
            errors=[_tail(text)],
        )
    return StepResult("types", PASS, checked, f"{checked} source file(s) type-checked")


def step_tests(root: Path) -> StepResult:
    """``pytest``, with the collected count required to be non-zero.

    Collection is measured separately from the run so that "zero tests collected" is
    visible as a distinct failure rather than as an ambiguous green run. The count is read
    from whichever shape pytest printed (:func:`collected_test_count`), because parsing one
    shape only would report a healthy suite as collecting nothing.
    """
    if not (root / "tests").is_dir():
        return StepResult("tests", FAIL, 0, "tests/ does not exist, so no test could run")

    collect = _run([sys.executable, "-m", "pytest", "--collect-only", "-q"], root)
    collect_text = f"{collect.stdout}\n{collect.stderr}"
    collected = collected_test_count(collect_text)
    if collect.returncode != 0:
        return StepResult(
            "tests",
            FAIL,
            collected,
            "test collection failed",
            errors=[_tail(collect_text)],
        )
    if collected == 0:
        return StepResult(
            "tests",
            FAIL,
            0,
            "pytest collected zero tests; a suite that runs nothing is not evidence",
            errors=[_tail(collect_text)],
        )

    result = _run([sys.executable, "-m", "pytest", "-q"], root)
    text = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        return StepResult(
            "tests",
            FAIL,
            collected,
            f"{collected} test(s) collected, suite failed",
            errors=[_tail(text, 25)],
        )
    return StepResult("tests", PASS, collected, f"{collected} test(s) collected and passing")


def step_validation(root: Path) -> StepResult:
    """Every validation gate, run through the module entry point as a real subprocess.

    Run as a subprocess on purpose: this is also the check that
    ``python -m engramfold.registry.validate`` actually executes rather than importing and
    exiting 0. The JSON it prints is parsed and its gate count asserted.
    """
    result = _run(
        [sys.executable, "-m", "engramfold.registry.validate", "--json"],
        root,
    )
    text = f"{result.stdout}\n{result.stderr}"
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return StepResult(
            "validation",
            FAIL,
            0,
            "the validator produced no parseable report, so it cannot be shown to have run",
            errors=[_tail(text)],
        )

    gates = payload.get("gates") or []
    executed = int(payload.get("totals", {}).get("checks_executed", 0))
    if not gates:
        return StepResult(
            "validation",
            FAIL,
            0,
            "the validator reported zero gates, so nothing was validated",
            errors=[_tail(text)],
        )
    if executed == 0:
        return StepResult(
            "validation",
            FAIL,
            0,
            "the validator executed zero checks across its gates",
            errors=[_tail(text)],
        )
    if payload.get("overall") != "PASS":
        return StepResult(
            "validation",
            FAIL,
            executed,
            f"{len(gates)} gate(s), {executed} check(s), overall {payload.get('overall')}",
            errors=[_tail(text)],
        )
    return StepResult(
        "validation",
        PASS,
        executed,
        f"{len(gates)} gate(s), {executed} check(s) executed",
    )


def step_freeze_mechanism(root: Path) -> StepResult:
    """Exercise the freeze path adversarially against a temporary fixture.

    Four assertions, each of which would catch a different way the freeze system could be
    decorative: the freeze must build with no errors; verification must pass on unchanged
    bytes; verification must *fail* after a single byte changes; and verification must pass
    again once the byte is restored. The third is the one that matters -- a freeze check that
    cannot detect a changed artifact is worse than no check, because it is trusted.

    The manifest is written to the fixture before it is frozen. It has to be: verification
    re-reads the manifest a freeze pins, and a freeze whose manifest is absent is reported as
    unverifiable -- correctly, because the dataset can no longer be described or re-checked.
    Building a freeze from an in-memory manifest and then verifying it therefore fails, which
    is how this step behaved the first time it was run end to end.
    """
    executed = 0
    errors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "datasets/manifests").mkdir(parents=True)
        artifact = base / "datasets/records.jsonl"
        artifact.write_bytes(b'{"a": 1}\n')
        digest = content_hash(artifact)

        manifest: dict[str, Any] = {
            "dataset_manifest_version": 1,
            "schema_version": 1,
            "dataset_id": "preflight-fixture",
            "dataset_version": "v1",
            "source_type": "local",
            "split": "train",
            "license": "CC0-1.0",
            "record_count": 1,
            "adapter_version": "preflight",
            "deduplication_policy": "none",
            "contamination_policy": "not assessed",
            "source_files": ["datasets/records.jsonl"],
            "source_file_hashes": {"datasets/records.jsonl": digest},
            "processed_files": ["datasets/records.jsonl"],
            "processed_file_hashes": {"datasets/records.jsonl": digest},
            "processing_command": "engramfold preflight fixture",
            "processing_code_revision": "0" * 40,
            "processing_config_hash": "1" * 64,
            "random_seed": 0,
        }

        manifest_path = base / "datasets/manifests/preflight-fixture.json"
        manifest_path.write_text(pretty_json_text(manifest), encoding="utf-8")

        executed += 1
        built = build_freeze(
            base,
            manifest,
            "datasets/manifests/preflight-fixture.json",
            created_at="1970-01-01T00:00:00+00:00",
        )
        if built.errors:
            errors.append(f"freeze mechanism: building the fixture freeze failed: {built.errors}")
            return StepResult("freeze-mechanism", FAIL, executed, "fixture freeze failed", errors)

        executed += 1
        clean = verify_freeze(base, built.freeze, path=built.output_path)
        if not clean.ok:
            errors.append(f"freeze mechanism: an unchanged freeze did not verify: {clean.errors}")
        if clean.artifacts_checked != 1:
            errors.append(
                f"freeze mechanism: verification checked {clean.artifacts_checked} artifact(s), "
                f"expected exactly 1"
            )

        executed += 1
        original = artifact.read_bytes()
        artifact.write_bytes(b'{"a": 2}\n')
        mutated = verify_freeze(base, built.freeze, path=built.output_path)
        if mutated.ok:
            errors.append(
                "freeze mechanism: a one-byte change to a frozen artifact did NOT fail "
                "verification, so the freeze cannot detect that its input changed"
            )
        if mutated.artifacts_changed != 1:
            errors.append(
                f"freeze mechanism: expected 1 changed artifact, verification reported "
                f"{mutated.artifacts_changed}"
            )

        executed += 1
        artifact.write_bytes(original)
        restored = verify_freeze(base, built.freeze, path=built.output_path)
        if not restored.ok:
            errors.append(
                f"freeze mechanism: verification did not return to a passing state after the "
                f"artifact was restored: {restored.errors}"
            )

    if errors:
        return StepResult("freeze-mechanism", FAIL, executed, f"{executed} assertion(s)", errors)
    return StepResult(
        "freeze-mechanism", PASS, executed, f"{executed} assertion(s) over a live fixture"
    )


def step_provenance_mechanism(root: Path) -> StepResult:
    """Exercise git provenance against a temporary repository.

    The three-state test the specification asks for, run as part of the health gate rather
    than only in the suite: a clean repository must report clean; modifying a tracked file
    must change the recorded provenance and mark the tree dirty; restoring the file must
    return the provenance to clean. A provenance mechanism that cannot tell those apart
    cannot establish what code produced anything.
    """
    executed = 0
    errors: list[str] = []
    git = shutil.which("git")
    if git is None:
        return StepResult(
            "provenance-mechanism",
            FAIL,
            0,
            "git is not installed, so provenance could not be exercised",
        )

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        try:
            for command in (
                [git, "init", "-q"],
                [git, "config", "user.email", "preflight@engramfold.invalid"],
                [git, "config", "user.name", "EngramFold preflight"],
                [git, "config", "commit.gpgsign", "false"],
            ):
                init = _run(command, base)
                if init.returncode != 0:
                    return StepResult(
                        "provenance-mechanism",
                        FAIL,
                        executed,
                        "could not create a git fixture",
                        errors=[_tail(init.stderr) or _tail(init.stdout)],
                    )
            tracked = base / "tracked.txt"
            tracked.write_text("original\n", encoding="utf-8")
            add = _run([git, "add", "tracked.txt"], base)
            commit = _run([git, "commit", "-q", "-m", "fixture"], base)
            if add.returncode != 0 or commit.returncode != 0:
                return StepResult(
                    "provenance-mechanism",
                    FAIL,
                    executed,
                    "could not commit the git fixture",
                    errors=[_tail(commit.stderr) or _tail(commit.stdout)],
                )

            executed += 1
            clean_state = capture_git_state(base)
            if not clean_state.available:
                errors.append(
                    f"provenance mechanism: fixture checkout unusable: {clean_state.error}"
                )
            if clean_state.dirty is not False:
                errors.append(
                    f"provenance mechanism: a freshly committed tree reported dirty="
                    f"{clean_state.dirty!r}, expected False"
                )
            if not clean_state.clean:
                errors.append("provenance mechanism: a clean repository did not report as clean")

            executed += 1
            tracked.write_text("modified\n", encoding="utf-8")
            dirty_state = capture_git_state(base)
            if dirty_state.dirty is not True:
                errors.append(
                    f"provenance mechanism: modifying a tracked file left dirty="
                    f"{dirty_state.dirty!r}, expected True"
                )
            if dirty_state.dirty_diff_hash == clean_state.dirty_diff_hash:
                errors.append(
                    "provenance mechanism: the dirty diff hash did not change when a tracked file "
                    "changed, so two different dirty states are indistinguishable"
                )
            if "tracked.txt" not in dirty_state.dirty_file_list:
                errors.append(
                    f"provenance mechanism: the modified file is absent from dirty_file_list "
                    f"{dirty_state.dirty_file_list}"
                )

            executed += 1
            tracked.write_text("original\n", encoding="utf-8")
            restored_state = capture_git_state(base)
            if restored_state.dirty is not False:
                errors.append(
                    f"provenance mechanism: restoring the file left dirty="
                    f"{restored_state.dirty!r}, expected False"
                )
            if restored_state.dirty_diff_hash != clean_state.dirty_diff_hash:
                errors.append(
                    "provenance mechanism: provenance did not return to the clean state after the "
                    "file was restored, so a dirty state leaves a permanent trace"
                )
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"provenance mechanism: fixture setup failed: {exc}")

    if errors:
        return StepResult(
            "provenance-mechanism", FAIL, executed, f"{executed} assertion(s)", errors
        )
    return StepResult(
        "provenance-mechanism",
        PASS,
        executed,
        f"{executed} assertion(s): clean, modified, restored",
    )


def run_preflight(root: Path | str | None = None) -> PreflightReport:
    """Run every preflight step and return the report."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    report = PreflightReport(root=base.as_posix())
    population = python_population(base)
    report.steps.append(step_format(base, population))
    report.steps.append(step_lint(base, population))
    report.steps.append(step_types(base))
    report.steps.append(step_tests(base))
    report.steps.append(step_validation(base))
    report.steps.append(step_freeze_mechanism(base))
    report.steps.append(step_provenance_mechanism(base))
    return report


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``engramfold-preflight``. Exits 0 only on an overall PASS."""
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    positional = [arg for arg in args if not arg.startswith("-")]
    root = Path(positional[0]) if positional else None

    try:
        report = run_preflight(root)
    except EngramFoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if as_json:
        print(pretty_json_text(report.to_dict()))
    else:
        print(report.render())
    return 0 if report.overall == PASS else 1


if __name__ == "__main__":
    # Without this guard the module would import and exit 0, which is the failure mode the
    # whole substrate is built around. tests/test_entry_points.py executes this command.
    raise SystemExit(main())


__all__ = [
    "FAIL",
    "PASS",
    "PREFLIGHT_STEPS",
    "SKIPPED",
    "PreflightReport",
    "StepResult",
    "collected_test_count",
    "main",
    "python_population",
    "resolve_tool",
    "run_preflight",
    "step_format",
    "step_freeze_mechanism",
    "step_lint",
    "step_provenance_mechanism",
    "step_tests",
    "step_types",
    "step_validation",
]
