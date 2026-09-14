"""Shared fixtures for the EngramFold test suite.

Two kinds of fixture, and the difference matters.

*Synthetic* fixtures build a repository from nothing. They are how the adversarial cases are
tested, because a fixture can be broken in exactly one way at a time -- which is the only way
to prove that a validator fails for the reason claimed rather than for some other reason.

*Cloned* fixtures copy the real repository into a temporary directory and commit it, so the
full audit can be exercised end to end. They are how entry-point parity is tested, because
parity is only meaningful against a repository that validates clean.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Copied by :func:`cloned_repo`. Everything else (caches, the object store) is skipped, so
#: the clone is small and the copied set is explicit.
CLONE_IGNORE = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "*.egg-info",
)

#: The population policy every synthetic fixture starts from.
OPTIONAL_POLICIES: dict[str, str] = {
    "datasets": "OPTIONAL",
    "dataset_freezes": "OPTIONAL",
    "models": "OPTIONAL",
    "experiments": "OPTIONAL",
    "artifacts": "OPTIONAL",
    "reports": "OPTIONAL",
    "claims": "OPTIONAL",
    "provenance_records": "OPTIONAL",
}

ZERO_REASON = "synthetic fixture: this population is empty on purpose"

REQUIRED_POLICIES: dict[str, str] = dict.fromkeys(OPTIONAL_POLICIES, "REQUIRED_NONEMPTY")


def write_text(path: Path, text: str) -> Path:
    """Write ``text`` to ``path``, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_yaml(path: Path, payload: str) -> Path:
    """Write raw YAML text.

    Deliberately not ``yaml.safe_dump``: fixtures need to be able to write *malformed* YAML,
    and several adversarial cases depend on that.
    """
    return write_text(path, payload)


def registry_yaml(records_key: str, records: list[str], *, schema_version: int = 1) -> str:
    """A registry index file with raw record blocks.

    ``records`` entries are raw YAML fragments (indented under the key), so a fixture can
    write a record with a wrong field name or a duplicate id when the test needs one.
    """
    lines = [f"schema_version: {schema_version}", f"{records_key}:"]
    if not records:
        lines[-1] = f"{records_key}: []"
    else:
        for record in records:
            lines.append(record.rstrip("\n"))
    return "\n".join(lines) + "\n"


def populations_yaml(
    *,
    phase: str,
    policies: dict[str, str] | None = None,
    zero_reasons: dict[str, str] | None = None,
    schema_version: int = 1,
) -> str:
    """A populations document.

    ``policies`` overrides the default OPTIONAL set, so a test can declare a population
    REQUIRED_NONEMPTY while it is empty -- the case that must fail.
    """
    chosen = dict(OPTIONAL_POLICIES if policies is None else policies)
    reasons = dict.fromkeys(chosen, ZERO_REASON)
    if zero_reasons is not None:
        reasons.update(zero_reasons)
    lines = [f"schema_version: {schema_version}", f'phase: "{phase}"', "populations:"]
    for name, policy in chosen.items():
        lines.append(f"  {name}:")
        lines.append(f"    policy: {policy}")
        if policy == "OPTIONAL":
            reason = reasons.get(name, ZERO_REASON)
            lines.append(f'    zero_allowed_reason: "{reason}"')
    return "\n".join(lines) + "\n"


