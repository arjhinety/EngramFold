"""The command line: every command's behaviour, and the exit code that carries it.

Exit codes are the contract an operator automates against, so they are asserted rather than the
presence of some text: ``0`` pass, ``1`` failure, ``2`` usage error, ``3`` blocked. Whichever
command is run, the process is a real subprocess with an explicit ``--repo``, because the defect
this suite was written after was a command that reported on a different repository than the one
it was pointed at.

Where a command can succeed by doing nothing, the test asserts that it does not:

* ``verify-freeze`` with no freezes is a failure, not an empty success;
* ``freeze dataset`` without ``--write`` must leave no file behind, and must say it was a dry
  run rather than print a freeze as though it had been recorded;
* ``hash`` on a missing path fails instead of printing a digest of nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.conftest import disable_git_repo
from tests.test_dataset_freeze import MANIFEST_PATH, make_dataset
from tests.test_experiments import draft_manifest

from engramfold.cli.main import EXIT_BLOCKED, EXIT_FAIL, EXIT_OK, EXIT_USAGE, main
from engramfold.hashing import content_hash

DATASET_MANIFEST = MANIFEST_PATH
EXPERIMENT_MANIFEST = "experiments/manifests/example.json"
CREATED_AT = "2026-01-01T00:00:00+00:00"


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI as an operator would: a real process, an explicit repository."""
    return subprocess.run(
        [sys.executable, "-m", "engramfold", "--repo", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )


def run_console(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """The same command through the console script, which is a different entry point."""
    return subprocess.run(
        ["engramfold", "--repo", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(root),
    )


def payload(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


@pytest.fixture
def cli_repo(cloned_repo: Path) -> Path:
    """A committed clone of the real repository, with nothing added to it.

    It is a clone rather than a synthetic tree, so a command that depends on the repository
    being valid -- ``validate``, ``provenance``, ``hash`` -- is exercised against something
    that really is valid. Manifests are deliberately *not* added here: an unindexed manifest is
    a validation failure, because the datasets gate reports it as orphaned, and that would make
    every test in this module fail for that reason instead of its own.
    """
    return cloned_repo


@pytest.fixture
def manifest_repo(cloned_repo: Path) -> Path:
    """A committed clone carrying one dataset manifest and one experiment manifest.

    Used by the freeze commands, which read a manifest and do not validate the registry.
    """
    manifest = make_dataset(cloned_repo)
    target = cloned_repo / DATASET_MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    experiment = cloned_repo / EXPERIMENT_MANIFEST
    experiment.parent.mkdir(parents=True, exist_ok=True)
    experiment.write_text(
        json.dumps(draft_manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return cloned_repo


# ── Identity, version and usage ────────────────────────────────────────────


def test_identity_reports_the_project_and_claims_no_results(cli_repo: Path):
    result = run(cli_repo, "identity")
    assert result.returncode == EXIT_OK, result.stderr
    assert "STATUS:" in result.stdout
    assert "claims no results" in result.stdout
    report = payload(run(cli_repo, "identity", "--json"))
    assert report["claims_results"] is False
    assert report["status"]


def test_json_output_is_canonical_so_two_runs_are_byte_identical(cli_repo: Path):
    first = run(cli_repo, "identity", "--json").stdout
    second = run(cli_repo, "identity", "--json").stdout
    assert first == second, "two runs of a deterministic command disagreed"


def test_version_exits_zero(cli_repo: Path):
    result = run(cli_repo, "--version")
    assert result.returncode == EXIT_OK
    assert result.stdout.strip().startswith("engramfold ")


def test_an_unknown_command_is_a_usage_error(cli_repo: Path):
    result = run(cli_repo, "not-a-command")
    assert result.returncode == EXIT_USAGE, result.stdout + result.stderr


def test_in_process_main_agrees_with_the_subprocess(cli_repo: Path):
    assert main(["--repo", str(cli_repo), "identity", "--json"]) == EXIT_OK


# ── validate, and the verdicts it carries ──────────────────────────────────


def test_validate_passes_on_a_valid_repository(cli_repo: Path):
    result = run(cli_repo, "validate")
    assert result.returncode == EXIT_OK, result.stdout + result.stderr
    assert "overall: PASS" in result.stdout
    report = payload(run(cli_repo, "validate", "--json"))
    assert report["overall"] == "PASS"
    assert report["missing_gates"] == []
    assert report["totals"]["checks_executed"] > 0
    assert len(report["gates"]) == len(report["expected_gates"])


def test_validate_fails_on_a_repository_with_a_dangling_reference(cli_repo: Path):
    (cli_repo / "registry/datasets.yaml").write_text(
        "schema_version: 1\ndatasets:\n  - id: dangling\n"
        "    manifest_path: datasets/manifests/does-not-exist.json\n",
        encoding="utf-8",
    )
    result = run(cli_repo, "validate")
    assert result.returncode == EXIT_FAIL, result.stdout
    assert "overall: FAIL" in result.stdout
    report = payload(run(cli_repo, "validate", "--json"))
    assert report["overall"] == "FAIL"
    assert any(gate["status"] == "FAIL" for gate in report["gates"])


def test_validate_is_blocked_when_provenance_is_unknowable(cli_repo: Path):
    """Blocked is never success, and it is a distinct exit code so a caller can tell them apart."""
    disable_git_repo(cli_repo)
    result = run(cli_repo, "validate")
    assert result.returncode == EXIT_BLOCKED, result.stdout
    assert "BLOCKED" in result.stdout
    assert main(["--repo", str(cli_repo), "validate"]) == EXIT_BLOCKED


def test_the_console_script_agrees_with_the_module_form(cli_repo: Path):
    module_form = run(cli_repo, "validate", "--json")
    console_form = run_console(cli_repo, "validate", "--json")
    assert console_form.returncode == module_form.returncode
    assert json.loads(console_form.stdout)["overall"] == json.loads(module_form.stdout)["overall"]


def test_registry_validate_reports_the_registry_gate(cli_repo: Path):
    result = run(cli_repo, "registry", "validate")
    assert result.returncode == EXIT_OK, result.stdout + result.stderr
    assert "overall: PASS" in result.stdout
    report = payload(run(cli_repo, "registry", "validate", "--json"))
    assert report["overall"] == "PASS"
    assert report["registry"]["name"] == "registry"
    assert report["registry"]["checks_executed"] > 0


def test_registry_validate_fails_when_the_registry_is_broken(cli_repo: Path):
    (cli_repo / "registry/claims.yaml").write_text(
        "schema_version: 1\nclaims:\n  - id: c1\n    statement: x\n    status: SUPPORTED\n"
        "    evidence: []\n",
        encoding="utf-8",
    )
    result = run(cli_repo, "registry", "validate")
    assert result.returncode == EXIT_FAIL, result.stdout
    assert "overall: FAIL" in result.stdout


# ── hash ──────────────────────────────────────────────────────────────────


def test_hash_of_a_file_matches_the_canonical_implementation(cli_repo: Path):
    result = run(cli_repo, "hash", "pyproject.toml", "--json")
    assert result.returncode == EXIT_OK, result.stderr
    report = payload(result)
    assert report["content_hash"] == content_hash(cli_repo / "pyproject.toml")
    assert report["size_bytes"] == (cli_repo / "pyproject.toml").stat().st_size
    assert result.stdout == run(cli_repo, "hash", "pyproject.toml", "--json").stdout


def test_hash_tree_reports_how_many_entries_it_covered(cli_repo: Path):
    result = run(cli_repo, "hash", "src", "--tree", "--json")
    assert result.returncode == EXIT_OK, result.stderr
    report = payload(result)
    assert report["entry_count"] > 0, "the tree digest covered no files"
    assert len(report["entries"]) == report["entry_count"]
    assert report["directory_hash_version"] == 1


def test_hash_tree_on_a_file_is_a_failure_not_an_empty_success(cli_repo: Path):
    """A tree hash of something that is not a tree must fail, and say what it needed."""
    result = run(cli_repo, "hash", "pyproject.toml", "--tree")
    assert result.returncode == EXIT_FAIL
    assert "requires a directory" in result.stderr


def test_hash_of_a_missing_path_fails(cli_repo: Path):
    result = run(cli_repo, "hash", "does-not-exist.txt")
    assert result.returncode == EXIT_FAIL
    assert "does not exist" in result.stderr


# ── provenance ───────────────────────────────────────────────────────────


def test_provenance_git_only_reports_a_clean_known_checkout(cli_repo: Path):
    result = run(cli_repo, "provenance", "--git-only", "--json")
    assert result.returncode == EXIT_OK, result.stderr
    record = payload(result)
    assert record["git_available"] is True
    assert record["git_dirty"] is False, "a freshly committed clone must read as clean"
    assert record["git_commit"]
    assert "environment" not in record, "--git-only captured the environment anyway"


def test_provenance_write_records_the_document_at_the_requested_path(cli_repo: Path):
    destination = "provenance/records/example.json"
    result = run(cli_repo, "provenance", "--git-only", "--json", "--write", destination)
    assert result.returncode == EXIT_OK, result.stderr
    written = cli_repo / destination
    assert written.is_file(), "the command reported success without writing the record"
    record = json.loads(written.read_text(encoding="utf-8"))
    assert record["provenance_record_version"] == 1
    assert record["git_commit"] == payload(result)["git_commit"]


def test_provenance_is_blocked_when_git_cannot_answer(cli_repo: Path):
    """An unknowable checkout is not a success: nothing about it can be attributed."""
    disable_git_repo(cli_repo)
    result = run(cli_repo, "provenance", "--git-only", "--json")
    assert result.returncode == EXIT_BLOCKED, result.stdout + result.stderr
    record = payload(result)
    assert record["git_available"] is False
    assert record["git_dirty"] is None


# ─ freeze dataset: dry run versus --write ───────────────────────────────


def test_freezing_a_dataset_without_write_leaves_no_file(manifest_repo: Path):
    result = run(manifest_repo, "freeze", "dataset", DATASET_MANIFEST, "--created-at", CREATED_AT)
    assert result.returncode == EXIT_OK, result.stderr
    assert "dry run" in result.stdout
    assert sorted((manifest_repo / "datasets/freezes").glob("*.json")) == [], (
        "a dry run wrote a freeze, so a caller cannot inspect before recording"
    )


def test_freezing_a_dataset_with_write_is_idempotent(manifest_repo: Path):
    args = ("freeze", "dataset", DATASET_MANIFEST, "--created-at", CREATED_AT, "--write", "--json")
    first = payload(run(manifest_repo, *args))
    assert first["written"] is True, first
    target = manifest_repo / first["output_path"]
    assert target.is_file()
    original = target.read_bytes()

    second = payload(run(manifest_repo, *args))
    assert second["written"] is False
    assert second["unchanged"] is True
    assert second["byte_identical"] is True, "regenerating a freeze rewrote its bytes"
    assert target.read_bytes() == original
    assert second["freeze"]["freeze_id"] == first["freeze"]["freeze_id"]


def test_freezing_a_missing_manifest_fails(cli_repo: Path):
    result = run(cli_repo, "freeze", "dataset", "datasets/manifests/absent.json")
    assert result.returncode == EXIT_FAIL
    assert "does not exist" in result.stderr


# ── freeze experiment ─────────────────────────────────────────────────────


def test_freezing_an_experiment_definition_writes_a_definition_freeze(manifest_repo: Path):
    args = (
        "freeze",
        "experiment",
        EXPERIMENT_MANIFEST,
        "--frozen-at",
        CREATED_AT,
        "--write",
        "--json",
    )
    first = payload(run(manifest_repo, *args))
    assert first["written"] is True, first
    target = manifest_repo / first["output_path"]
    assert target.is_file(), "the definition freeze was reported as written but is not there"
    original = target.read_bytes()
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["freeze_format_version"] == 1
    assert document["experiment_id"] == "example"
    assert document["definition_hash"] == first["freeze"]["definition_hash"]
    assert "captured_at" not in (document["code_revision_at_freeze"] or {}), (
        "the embedded provenance record carries a capture timestamp, so the document cannot "
        "be regenerated byte-for-byte even with --frozen-at fixed"
    )

    second = payload(run(manifest_repo, *args))
    assert second["unchanged"] is True
    assert target.read_bytes() == original, "regenerating a definition freeze rewrote its bytes"
    assert second["freeze"]["freeze_id"] == first["freeze"]["freeze_id"]

    # The regenerated *document* differs in exactly one place: the embedded provenance record
    # now lists the freeze file itself as untracked, because writing it created that file. The
    # identity is unchanged, the bytes on disk are unchanged, and the difference is confined to
    # a recorded observation about the checkout rather than a fact about the definition.
    regenerated = second["freeze"]
    differing = [key for key in document if document[key] != regenerated.get(key)]
    assert differing == ["code_revision_at_freeze"], differing
    nested = document["code_revision_at_freeze"]
    assert [
        key for key in nested if nested[key] != regenerated["code_revision_at_freeze"][key]
    ] == ["untracked_file_list"]
    assert second["byte_identical"] is False, (
        "byte_identical would be true only if the embedded provenance record were unchanged, "
        "and writing the freeze changed the checkout it describes"
    )


# ── verify-freeze, and the vacuous pass it refuses ───────────────────────


def test_verify_freeze_with_no_freezes_fails(cli_repo: Path):
    """The repository contains no freezes: verifying zero targets fails rather than passing."""
    result = run(cli_repo, "verify-freeze")
    assert result.returncode == EXIT_FAIL, result.stdout
    assert "checked nothing" in result.stderr
    report = payload(run(cli_repo, "verify-freeze", "--json"))
    assert report["discovered"] == 0
    assert report["selected"] == 0


def test_verify_freeze_asked_for_a_specific_freeze_reports_that_it_is_absent(cli_repo: Path):
    result = run(cli_repo, "verify-freeze", "a" * 64)
    assert result.returncode == EXIT_FAIL
    assert "no dataset freeze with freeze_id" in result.stdout + result.stderr


def test_verify_freeze_passes_over_a_recorded_freeze_and_fails_after_a_byte_changes(
    manifest_repo: Path,
):
    written = payload(
        run(
            manifest_repo,
            "freeze",
            "dataset",
            DATASET_MANIFEST,
            "--created-at",
            CREATED_AT,
            "--write",
            "--json",
        )
    )
    report = payload(run(manifest_repo, "verify-freeze", "--json"))
    assert run(manifest_repo, "verify-freeze").returncode == EXIT_OK
    assert report["discovered"] == 1
    assert report["selected"] == 1
    assert report["failed"] == 0
    assert report["verifications"][0]["artifacts_checked"] == 1
    assert written["freeze"]["freeze_id"] == report["verifications"][0]["freeze_id"]

    (manifest_repo / "datasets/records.jsonl").write_bytes(b'{"record": 999}\n')
    changed = payload(run(manifest_repo, "verify-freeze", "--json"))
    assert run(manifest_repo, "verify-freeze").returncode == EXIT_FAIL
    assert changed["failed"] == 1
    assert any("changed" in error for error in changed["verifications"][0]["errors"])


# ── hashing selfcheck ─────────────────────────────────────────────────────


def test_hashing_selfcheck_executes_its_checks_and_passes(cli_repo: Path):
    report = payload(run(cli_repo, "hashing", "selfcheck", "--json"))
    assert report["checks_executed"] > 0
    assert report["failed"] == 0
    assert report["source_files_covered"] > 0
    assert run(cli_repo, "hashing", "selfcheck").returncode == EXIT_OK
