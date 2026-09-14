"""Regression tests for vacuous success, against the real gates.

This is the module the repository's whole design exists to make possible. Each test is the
direct regression test for a failure that has actually occurred in a research repository of
this shape:

1. a freeze check read the artifact path from a field the canonical records did not use, so
   every corpus was skipped and the gate reported PASS;
2. ``python -m pkg.module`` imported the module, ran nothing, and exited 0 -- indistinguishable
   from a passing validation;
3. counters that did not add up were read as a pass.

The cases the specification names as first-class are all here: zero manifests, zero freezes,
a broken manifest lookup, a wrong field name, a missing referenced file, an empty registry, a
malformed document, an unknown schema version, and everything being skipped.

Wherever possible these assert on **counts**, because ``assert status == PASS`` cannot
distinguish "checked everything and found nothing wrong" from "checked nothing".
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import (
    REQUIRED_POLICIES,
    registry_repo,
    write_text,
    write_yaml,
)

from engramfold import STATUS
from engramfold.registry.store import load_registry, population_policy_errors
from engramfold.registry.validate import (
    check_artifacts,
    check_datasets,
    check_experiments,
    check_freezes,
    check_registry,
    check_substrate,
    validate,
)
from engramfold.validation import (
    FAIL,
    NONVACUOUS_PREFIX,
    OPTIONAL,
    PASS,
)


def _messages(result) -> str:
    return "\n".join(result.all_errors())


# ── Zero targets, and the authorisation to have none ─────────────────────────


def test_zero_dataset_manifests_is_authorised_but_still_executes_checks(registry_root: Path):
    """An authorised-empty population must still prove the gate ran."""
    result = check_datasets(registry_root)
    assert result.discovered == 0
    assert result.checks_executed > 0, "an authorised-empty gate must still execute assertions"
    assert result.status == PASS
    assert result.policy == OPTIONAL


def test_zero_dataset_manifests_without_authorisation_fails(tmp_path: Path):
    """The same repository, with the policy declaring the population required."""
    root = registry_repo(tmp_path / "r", policies=REQUIRED_POLICIES)
    result = check_datasets(root)
    assert result.discovered == 0
    assert result.status == FAIL
    assert NONVACUOUS_PREFIX in _messages(result)


def test_zero_freezes_is_authorised_but_still_executes_checks(registry_root: Path):
    result = check_freezes(registry_root)
    assert result.discovered == 0
    assert result.checks_executed > 0
    assert result.status == PASS


def test_zero_experiments_is_authorised_but_still_executes_checks(registry_root: Path):
    result = check_experiments(registry_root)
    assert result.discovered == 0
    assert result.checks_executed > 0
    assert result.status == PASS


def test_zero_artifacts_is_authorised_but_still_executes_checks(registry_root: Path):
    result = check_artifacts(registry_root)
    assert result.discovered == 0
    assert result.checks_executed > 0
    assert result.status == PASS


def test_the_substrate_gate_always_executes_every_named_invariant(registry_root: Path):
    """Every named invariant is a target, and every one must have run."""
    from engramfold.registry.validate import SUBSTRATE_CHECKS

    result = check_substrate(registry_root)
    assert result.checks_executed == len(SUBSTRATE_CHECKS)
    assert result.discovered >= len(SUBSTRATE_CHECKS)
    assert result.status == FAIL or result.status == PASS


# ── A removed manifest must not read as an absent one ────────────────────────


def test_a_registry_record_indexing_a_missing_manifest_fails(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        datasets=[
            "  - id: ghost\n    manifest_path: datasets/manifests/ghost.json\n",
        ],
    )
    result = check_datasets(root)
    assert result.status == FAIL
    assert "ghost" in _messages(result)


def test_a_registry_record_indexing_a_present_manifest_is_discovered(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        datasets=[
            "  - id: real\n    manifest_path: datasets/manifests/real.json\n",
        ],
    )
    write_text(
        root / "datasets/manifests/real.json",
        """{
  "dataset_manifest_version": 1,
  "schema_version": 1,
  "dataset_id": "real",
  "dataset_version": "v1",
  "source_type": "local",
  "split": "train",
  "license": "CC0-1.0",
  "record_count": 3,
  "adapter_version": "fixture",
  "deduplication_policy": "none",
  "contamination_policy": "unassessed",
  "source_files": ["datasets/data.jsonl"],
  "source_file_hashes": {"datasets/data.jsonl": "%s"}
}
"""
        % ("0" * 64),
    )
    result = check_datasets(root)
    assert result.discovered == 1
    assert result.checks_executed > 0
    assert "ghost" not in _messages(result)


def test_an_unindexed_manifest_on_disk_is_an_error(tmp_path: Path):
    """A canonical document nothing references is unreachable from the research record."""
    root = registry_repo(tmp_path / "r")
    write_text(
        root / "datasets/manifests/orphan.json",
        '{"dataset_manifest_version": 1, "schema_version": 1, "dataset_id": "orphan",'
        ' "dataset_version": "v1", "source_type": "local", "split": "train",'
        ' "license": "CC0-1.0", "record_count": 1, "adapter_version": "f",'
        ' "deduplication_policy": "none", "contamination_policy": "unassessed",'
        ' "source_files": ["datasets/x.jsonl"],'
        ' "source_file_hashes": {"datasets/x.jsonl": "' + "0" * 64 + '"}}',
    )
    result = check_datasets(root)
    assert result.status == FAIL
    assert "orphan" in _messages(result)


# ── Wrong field name ─────────────────────────────────────────────────────────


def test_a_renamed_manifest_field_fails_rather_than_being_ignored(tmp_path: Path):
    """Renaming record_count to records must fail, not silently drop the count.

    If unknown fields were ignored, every downstream check that reads record_count would
    quietly stop applying, and the manifest would still validate.
    """
    from engramfold.datasets.manifest import validate_dataset_manifest

    manifest = {
        "dataset_manifest_version": 1,
        "schema_version": 1,
        "dataset_id": "x",
        "dataset_version": "v1",
        "source_type": "local",
        "split": "train",
        "license": "CC0-1.0",
        "records": 3,  # renamed: record_count
        "adapter_version": "f",
        "deduplication_policy": "none",
        "contamination_policy": "unassessed",
    }
    errors = validate_dataset_manifest(manifest, origin="fixture")
    assert any("unknown field" in error for error in errors), errors
    assert any("record_count" in error for error in errors), errors


def test_a_renamed_experiment_field_fails(tmp_path: Path):
    from engramfold.experiments.manifest import validate_experiment_manifest

    errors = validate_experiment_manifest(
        {
            "experiment_manifest_version": 1,
            "experiment_id": "x",
            "description": "d",
            "status": "DRAFT",
            "dataset_freezes": [],
            "config": {},  # renamed: configuration
            "configuration_hash": "0" * 64,
        },
        origin="fixture",
    )
    assert any("unknown field" in error for error in errors), errors


# ── Malformed documents, unknown versions ────────────────────────────────────


def test_a_malformed_manifest_fails_as_malformed_not_as_absent(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_text(root / "datasets/manifests/broken.json", "{ this is not json")
    result = check_datasets(root)
    assert result.status == FAIL
    assert "broken.json" in _messages(result)
    assert "does not parse" in _messages(result)


def test_an_unknown_schema_version_fails_as_such(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_text(
        root / "datasets/manifests/future.json",
        '{"dataset_manifest_version": 99, "dataset_id": "future"}',
    )
    result = check_datasets(root)
    assert result.status == FAIL
    assert "future" in _messages(result)


def test_a_malformed_registry_file_fails_rather_than_counting_as_empty(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_yaml(root / "registry/datasets.yaml", "schema_version: 1\ndatasets: [ oops\n")
    result = check_registry(root)
    assert result.status == FAIL
    assert "datasets" in _messages(result)


def test_a_registry_with_the_wrong_record_key_fails(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_yaml(root / "registry/datasets.yaml", "schema_version: 1\ndataset: []\n")
    result = check_registry(root)
    assert result.status == FAIL
    assert "datasets" in _messages(result)


# ── Empty registry, and the policy that would excuse it ──────────────────────


def test_a_fully_optional_empty_registry_is_authorised_and_still_executes(registry_root: Path):
    result = check_registry(registry_root)
    assert result.checks_executed > 0
    assert result.discovered > 0, "the registry files and populations are themselves targets"


def test_declaring_a_population_optional_while_it_holds_records_fails(tmp_path: Path):
    """An OPTIONAL policy must never be usable to authorise a run over real targets."""
    root = registry_repo(
        tmp_path / "r",
        datasets=["  - id: present\n    manifest_path: datasets/manifests/present.json\n"],
    )
    write_text(
        root / "datasets/manifests/present.json",
        '{"dataset_manifest_version": 1}',
    )
    registry = load_registry(root)
    errors = population_policy_errors(registry)
    assert any("OPTIONAL" in error and "populated" in error for error in errors), errors


def test_declaring_a_population_required_while_it_is_empty_fails(tmp_path: Path):
    root = registry_repo(tmp_path / "r", policies=REQUIRED_POLICIES)
    registry = load_registry(root)
    errors = population_policy_errors(registry)
    assert any("no records" in error for error in errors), errors


def test_a_missing_populations_file_is_an_error_not_an_empty_policy(tmp_path: Path):
    """Without it there is no declared basis for emptiness, and defaulting permissive is wrong."""
    root = tmp_path / "bare"
    root.mkdir()
    write_text(root / "README.md", f"**STATUS: {STATUS}**\n")
    (root / "registry").mkdir()
    registry = load_registry(root)
    assert registry.populations is None
    assert registry.populations_errors
    assert any("missing" in error for error in registry.populations_errors)


def test_a_phase_that_does_not_match_the_advertised_status_fails(tmp_path: Path):
    root = registry_repo(tmp_path / "r", phase="RESEARCH IN PROGRESS", status_line=STATUS)
    result = check_registry(root)
    assert result.status == FAIL


def test_optional_is_illegal_once_the_phase_is_no_longer_pre_experiment(tmp_path: Path):
    """Relaxing the policy must require changing what the repository publicly says it is."""
    root = registry_repo(tmp_path / "r", phase="RESEARCH IN PROGRESS")
    result = check_registry(root)
    assert result.status == FAIL
    assert "OPTIONAL" in _messages(result)


# ── Dangling references ──────────────────────────────────────────────────────


def test_a_dangling_experiment_reference_fails(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        experiments=[
            "  - id: e1\n    manifest_path: experiments/manifests/e1.json\n"
            "    dataset_ids: [nonexistent]\n",
        ],
    )
    result = check_registry(root)
    assert result.status == FAIL
    assert "nonexistent" in _messages(result)


def test_a_dangling_artifact_to_experiment_reference_fails(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        artifacts=[
            "  - id: a1\n    manifest_path: artifacts/manifests/a1.json\n"
            "    experiment_id: ghost-experiment\n",
        ],
    )
    result = check_registry(root)
    assert result.status == FAIL
    assert "ghost-experiment" in _messages(result)


def test_a_duplicate_record_id_fails(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        datasets=[
            "  - id: dup\n    manifest_path: datasets/manifests/a.json\n",
            "  - id: dup\n    manifest_path: datasets/manifests/b.json\n",
        ],
    )
    result = check_registry(root)
    assert result.status == FAIL
    assert "duplicate" in _messages(result)


def test_a_freeze_reference_to_a_nonexistent_freeze_fails(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        datasets=[
            "  - id: d\n    manifest_path: datasets/manifests/d.json\n"
            f"    freeze_ids: [{'a' * 64}]\n",
        ],
    )
    result = check_registry(root)
    assert result.status == FAIL
    assert "datasets/freezes" in _messages(result)


# ── Claims cannot be asserted without evidence ───────────────────────────────


def test_a_supported_claim_with_no_evidence_fails(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        claims=[
            "  - id: c1\n    statement: consolidation preserves behaviour\n"
            "    status: SUPPORTED\n    evidence: []\n",
        ],
    )
    result = check_registry(root)
    assert result.status == FAIL
    assert "SUPPORTED" in _messages(result)


def test_a_claim_citing_prose_as_evidence_fails(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        claims=[
            "  - id: c1\n    statement: x\n    status: SUPPORTED\n"
            "    evidence:\n      - kind: prose\n        ref: README.md\n",
        ],
    )
    result = check_registry(root)
    assert result.status == FAIL
    assert "prose" in _messages(result)


def test_an_unsupported_claim_is_legitimate(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        claims=[
            "  - id: c1\n    statement: a question we have not answered\n"
            "    status: UNSUPPORTED\n    evidence: []\n",
        ],
    )
    result = check_registry(root)
    assert "SUPPORTED" not in _messages(result)


def test_a_supported_claim_citing_a_metric_file_that_is_absent_fails(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        claims=[
            "  - id: c1\n    statement: x\n    status: SUPPORTED\n"
            "    evidence:\n      - kind: metric_file\n        ref: reports/metrics.json\n",
        ],
    )
    result = check_registry(root)
    assert result.status == FAIL
    assert "metrics.json" in _messages(result)


# ── The real repository ──────────────────────────────────────────────────────


def test_the_real_repository_validates_clean(repo_root: Path):
    assert validate(repo_root) == [], validate(repo_root)


def test_the_real_repository_executes_more_than_one_check_per_gate(repo_root: Path):
    """A PASS must be backed by executed assertions, not by a quiet no-op."""
    from engramfold.registry.validate import audit

    report = audit(repo_root)
    assert report.overall == PASS, report.render()
    for result in report.results:
        assert result.checks_executed > 0, f"{result.name}: {result.render()}"
        assert result.accounting_errors() == [], f"{result.name}: {result.render()}"


def test_no_real_gate_reports_a_census_that_does_not_add_up(repo_root: Path):
    from engramfold.registry.validate import audit

    report = audit(repo_root)
    for result in report.results:
        assert result.accounting_errors() == [], f"{result.name}: {result.render()}"


def test_the_real_repository_has_all_eight_gates_executing(repo_root: Path):
    """A missing gate must fail coverage rather than shorten the report."""
    from engramfold.registry.validate import audit
    from engramfold.validation import GATE_CONTRACT

    report = audit(repo_root)
    assert report.missing_gates() == []
    assert set(report.ran_gates) == set(GATE_CONTRACT)
    assert len(report.results) == len(GATE_CONTRACT)