def registry_repo(
    root: Path,
    *,
    phase: str = "PRE-EXPERIMENT / INFRASTRUCTURE ONLY",
    policies: dict[str, str] | None = None,
    status_line: str | None = None,
    datasets: list[str] | None = None,
    experiments: list[str] | None = None,
    artifacts: list[str] | None = None,
    reports: list[str] | None = None,
    claims: list[str] | None = None,
) -> Path:
    """A minimal repository containing only the registry and a README.

    Enough for the registry, datasets, freezes, experiments, artifacts and provenance gates
    to have something to read, and deliberately not enough for the substrate or documents
    gates -- so a test about registry behaviour is not entangled with layout requirements.
    """
    write_yaml(
        root / "registry/populations.yaml",
        populations_yaml(phase=phase, policies=policies),
    )
    write_yaml(
        root / "registry/datasets.yaml",
        registry_yaml("datasets", datasets or []),
    )
    write_yaml(root / "registry/models.yaml", registry_yaml("models", []))
    write_yaml(
        root / "registry/experiments.yaml",
        registry_yaml("experiments", experiments or []),
    )
    write_yaml(
        root / "registry/artifacts.yaml",
        registry_yaml("artifacts", artifacts or []),
    )
    write_yaml(root / "registry/reports.yaml", registry_yaml("reports", reports or []))
    write_yaml(
        root / "registry/claims.yaml",
        registry_yaml("claims", claims or []),
    )
    write_text(
        root / "README.md",
        f"# Fixture\n\n**STATUS: {status_line or phase}**\n\nSynthetic test repository.\n",
    )
    # The canonical directories exist even when they are empty. A gate that reports "the
    # directory does not exist" is reporting a real defect, so a fixture that omitted them
    # would make every empty-population test fail for the wrong reason.
    for relative in (
        "datasets/manifests",
        "datasets/freezes",
        "experiments/manifests",
        "experiments/freezes",
        "artifacts/manifests",
        "provenance/records",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return root


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run git in ``cwd``, never raising on a non-zero exit."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def init_git_repo(root: Path, *, commit: bool = True) -> Path:
    """Initialise a git repository and make one commit.

    ``commit=False`` leaves the repository with an unborn HEAD, which is the case the
    provenance gate must treat as *unknowable* rather than as clean.
    """
    root.mkdir(parents=True, exist_ok=True)
    git("init", "-q", cwd=root)
    git("config", "user.email", "fixture@engramfold.invalid", cwd=root)
    git("config", "user.name", "EngramFold fixture", cwd=root)
    git("config", "commit.gpgsign", "false", cwd=root)
    if commit:
        write_text(root / ".fixture-marker", "fixture\n")
        git("add", "-A", cwd=root)
        git("commit", "-q", "-m", "fixture", cwd=root)
    return root


@pytest.fixture
def repo_root() -> Path:
    """The real repository root."""
    return REPO_ROOT


@pytest.fixture
def cloned_repo(tmp_path: Path) -> Iterator[Path]:
    """A committed copy of the real repository.

    The full audit is expected to pass against this. If it does not, a change to the real
    repository has broken something the parity tests depend on, and the failure surfaces here
    with a clean explanation rather than as a confusing diff in the real tree.
    """
    destination = tmp_path / "clone"
    shutil.copytree(REPO_ROOT, destination, ignore=CLONE_IGNORE)
    init_git_repo(destination)
    yield destination
    shutil.rmtree(destination, ignore_errors=True)


@pytest.fixture
def empty_git_repo(tmp_path: Path) -> Path:
    """A git repository with no commits -- HEAD is unborn."""
    root = tmp_path / "unborn"
    root.mkdir()
    return init_git_repo(root, commit=False)


def disable_git_repo(root: Path) -> Path:
    """Make a checkout un-findable by git without deleting its object store.

    ``shutil.rmtree(root / ".git")`` fails on Windows: git marks its object files read-only,
    and removing them raises ``PermissionError``. Renaming is enough, because git searches
    upward from the directory it is given, finds no repository, and reports an unknowable
    checkout -- which is the state under test. The clone keeps its ``pyproject.toml``, so root
    resolution still lands on the clone rather than wandering off to an ancestor.
    """
    git_dir = root / ".git"
    if git_dir.exists():
        git_dir.rename(root / ".git-disabled")
    return root


@pytest.fixture
def registry_root(tmp_path: Path) -> Path:
    """A minimal registry-only repository."""
    root = tmp_path / "registry-fixture"
    root.mkdir()
    return registry_repo(root)


def sha256_of(text: str) -> str:
    """Digest helper for fixtures, routed through the canonical implementation."""
    from engramfold.hashing import sha256_bytes

    return sha256_bytes(text.encode("utf-8"))
