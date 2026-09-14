"""Experiment manifests and definition freezes: status obligations and detectable drift.

The question this module answers is the one the specification asks directly: *has an
experiment definition changed since execution?* The answer must be "yes, and here is the field
that moved", never "no" because nothing was compared.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from engramfold.errors import IntegrityError
from engramfold.experiments.freeze import (
    IDENTITY_FIELDS,
    build_experiment_freeze,
    experiment_freeze_identity,
    load_experiment_freeze,
    validate_experiment_freeze_document,
    verify_experiment_freeze,
    write_experiment_freeze,
)
from engramfold.experiments.manifest import (
    ARTIFACT_STATUSES,
    ATTRIBUTION_FIELDS,
    EXECUTED_STATUSES,
    REQUIRED_FIELDS,
    STATUSES,
    configuration_identity,
    experiment_identity,
    experiment_manifest_version,
    load_experiment_manifest,
    validate_experiment_manifest,
)

ORIGIN = "fixture"
REVISION = "a" * 40
DIGEST = "b" * 64
MANIFEST_PATH = "experiments/manifests/example.json"
FROZEN_AT = "2026-01-01T00:00:00+00:00"


def clean_code_revision() -> dict[str, Any]:
    return {
        "provenance_record_version": 1,
        "git_commit": REVISION,
        "git_branch": "main",
        "git_dirty": False,
    }


def draft_manifest(**overrides: Any) -> dict[str, Any]:
    configuration: dict[str, Any] = {"scope": "infrastructure-only", "placeholder": True}
    manifest: dict[str, Any] = {
        "experiment_manifest_version": experiment_manifest_version(),
        "experiment_id": "example",
        "description": "A fixture experiment used to exercise the schema and the drift check.",
        "status": "DRAFT",
        "dataset_freezes": [],
        "configuration": configuration,
        "configuration_hash": configuration_identity(configuration),
    }
    manifest.update(overrides)
    return manifest


def executed_manifest(**overrides: Any) -> dict[str, Any]:
    manifest = draft_manifest(status="RUNNING")
    manifest.update(
        {
            "model_id": "some/model",
            "model_revision": REVISION,
            "seed": 0,
            "code_revision": clean_code_revision(),
            "environment_manifest": {"python": {"version": "3.12.0"}},
            "command": "engramfold-run example",
            "started_at": "2026-01-01T00:00:00+00:00",
        }
    )
    manifest.update(overrides)
    return manifest


def completed_manifest(**overrides: Any) -> dict[str, Any]:
    manifest = executed_manifest(status="COMPLETED")
    manifest.update(
        {
            "completed_at": "2026-01-02T00:00:00+00:00",
            "artifacts": ["artifacts/data/result.json"],
            "artifact_hashes": {"artifacts/data/result.json": DIGEST},
        }
    )
    manifest.update(overrides)
    return manifest


def errors_for(manifest: dict[str, Any]) -> list[str]:
    return validate_experiment_manifest(manifest, origin=ORIGIN)


# ── Shape ────────────────────────────────────────────────────────────────────


def test_a_draft_is_valid_without_attribution_fields():
    assert errors_for(draft_manifest()) == []


def test_every_declared_required_field_is_enforced():
    for field in REQUIRED_FIELDS:
        manifest = draft_manifest()
        manifest.pop(field)
        assert errors_for(manifest), f"removing {field} was accepted"


def test_the_status_vocabulary_is_closed():
    assert errors_for(draft_manifest(status="DONE"))
    for status in STATUSES:
        assert (
            validate_experiment_manifest(draft_manifest(status=status), origin=ORIGIN) is not None
        )


def test_an_unknown_field_is_rejected():
    errors = errors_for(draft_manifest(hyperparameters={}))
    assert any("unknown field" in error for error in errors)


def test_a_renamed_configuration_field_is_rejected():
    manifest = draft_manifest()
    manifest["config"] = manifest.pop("configuration")
    manifest["configuration_hash"] = configuration_identity(manifest["config"])
    assert any("unknown field" in error for error in errors_for(manifest))


# ── Configuration drift ──────────────────────────────────────────────────────


def test_a_configuration_hash_that_does_not_match_the_block_fails():
    """This is the mechanism behind "has the definition changed since execution?"."""
    manifest = draft_manifest()
    manifest["configuration"]["placeholder"] = False  # edited after the hash was written
    errors = errors_for(manifest)
    assert any("configuration_hash does not match" in error for error in errors)
    assert any("definition changed" in error for error in errors)


def test_key_reordering_is_not_drift():
    """Reordering a mapping is not a change to the experiment."""
    configuration = {"a": 1, "b": 2}
    reordered = {"b": 2, "a": 1}
    assert configuration_identity(configuration) == configuration_identity(reordered)
    manifest = draft_manifest(
        configuration=reordered, configuration_hash=configuration_identity(configuration)
    )
    assert errors_for(manifest) == []


def test_a_configuration_hash_that_is_not_a_digest_fails():
    assert errors_for(draft_manifest(configuration_hash="not-a-digest"))


def test_a_configuration_that_is_not_a_mapping_fails():
    assert errors_for(draft_manifest(configuration="scope=infrastructure"))


# ── Status obligations ───────────────────────────────────────────────────────


def test_executed_statuses_require_every_attribution_field():
    for field in ATTRIBUTION_FIELDS:
        manifest = executed_manifest()
        manifest.pop(field, None)
        if field == "configuration":
            continue  # removing it makes the manifest structurally invalid instead
        assert errors_for(manifest), f"a RUNNING manifest without {field} was accepted"


def test_a_draft_is_not_required_to_name_a_model():
    assert errors_for(draft_manifest()) == []


def test_a_running_manifest_must_name_a_model_and_revision():
    manifest = executed_manifest()
    manifest.pop("model_id")
    manifest.pop("model_revision")
    errors = errors_for(manifest)
    assert any("model_id" in error for error in errors)
    assert any("model_revision" in error for error in errors)


def test_a_model_revision_must_be_immutable():
    errors = errors_for(executed_manifest(model_revision="main"))
    assert any("40-character commit" in error for error in errors)


def test_a_seed_is_required_once_the_experiment_ran():
    manifest = executed_manifest()
    manifest.pop("seed")
    assert any("seed" in error for error in errors_for(manifest))


def test_a_command_is_required_once_the_experiment_ran():
    manifest = executed_manifest(command="   ")
    assert any("command" in error for error in errors_for(manifest))


def test_completed_requires_a_completion_timestamp():
    manifest = completed_manifest()
    manifest.pop("completed_at")
    assert any("completed_at" in error for error in errors_for(manifest))


def test_completed_requires_artifacts_and_hashes():
    """A COMPLETED experiment with no recorded artifact is a claim without a result."""
    no_artifacts = completed_manifest(artifacts=[], artifact_hashes={})
    errors = errors_for(no_artifacts)
    assert any("no artifacts are recorded" in error for error in errors)
    assert any("artifact_hashes is empty" in error for error in errors)


def test_artifact_statuses_are_the_ones_that_demand_outputs():
    assert ARTIFACT_STATUSES == ("COMPLETED",)
    assert set(EXECUTED_STATUSES) == {"RUNNING", "COMPLETED", "FAILED", "INVALID"}


def test_an_invalid_record_is_permitted_but_must_still_be_attributable():
    """INVALID preserves a non-result as evidence, so it still needs its provenance."""
    manifest = completed_manifest(status="INVALID")
    manifest.pop("environment_manifest")
    assert errors_for(manifest)


# ── Code revision: dirty state must never be hidden ─────────────────────────


def test_code_revision_must_be_a_provenance_record_not_a_bare_commit():
    errors = errors_for(executed_manifest(code_revision=REVISION))
    assert any("provenance record" in error for error in errors)
    assert any("dirty" in error for error in errors)


def test_a_dirty_tree_must_carry_a_written_override():
    dirty = {**clean_code_revision(), "git_dirty": True}
    errors = errors_for(executed_manifest(code_revision=dirty))
    assert any("dirty_override_reason" in error for error in errors)


def test_a_dirty_tree_with_an_override_is_accepted_and_still_records_the_dirt():
    dirty = {
        **clean_code_revision(),
        "git_dirty": True,
        "dirty_file_list": ["src/engramfold/hashing.py"],
        "dirty_override_reason": "exploratory run; the modification is recorded in full",
    }
    manifest = executed_manifest(code_revision=dirty)
    assert errors_for(manifest) == []
    assert manifest["code_revision"]["git_dirty"] is True
    assert manifest["code_revision"]["dirty_file_list"] == ["src/engramfold/hashing.py"]


def test_a_null_dirty_flag_is_representable_but_not_acceptable():
    """``null`` means *unknowable*, which is a different fact from *clean*.

    The value must be representable -- otherwise a real state could not be recorded at all --
    but an experiment that ran cannot rest on an unattributable checkout, so it is an error.
    Rejecting it as a *blank field* would conflate two different things.
    """
    unknown = {**clean_code_revision(), "git_dirty": None}
    errors = errors_for(executed_manifest(code_revision=unknown))
    assert any("unknowable" in error for error in errors)
    assert not any("is empty" in error for error in errors)


def test_a_non_boolean_dirty_flag_is_rejected():
    wrong_type = {**clean_code_revision(), "git_dirty": "no"}
    errors = errors_for(executed_manifest(code_revision=wrong_type))
    assert any("must be a boolean or null" in error for error in errors)


def test_omitting_the_dirty_flag_entirely_is_rejected():
    """A record that omits the dirty state cannot distinguish clean from uncommitted code."""
    omitted = {key: value for key, value in clean_code_revision().items() if key != "git_dirty"}
    errors = errors_for(executed_manifest(code_revision=omitted))
    assert any("does not record git_dirty" in error for error in errors)


def test_an_unknown_commit_marker_is_permitted_explicitly():
    unknown = {**clean_code_revision(), "git_commit": "unknown"}
    assert errors_for(executed_manifest(code_revision=unknown)) == []


def test_a_nonsense_commit_is_rejected():
    bad = {**clean_code_revision(), "git_commit": "not-a-commit"}
    assert errors_for(executed_manifest(code_revision=bad))


# ── Inputs ───────────────────────────────────────────────────────────────────


def test_inputs_must_be_pinned_by_freeze_id():
    """An input named by path pins nothing, because a path can hold different bytes tomorrow."""
    errors = errors_for(draft_manifest(dataset_freezes=["datasets/records.jsonl"]))
    assert any("not a freeze id" in error for error in errors)


def test_a_valid_freeze_id_is_accepted():
    assert errors_for(draft_manifest(dataset_freezes=[DIGEST])) == []


def test_duplicate_freeze_ids_are_rejected():
    assert errors_for(draft_manifest(dataset_freezes=[DIGEST, DIGEST]))


# ── Lineage ─────────────────────────────────────────────────────────────────


def test_a_parent_must_be_a_different_experiment():
    assert errors_for(draft_manifest(parent_experiment="example"))
    assert errors_for(draft_manifest(parent_experiment="Not An Id"))
    assert errors_for(draft_manifest(parent_experiment="other")) == []


# ─ Identity ─────────────────────────────────────────────────────────────────


def test_identity_ignores_timestamps():
    left = executed_manifest(started_at="2026-01-01T00:00:00+00:00")
    right = executed_manifest(started_at="2026-06-06T06:06:06+00:00")
    assert experiment_identity(left) == experiment_identity(right)


def test_identity_changes_with_the_configuration():
    assert experiment_identity(draft_manifest()) != experiment_identity(
        draft_manifest(
            configuration={"scope": "different"},
            configuration_hash=configuration_identity({"scope": "different"}),
        )
    )


# ── Loading ─────────────────────────────────────────────────────────────────


def test_loading_a_missing_manifest_reports_absence(tmp_path: Path):
    manifest, errors = load_experiment_manifest(tmp_path, "experiments/manifests/gone.json")
    assert manifest is None
    assert errors and "missing" in errors[0]


def test_loading_a_malformed_manifest_reports_corruption(tmp_path: Path):
    path = tmp_path / "experiments/manifests/bad.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    manifest, errors = load_experiment_manifest(tmp_path, "experiments/manifests/bad.json")
    assert manifest is None
    assert errors


# ── Definition freezes ───────────────────────────────────────────────────────


@pytest.fixture
def frozen_experiment(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = tmp_path / "repo"
    manifest = draft_manifest(status="FROZEN")
    path = root / MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = build_experiment_freeze(
        manifest,
        MANIFEST_PATH,
        frozen_at=FROZEN_AT,
        repository_revision="0" * 40,
        code_revision_at_freeze=clean_code_revision(),
    )
    assert result.errors == [], result.errors
    return root, manifest, result.freeze


def _rewrite(root: Path, manifest: dict[str, Any]) -> None:
    (root / MANIFEST_PATH).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_an_unchanged_definition_shows_no_drift(frozen_experiment):
    root, _manifest, freeze = frozen_experiment
    drift = verify_experiment_freeze(root, freeze)
    assert drift.ok, drift.errors
    assert drift.compared_fields >= 4, "drift detection must actually compare fields"


def test_comparing_nothing_is_visible_in_the_field_count(frozen_experiment):
    """A comparison function that silently compared nothing must not look like success."""
    root, _manifest, freeze = frozen_experiment
    assert verify_experiment_freeze(root, freeze).compared_fields > 0


def test_a_changed_configuration_is_reported_as_configuration_drift(frozen_experiment):
    root, manifest, freeze = frozen_experiment
    manifest["configuration"] = {"scope": "edited-after-freezing"}
    manifest["configuration_hash"] = configuration_identity(manifest["configuration"])
    _rewrite(root, manifest)

    drift = verify_experiment_freeze(root, freeze)
    assert not drift.ok
    assert any("configuration changed" in error for error in drift.errors)
    assert any("definition changed" in error for error in drift.errors)


def test_a_changed_description_is_reported_as_definition_drift(frozen_experiment):
    root, manifest, freeze = frozen_experiment
    manifest["description"] = "a different intent, written after the freeze"
    _rewrite(root, manifest)

    drift = verify_experiment_freeze(root, freeze)
    assert not drift.ok
    assert any("definition changed" in error for error in drift.errors)


def test_a_changed_input_set_is_reported(frozen_experiment):
    root, manifest, freeze = frozen_experiment
    manifest["dataset_freezes"] = [DIGEST]
    _rewrite(root, manifest)

    drift = verify_experiment_freeze(root, freeze)
    assert not drift.ok
    assert any("frozen inputs changed" in error for error in drift.errors)


def test_a_status_advance_is_reported_rather_than_silently_accepted(frozen_experiment):
    root, manifest, freeze = frozen_experiment
    manifest["status"] = "RUNNING"
    manifest.update(
        {
            "model_id": "some/model",
            "model_revision": REVISION,
            "seed": 0,
            "code_revision": clean_code_revision(),
            "environment_manifest": {"python": {"version": "3.12.0"}},
            "command": "engramfold-run example",
            "started_at": "2026-01-01T00:00:00+00:00",
        }
    )
    _rewrite(root, manifest)

    drift = verify_experiment_freeze(root, freeze)
    assert not drift.ok
    assert any("status changed" in error for error in drift.errors)


def test_the_manifest_disappearing_fails(frozen_experiment):
    root, _manifest, freeze = frozen_experiment
    (root / MANIFEST_PATH).unlink()
    drift = verify_experiment_freeze(root, freeze)
    assert not drift.ok
    assert any("no longer be compared" in error for error in drift.errors)


def test_a_freeze_naming_a_different_experiment_fails(frozen_experiment):
    root, _manifest, freeze = frozen_experiment
    other = dict(freeze)
    other["experiment_id"] = "someone-else"
    other["freeze_id"] = experiment_freeze_identity(other)
    drift = verify_experiment_freeze(root, other)
    assert not drift.ok
    assert any("disagree about experiment_id" in error for error in drift.errors)


def test_a_hand_edited_freeze_fails_its_own_identity_check(frozen_experiment):
    root, _manifest, freeze = frozen_experiment
    tampered = dict(freeze)
    tampered["status_at_freeze"] = "COMPLETED"
    drift = verify_experiment_freeze(root, tampered)
    assert not drift.ok
    assert any("freeze_id does not match" in error for error in drift.errors)


def test_identity_fields_are_exactly_the_declared_set(frozen_experiment):
    _root, _manifest, freeze = frozen_experiment
    assert experiment_freeze_identity(freeze) == freeze["freeze_id"]
    for field in IDENTITY_FIELDS:
        assert field in freeze


def test_volatile_freeze_fields_do_not_change_the_identity(frozen_experiment):
    _root, _manifest, freeze = frozen_experiment
    for field, value in (
        ("frozen_at", "2099-01-01T00:00:00+00:00"),
        ("repository_revision", "f" * 40),
        ("repository_dirty", True),
        ("code_revision_at_freeze", {"git_commit": "z" * 40, "git_dirty": True}),
        ("generated_by", "other"),
    ):
        mutated = dict(freeze)
        mutated[field] = value
        assert experiment_freeze_identity(mutated) == freeze["freeze_id"], field


def test_a_freeze_missing_a_generated_marker_is_rejected(frozen_experiment):
    _root, _manifest, freeze = frozen_experiment
    bad = {key: value for key, value in freeze.items() if key != "generated_by"}
    errors = validate_experiment_freeze_document(bad, origin="fixture")
    assert any("generated_by" in error for error in errors)


def test_a_freeze_with_a_non_digest_definition_hash_is_rejected(frozen_experiment):
    _root, _manifest, freeze = frozen_experiment
    bad = dict(freeze)
    bad["definition_hash"] = "nope"
    bad["freeze_id"] = experiment_freeze_identity(bad)
    errors = validate_experiment_freeze_document(bad, origin="fixture")
    assert any("definition_hash" in error for error in errors)


# ── Writing a definition freeze ──────────────────────────────────────────────


def test_writing_a_definition_freeze_then_regenerating_is_idempotent(frozen_experiment):
    root, manifest, _freeze = frozen_experiment
    first = write_experiment_freeze(
        root,
        build_experiment_freeze(manifest, MANIFEST_PATH, frozen_at=FROZEN_AT),
    )
    assert first.written
    path = root / first.output_path
    original = path.read_bytes()

    second = write_experiment_freeze(
        root,
        build_experiment_freeze(manifest, MANIFEST_PATH, frozen_at=FROZEN_AT),
    )
    assert second.unchanged
    assert second.byte_identical
    assert path.read_bytes() == original


def test_rewriting_a_frozen_definition_is_refused(frozen_experiment):
    """A frozen definition is immutable; if the experiment changed, it is a different experiment."""
    root, manifest, _freeze = frozen_experiment
    write_experiment_freeze(
        root, build_experiment_freeze(manifest, MANIFEST_PATH, frozen_at=FROZEN_AT)
    )

    changed = dict(manifest)
    changed["configuration"] = {"scope": "changed"}
    changed["configuration_hash"] = configuration_identity(changed["configuration"])

    with pytest.raises(IntegrityError) as excinfo:
        write_experiment_freeze(
            root, build_experiment_freeze(changed, MANIFEST_PATH, frozen_at=FROZEN_AT)
        )
    assert "refusing to overwrite the frozen definition" in str(excinfo.value)


def test_a_freeze_for_a_manifest_without_a_configuration_is_an_error():
    manifest = draft_manifest()
    manifest.pop("configuration")
    result = build_experiment_freeze(manifest, MANIFEST_PATH, frozen_at=FROZEN_AT)
    assert any("no configuration block" in error for error in result.errors)


def test_loading_a_missing_definition_freeze_reports_it(tmp_path: Path):
    freeze, errors = load_experiment_freeze(tmp_path, "experiments/freezes/gone.json")
    assert freeze is None
    assert errors and "missing" in errors[0]
