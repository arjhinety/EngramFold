"""The registry: reference resolution, duplicates, and the population policy coupling.

The two mechanisms tested hardest here are the ones that stop a validation suite from
shrinking to nothing while still reporting success: references that must resolve, and a
population policy that cannot be relaxed without changing what the repository says it is.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import (
    OPTIONAL_POLICIES,
    REQUIRED_POLICIES,
    populations_yaml,
    registry_repo,
    registry_yaml,
    write_text,
    write_yaml,
)

from engramfold import STATUS
from engramfold.registry.store import (
    CANONICAL_DIRECTORIES,
    CLAIM_STATUSES,
    DECLARED_POPULATIONS,
    EVIDENCE_KINDS,
    GATE_POPULATION,
    REGISTRY_FILES,
    claim_errors,
    declared_phase,
    load_populations,
    load_registry,
    phase_errors,
    population_policy_errors,
    record_errors,
    reference_errors,
    registry_file_errors,
)
from engramfold.registry.validate import check_registry
from engramfold.validation import FAIL, PASS

# ── Declarations ─────────────────────────────────────────────────────────────


def test_every_population_has_a_registry_file_and_a_declared_policy():
    """Pins the two vocabularies together, so a new population cannot be half-added."""
    for population in REGISTRY_FILES:
        assert population in DECLARED_POPULATIONS
    for population in DECLARED_POPULATIONS:
        assert population in OPTIONAL_POLICIES, population


def test_each_gate_maps_to_a_declared_population():
    """A gate must inherit the policy of a population that exists.

    The freeze gate is governed by ``dataset_freezes``, not by a population called ``freezes``.
    Spelling the mapping out is what stops a gate inheriting the wrong policy by accident.
    """
    for gate, population in GATE_POPULATION.items():
        assert population in DECLARED_POPULATIONS, (gate, population)


def test_gate_names_and_population_names_are_different_vocabularies():
    """Documented on purpose: conflating them produced a false non-vacuity failure once.

    ``freezes`` and ``provenance`` are gate names with no population of their own; the
    populations they are governed by are named differently.
    """
    assert "freezes" not in DECLARED_POPULATIONS
    assert "provenance" not in DECLARED_POPULATIONS
    assert GATE_POPULATION["freezes"] == "dataset_freezes"
    assert GATE_POPULATION["provenance"] == "provenance_records"
    assert set(GATE_POPULATION) - set(DECLARED_POPULATIONS) == {"freezes", "provenance"}


def test_canonical_directories_are_all_relative_and_non_empty():
    for relative in CANONICAL_DIRECTORIES:
        assert relative and not relative.startswith("/") and "\\" not in relative


# ── File loading ─────────────────────────────────────────────────────────────


def test_a_complete_registry_loads(registry_root: Path):
    registry = load_registry(registry_root)
    assert registry.populations is not None
    assert registry.populations.phase == STATUS
    assert registry.document_errors() == []
    assert registry.total_records() == 0


def test_a_missing_registry_file_is_an_error(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    (root / "registry/models.yaml").unlink()
    registry = load_registry(root)
    errors = registry_file_errors(registry)
    assert any("models" in error and "missing" in error for error in errors)


def test_a_registry_file_with_the_wrong_record_key_is_an_error(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_yaml(root / "registry/reports.yaml", "schema_version: 1\nreport: []\n")
    registry = load_registry(root)
    assert any("reports" in error for error in registry_file_errors(registry))


def test_a_registry_file_whose_records_are_not_mappings_is_an_error(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_yaml(root / "registry/reports.yaml", "schema_version: 1\nreports:\n  - just-a-string\n")
    registry = load_registry(root)
    assert any("not a mapping" in error for error in registry_file_errors(registry))


def test_a_malformed_registry_file_is_an_error_not_an_empty_population(tmp_path: Path):
    """The distinction that matters: broken is not the same as empty."""
    root = registry_repo(tmp_path / "r")
    write_yaml(root / "registry/datasets.yaml", "schema_version: 1\ndatasets: [oops\n")
    registry = load_registry(root)
    errors = registry_file_errors(registry)
    assert errors
    assert not any("missing" in error for error in errors), "broken must not read as absent"


def test_an_unknown_registry_schema_version_is_an_error(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_yaml(root / "registry/datasets.yaml", "schema_version: 99\ndatasets: []\n")
    registry = load_registry(root)
    assert registry_file_errors(registry)


def test_a_null_registry_file_is_malformed_not_empty(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_yaml(root / "registry/models.yaml", "# only a comment\n")
    registry = load_registry(root)
    assert registry_file_errors(registry)


# ── Record shape ─────────────────────────────────────────────────────────────


def test_a_record_without_an_id_is_rejected(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_yaml(
        root / "registry/reports.yaml",
        registry_yaml("reports", ["  - title: no id here\n"]),
    )
    registry = load_registry(root)
    assert any("id must match" in error for error in record_errors(registry))


def test_a_duplicate_id_within_a_file_is_rejected(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        reports=["  - id: dup\n", "  - id: dup\n"],
    )
    registry = load_registry(root)
    assert any("duplicate id" in error for error in record_errors(registry))


def test_a_missing_required_record_field_is_rejected(tmp_path: Path):
    root = registry_repo(tmp_path / "r", reports=["  - id: r1\n"])
    assert record_errors(load_registry(root)) == []

    root2 = registry_repo(tmp_path / "r2", datasets=["  - id: d1\n"])
    assert any("manifest_path" in error for error in record_errors(load_registry(root2)))


# ── References ──────────────────────────────────────────────────────────────


def test_a_resolving_reference_is_accepted(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        experiments=[
            "  - id: e1\n    manifest_path: experiments/manifests/e1.json\n    dataset_ids: [d1]\n",
        ],
        datasets=["  - id: d1\n    manifest_path: datasets/manifests/d1.json\n"],
    )
    write_text(root / "datasets/manifests/d1.json", "{}")
    write_text(root / "experiments/manifests/e1.json", "{}")
    assert reference_errors(load_registry(root), root) == []


def test_a_dangling_reference_is_an_error(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        experiments=[
            "  - id: e1\n    manifest_path: experiments/manifests/e1.json\n"
            "    dataset_ids: [missing]\n",
        ],
    )
    write_text(root / "experiments/manifests/e1.json", "{}")
    errors = reference_errors(load_registry(root), root)
    assert any("missing" in error and "does not exist" in error for error in errors)


def test_a_manifest_path_that_does_not_resolve_is_an_error(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        datasets=["  - id: d1\n    manifest_path: datasets/manifests/ghost.json\n"],
    )
    errors = reference_errors(load_registry(root), root)
    assert any("does not resolve" in error for error in errors)


def test_a_freeze_reference_to_a_missing_freeze_is_an_error(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        datasets=[
            "  - id: d1\n    manifest_path: datasets/manifests/d1.json\n"
            f"    freeze_ids: [{'a' * 64}]\n",
        ],
    )
    write_text(root / "datasets/manifests/d1.json", "{}")
    errors = reference_errors(load_registry(root), root)
    assert any("datasets/freezes" in error for error in errors)


def test_a_freeze_reference_that_is_not_digest_shaped_is_an_error(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        datasets=[
            "  - id: d1\n    manifest_path: datasets/manifests/d1.json\n"
            "    freeze_ids: [not-a-digest]\n",
        ],
    )
    write_text(root / "datasets/manifests/d1.json", "{}")
    errors = reference_errors(load_registry(root), root)
    assert any("not a sha256 freeze id" in error for error in errors)


def test_a_resolving_freeze_reference_is_accepted(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        datasets=[
            "  - id: d1\n    manifest_path: datasets/manifests/d1.json\n"
            f"    freeze_ids: [{'a' * 64}]\n",
        ],
    )
    write_text(root / "datasets/manifests/d1.json", "{}")
    write_text(root / f"datasets/freezes/{'a' * 64}.json", "{}")
    errors = reference_errors(load_registry(root), root)
    assert not any("freeze" in error for error in errors)


# ── Population policy ────────────────────────────────────────────────────────


def test_the_declared_phase_must_match_the_advertised_status(tmp_path: Path):
    root = registry_repo(tmp_path / "r", phase=STATUS, status_line="SOMETHING ELSE")
    assert phase_errors(root, load_registry(root).populations)


def test_a_readme_without_a_status_line_is_an_error(tmp_path: Path):
    root = registry_repo(tmp_path / "r")
    write_text(root / "README.md", "# Fixture\n\nNo status here.\n")
    assert any("STATUS" in error for error in phase_errors(root, load_registry(root).populations))


def test_the_status_line_is_found_through_markdown_decoration(tmp_path: Path):
    for decoration in (
        f"STATUS: {STATUS}",
        f"**STATUS: {STATUS}**",
        f"## STATUS: {STATUS}",
        f"> STATUS: {STATUS}",
    ):
        root = tmp_path / f"r{hash(decoration) & 0xFFFF}"
        root.mkdir()
        write_text(root / "README.md", f"# X\n\n{decoration}\n")
        assert declared_phase(root) == STATUS, decoration


def test_optional_with_records_is_a_contradiction(tmp_path: Path):
    """OPTIONAL must never authorise a run over a populated registry."""
    root = registry_repo(
        tmp_path / "r",
        datasets=["  - id: d1\n    manifest_path: datasets/manifests/d1.json\n"],
    )
    write_text(root / "datasets/manifests/d1.json", "{}")
    errors = population_policy_errors(load_registry(root))
    assert any("OPTIONAL" in error for error in errors)


def test_required_while_empty_is_a_contradiction(tmp_path: Path):
    root = registry_repo(tmp_path / "r", policies=REQUIRED_POLICIES)
    errors = population_policy_errors(load_registry(root))
    assert any("no records" in error for error in errors)


def test_an_optional_population_must_state_a_reason(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    write_text(root / "README.md", f"**STATUS: {STATUS}**\n")
    (root / "registry").mkdir()
    write_yaml(
        root / "registry/populations.yaml",
        "schema_version: 1\n"
        f'phase: "{STATUS}"\n'
        "populations:\n"
        + "".join(f"  {name}:\n    policy: OPTIONAL\n" for name in OPTIONAL_POLICIES),
    )
    _populations, errors = load_populations(root)
    assert any("zero_allowed_reason" in error for error in errors)


def test_a_required_population_may_not_carry_a_zero_reason(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    write_text(root / "README.md", f"**STATUS: {STATUS}**\n")
    (root / "registry").mkdir()
    body = "populations:\n" + "".join(
        f"  {name}:\n    policy: REQUIRED_NONEMPTY\n    zero_allowed_reason: stale\n"
        for name in OPTIONAL_POLICIES
    )
    write_yaml(root / "registry/populations.yaml", f'schema_version: 1\nphase: "{STATUS}"\n{body}')
    _populations, errors = load_populations(root)
    assert any("cannot be authorised to be empty" in error for error in errors)


def test_an_undeclared_population_is_an_error(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    write_text(root / "README.md", f"**STATUS: {STATUS}**\n")
    (root / "registry").mkdir()
    write_yaml(
        root / "registry/populations.yaml",
        f'schema_version: 1\nphase: "{STATUS}"\npopulations:\n  datasets:\n    policy: OPTIONAL\n'
        "    zero_allowed_reason: because\n",
    )
    _populations, errors = load_populations(root)
    assert any("is not declared" in error for error in errors)


def test_an_unknown_population_is_an_error(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    write_text(root / "README.md", f"**STATUS: {STATUS}**\n")
    (root / "registry").mkdir()
    write_yaml(
        root / "registry/populations.yaml",
        populations_yaml(phase=STATUS).replace(
            "populations:\n",
            "populations:\n  invented:\n    policy: OPTIONAL\n    zero_allowed_reason: x\n",
        ),
    )
    _populations, errors = load_populations(root)
    assert any("not a population this build knows about" in error for error in errors)


def test_an_unknown_policy_value_is_an_error(tmp_path: Path):
    root = registry_repo(tmp_path / "r", policies={**OPTIONAL_POLICIES, "datasets": "MAYBE"})
    _populations, errors = load_populations(root)
    assert any("must be one of" in error for error in errors)


def test_an_unknown_populations_key_is_an_error(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    write_text(root / "README.md", f"**STATUS: {STATUS}**\n")
    (root / "registry").mkdir()
    write_yaml(
        root / "registry/populations.yaml",
        populations_yaml(phase=STATUS).replace(
            "    policy: OPTIONAL", "    policy: OPTIONAL\n    extra: nope", 1
        ),
    )
    _populations, errors = load_populations(root)
    assert any("unknown key" in error for error in errors)


def test_optional_is_legal_only_while_the_phase_is_pre_experiment(tmp_path: Path):
    root = registry_repo(tmp_path / "r", phase="RESEARCH IN PROGRESS")
    _populations, errors = load_populations(root)
    assert any("OPTIONAL is only permitted" in error for error in errors)


def test_required_nonempty_is_legal_after_the_phase_advances(tmp_path: Path):
    root = registry_repo(tmp_path / "r", phase="RESEARCH IN PROGRESS", policies=REQUIRED_POLICIES)
    _populations, errors = load_populations(root)
    assert not any("OPTIONAL is only permitted" in error for error in errors)


# ── Claims ───────────────────────────────────────────────────────────────────


def test_the_claim_vocabulary_is_closed():
    assert set(CLAIM_STATUSES) == {"UNSUPPORTED", "SUPPORTED", "WITHDRAWN", "CORRECTED"}
    assert "prose" not in EVIDENCE_KINDS


def test_a_supported_claim_needs_resolvable_evidence(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        claims=[
            "  - id: c1\n    statement: x\n    status: SUPPORTED\n    evidence: []\n",
        ],
    )
    errors = claim_errors(load_registry(root), root)
    assert any("no evidence" in error for error in errors)


def test_a_supported_claim_citing_an_existing_artifact_resolves(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        artifacts=["  - id: a1\n    manifest_path: artifacts/manifests/a1.json\n"],
        claims=[
            "  - id: c1\n    statement: x\n    status: SUPPORTED\n"
            "    evidence:\n      - kind: artifact\n        ref: a1\n",
        ],
    )
    write_text(root / "artifacts/manifests/a1.json", "{}")
    errors = claim_errors(load_registry(root), root)
    assert not any("does not exist" in error for error in errors)


def test_a_claim_citing_a_missing_artifact_is_an_error(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        claims=[
            "  - id: c1\n    statement: x\n    status: SUPPORTED\n"
            "    evidence:\n      - kind: artifact\n        ref: ghost\n",
        ],
    )
    errors = claim_errors(load_registry(root), root)
    assert any("ghost" in error for error in errors)


def test_an_unknown_evidence_kind_is_an_error(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        claims=[
            "  - id: c1\n    statement: x\n    status: SUPPORTED\n"
            "    evidence:\n      - kind: vibes\n        ref: x\n",
        ],
    )
    errors = claim_errors(load_registry(root), root)
    assert any("evidence kind" in error for error in errors)


def test_a_withdrawn_claim_is_legitimate_and_kept(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        claims=[
            "  - id: c1\n    statement: a retracted assertion\n"
            "    status: WITHDRAWN\n    evidence: []\n",
        ],
    )
    assert claim_errors(load_registry(root), root) == []


def test_an_unknown_claim_status_is_an_error(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        claims=["  - id: c1\n    statement: x\n    status: PROBABLY\n    evidence: []\n"],
    )
    errors = claim_errors(load_registry(root), root)
    assert any("must be one of" in error for error in errors)


# ── The gate ────────────────────────────────────────────────────────────────


def test_the_registry_gate_passes_on_a_clean_fixture(registry_root: Path):
    result = check_registry(registry_root)
    assert result.status == PASS, result.render()
    assert result.checks_executed > 0
    assert result.accounting_errors() == []


def test_the_registry_gate_fails_on_a_fixture_with_a_contradiction(tmp_path: Path):
    root = registry_repo(
        tmp_path / "r",
        datasets=["  - id: d1\n    manifest_path: datasets/manifests/d1.json\n"],
    )
    write_text(root / "datasets/manifests/d1.json", "{}")
    result = check_registry(root)
    assert result.status == FAIL
    assert result.failed > 0


def test_the_registry_gate_counts_reference_edges(tmp_path: Path):
    # Populations that hold records must not be declared OPTIONAL -- that is the rule the gate
    # enforces, so the fixture declares them required instead of contradicting itself.
    policies = {
        **OPTIONAL_POLICIES,
        "datasets": "REQUIRED_NONEMPTY",
        "experiments": "REQUIRED_NONEMPTY",
        "artifacts": "REQUIRED_NONEMPTY",
    }
    root = registry_repo(
        tmp_path / "r",
        policies=policies,
        experiments=[
            "  - id: e1\n    manifest_path: experiments/manifests/e1.json\n"
            "    dataset_ids: [d1]\n    artifact_ids: [a1]\n",
        ],
        datasets=["  - id: d1\n    manifest_path: datasets/manifests/d1.json\n"],
        artifacts=["  - id: a1\n    manifest_path: artifacts/manifests/a1.json\n"],
    )
    for relative in (
        "datasets/manifests/d1.json",
        "experiments/manifests/e1.json",
        "artifacts/manifests/a1.json",
    ):
        write_text(root / relative, "{}")
    result = check_registry(root)
    assert result.detail["reference_edges_examined"] == 2
    assert result.status == PASS, result.render()


def test_a_populated_population_may_not_be_declared_optional(tmp_path: Path):
    """The rule the previous test's fixture had to respect, asserted directly."""
    root = registry_repo(
        tmp_path / "r",
        datasets=["  - id: d1\n    manifest_path: datasets/manifests/d1.json\n"],
    )
    write_text(root / "datasets/manifests/d1.json", "{}")
    result = check_registry(root)
    assert result.status == FAIL
    assert any("OPTIONAL" in error for error in result.all_errors())
