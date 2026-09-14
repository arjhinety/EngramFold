"""Dataset freezes: mutate one thing at a time and prove verification fails.

This is the mutation matrix. Every test changes exactly one fact about a frozen dataset and
asserts that verification fails, **and names that fact**. A freeze that cannot detect a changed
artifact is worse than no freeze, because it is trusted.

The counterpart tests matter as much: an unchanged freeze must verify, and regenerating it must
be idempotent. A freeze that always fails is as useless as one that never does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from engramfold.datasets.freeze import (
    GENERATED_BY_DEFAULT,
    IDENTITY_FIELDS,
    build_freeze,
    canonical_freeze_text,
    freeze_identity,
    freeze_relative_path,
    load_freeze,
    validate_freeze_document,
    verify_freeze,
    write_freeze,
)
from engramfold.errors import IntegrityError
from engramfold.hashing import content_hash, sha256_bytes

MANIFEST_PATH = "datasets/manifests/example.json"
ARTIFACT_PATH = "datasets/records.jsonl"
CREATED_AT = "2026-01-01T00:00:00+00:00"


def make_dataset(root: Path) -> dict[str, Any]:
    """A one-artifact dataset on disk, plus the manifest describing it."""
    artifact = root / ARTIFACT_PATH
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b'{"record": 1}\n')
    digest = content_hash(artifact)
    return {
        "dataset_manifest_version": 1,
        "schema_version": 1,
        "dataset_id": "example",
        "dataset_version": "v1",
        "source_type": "local",
        "split": "train",
        "license": "CC0-1.0",
        "record_count": 1,
        "adapter_version": "fixture",
        "deduplication_policy": "none",
        "contamination_policy": "unassessed",
        "source_files": [ARTIFACT_PATH],
        "source_file_hashes": {ARTIFACT_PATH: digest},
    }


def freeze_of(root: Path, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    result = build_freeze(
        root,
        manifest if manifest is not None else make_dataset(root),
        MANIFEST_PATH,
        created_at=CREATED_AT,
        repository_revision="0" * 40,
        repository_dirty=False,
        frozen_by_command="pytest",
    )
    assert result.errors == [], result.errors
    return result.freeze


@pytest.fixture
def frozen(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    manifest_file = root / MANIFEST_PATH
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return root, freeze_of(root, manifest)


# ── Happy paths ─────────────────────────────────────────────────────────────


def test_an_unchanged_freeze_verifies(frozen):
    root, freeze = frozen
    verification = verify_freeze(root, freeze)
    assert verification.ok, verification.errors
    assert verification.artifacts_checked == 1
    assert verification.manifest_checked


def test_verification_reports_how_much_it_checked(frozen):
    """A verification that checked nothing must not read as one that checked everything."""
    root, freeze = frozen
    verification = verify_freeze(root, freeze)
    assert verification.artifacts_checked == len(freeze["artifacts"])
    assert verification.artifacts_missing == 0
    assert verification.artifacts_changed == 0


# ── Mutation matrix: change one thing, expect a specific failure ─────────────


def test_a_missing_artifact_fails_and_is_distinguished_from_a_change(frozen):
    root, freeze = frozen
    (root / ARTIFACT_PATH).unlink()
    verification = verify_freeze(root, freeze)
    assert not verification.ok
    assert verification.artifacts_missing == 1
    assert verification.artifacts_changed == 0
    assert any("missing" in error for error in verification.errors)
    assert not any("changed" in error for error in verification.errors)


def test_one_byte_of_a_frozen_artifact_changing_fails(frozen):
    root, freeze = frozen
    (root / ARTIFACT_PATH).write_bytes(b'{"record": 2}\n')
    verification = verify_freeze(root, freeze)
    assert not verification.ok
    assert verification.artifacts_changed == 1
    assert any("changed" in error for error in verification.errors)


def test_an_artifact_size_change_is_reported(frozen):
    root, freeze = frozen
    target = root / ARTIFACT_PATH
    target.write_bytes(target.read_bytes() + b"extra")
    verification = verify_freeze(root, freeze)
    assert not verification.ok
    assert any("size changed" in error or "changed" in error for error in verification.errors)


def test_the_manifest_contents_changing_fails(frozen):
    root, freeze = frozen
    manifest_file = root / MANIFEST_PATH
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["notes"] = "edited after the freeze"
    manifest_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    verification = verify_freeze(root, freeze)
    assert not verification.ok
    assert any("manifest contents changed" in error for error in verification.errors)


def test_the_record_count_changing_fails(frozen):
    root, freeze = frozen
    manifest_file = root / MANIFEST_PATH
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["record_count"] = 99
    manifest_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    verification = verify_freeze(root, freeze)
    assert not verification.ok
    assert any("record count changed" in error for error in verification.errors)


def test_the_manifest_disappearing_fails(frozen):
    """A referenced file disappearing must fail, not be skipped."""
    root, freeze = frozen
    (root / MANIFEST_PATH).unlink()
    verification = verify_freeze(root, freeze)
    assert not verification.ok
    assert any("manifest this freeze pins is missing" in error for error in verification.errors)


def test_pointing_the_freeze_at_a_different_manifest_fails(frozen, tmp_path: Path):
    """The most dangerous drift: both files validate individually but disagree about identity."""
    root, freeze = frozen
    other = dict(freeze)
    other["dataset_id"] = "some-other-dataset"
    other["freeze_id"] = freeze_identity(other)
    verification = verify_freeze(root, other)
    assert not verification.ok
    assert any("disagree about dataset_id" in error for error in verification.errors)


def test_a_manifest_version_mismatch_fails(frozen):
    root, freeze = frozen
    other = dict(freeze)
    other["dataset_version"] = "v2"
    other["freeze_id"] = freeze_identity(other)
    verification = verify_freeze(root, other)
    assert not verification.ok
    assert any("dataset_version" in error for error in verification.errors)


def test_a_frozen_artifact_the_manifest_no_longer_declares_fails(frozen):
    root, freeze = frozen
    manifest_file = root / MANIFEST_PATH
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["source_files"] = []
    manifest["source_file_hashes"] = {}
    # Keep the recorded manifest hash consistent so this test isolates composition, not hashing.
    from engramfold.datasets.manifest import dataset_identity

    freeze["manifest_hash"] = dataset_identity(manifest)
    freeze["freeze_id"] = freeze_identity(freeze)
    manifest_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    verification = verify_freeze(root, freeze)
    assert not verification.ok
    assert any(
        "no longer declares" in error or "frozen set is incomplete" in error
        for error in verification.errors
    )


def test_an_artifact_declared_but_not_frozen_fails(frozen):
    root, freeze = frozen
    extra = root / "datasets/records-2.jsonl"
    extra.write_bytes(b'{"record": 2}\n')
    manifest_file = root / MANIFEST_PATH
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["source_files"] = [ARTIFACT_PATH, "datasets/records-2.jsonl"]
    manifest["source_file_hashes"] = {
        ARTIFACT_PATH: content_hash(root / ARTIFACT_PATH),
        "datasets/records-2.jsonl": content_hash(extra),
    }
    from engramfold.datasets.manifest import dataset_identity

    freeze["manifest_hash"] = dataset_identity(manifest)
    freeze["freeze_id"] = freeze_identity(freeze)
    manifest_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    verification = verify_freeze(root, freeze)
    assert not verification.ok
    assert any("not frozen" in error or "incomplete" in error for error in verification.errors)


# ─ Malformed freezes ───────────────────────────────────────────────────────


def test_a_hand_edited_freeze_fails_its_own_identity_check(frozen):
    """The document has been edited since it was frozen, and that must be detectable."""
    root, freeze = frozen
    tampered = dict(freeze)
    tampered["record_counts"] = {"total": 500}
    verification = verify_freeze(root, tampered)
    assert not verification.ok
    assert any("freeze_id does not match" in error for error in verification.errors)


def test_an_empty_artifact_list_is_rejected():
    """A freeze over no bytes cannot detect that an input changed."""
    errors = validate_freeze_document(
        {
            "freeze_format_version": 1,
            "dataset_id": "x",
            "dataset_version": "v1",
            "manifest_path": MANIFEST_PATH,
            "manifest_hash": "a" * 64,
            "record_schema_version": 1,
            "record_counts": {"total": 0},
            "artifact_role": "source",
            "artifacts": [],
            "created_at": CREATED_AT,
            "generated_by": GENERATED_BY_DEFAULT,
            "freeze_id": "b" * 64,
        },
        origin="fixture",
    )
    assert any("pins no bytes" in error for error in errors)


def test_a_freeze_with_an_unknown_role_is_rejected(frozen):
    _root, freeze = frozen
    bad = dict(freeze)
    bad["artifact_role"] = "whatever"
    assert any("artifact_role" in error for error in validate_freeze_document(bad, origin="f"))


def test_a_freeze_missing_a_generated_marker_is_rejected():
    errors = validate_freeze_document(
        {
            "freeze_format_version": 1,
            "dataset_id": "x",
            "dataset_version": "v1",
            "manifest_path": MANIFEST_PATH,
            "manifest_hash": "a" * 64,
            "record_schema_version": 1,
            "record_counts": {"total": 1},
            "artifact_role": "source",
            "artifacts": [{"path": ARTIFACT_PATH, "sha256": "b" * 64, "size_bytes": 1}],
            "created_at": CREATED_AT,
            "freeze_id": "c" * 64,
        },
        origin="fixture",
    )
    assert any("generated_by" in error for error in errors)


def test_a_freeze_with_a_non_integer_count_is_rejected(frozen):
    _root, freeze = frozen
    bad = dict(freeze)
    bad["record_counts"] = {"total": "many"}
    bad["freeze_id"] = freeze_identity(bad)
    errors = validate_freeze_document(bad, origin="fixture")
    assert any("non-negative integer" in error for error in errors)


def test_a_freeze_with_a_duplicate_artifact_path_is_rejected(frozen):
    _root, freeze = frozen
    bad = dict(freeze)
    bad["artifacts"] = [*freeze["artifacts"], *freeze["artifacts"]]
    bad["freeze_id"] = freeze_identity(bad)
    errors = validate_freeze_document(bad, origin="fixture")
    assert any("duplicates artifact path" in error for error in errors)


def test_a_freeze_with_an_absolute_manifest_path_is_rejected(frozen):
    _root, freeze = frozen
    bad = dict(freeze)
    bad["manifest_path"] = "/abs/path/manifest.json"
    bad["freeze_id"] = freeze_identity(bad)
    errors = validate_freeze_document(bad, origin="fixture")
    assert any("absolute" in error for error in errors)


def test_a_malformed_freeze_file_yields_an_error_not_a_silent_none(tmp_path: Path):
    path = tmp_path / "datasets/freezes/bad.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    freeze, errors = load_freeze(tmp_path, "datasets/freezes/bad.json")
    assert freeze is None
    assert errors


def test_a_missing_freeze_file_yields_an_error(tmp_path: Path):
    freeze, errors = load_freeze(tmp_path, "datasets/freezes/gone.json")
    assert freeze is None
    assert errors and "missing" in errors[0]


# ── Identity separation ──────────────────────────────────────────────────────


def test_identity_fields_are_exactly_the_declared_set(frozen):
    _root, freeze = frozen
    recomputed = freeze_identity(freeze)
    assert recomputed == freeze["freeze_id"]
    for field in IDENTITY_FIELDS:
        assert field in freeze, field


def test_changing_a_volatile_field_does_not_change_the_freeze_id(frozen):
    """Timestamps and release provenance describe the freeze event, not the data."""
    _root, freeze = frozen
    for field, value in (
        ("created_at", "2099-12-31T23:59:59+00:00"),
        ("repository_revision", "f" * 40),
        ("repository_dirty", True),
        ("frozen_by_command", "something else"),
        ("generated_by", "another tool"),
    ):
        mutated = dict(freeze)
        mutated[field] = value
        assert freeze_identity(mutated) == freeze["freeze_id"], field


def test_changing_an_identity_field_does_change_the_freeze_id(frozen):
    _root, freeze = frozen
    for field, value in (
        ("dataset_id", "other"),
        ("dataset_version", "v9"),
        ("manifest_hash", "0" * 64),
        ("artifact_role", "processed"),
        ("record_counts", {"total": 5}),
    ):
        mutated = dict(freeze)
        mutated[field] = value
        assert freeze_identity(mutated) != freeze["freeze_id"], field


def test_the_freeze_id_is_a_digest(frozen):
    _root, freeze = frozen
    assert len(str(freeze["freeze_id"])) == 64
    int(str(freeze["freeze_id"]), 16)


def test_freeze_relative_path_is_derived_from_the_id(frozen):
    _root, freeze = frozen
    assert freeze_relative_path(str(freeze["freeze_id"])) == (
        f"datasets/freezes/{freeze['freeze_id']}.json"
    )


# ── Writing, idempotence and immutability ────────────────────────────────────


def test_writing_a_freeze_then_regenerating_it_is_idempotent(tmp_path: Path):
    """Byte-for-byte idempotence, which is what makes "has this changed?" answerable."""
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    manifest_file = root / MANIFEST_PATH
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    first = write_freeze(root, build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT))
    assert first.written
    path = root / first.output_path
    original_bytes = path.read_bytes()

    second = write_freeze(root, build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT))
    assert not second.written
    assert second.unchanged
    assert second.byte_identical, "regeneration must reproduce the same bytes"
    assert path.read_bytes() == original_bytes


def test_regenerating_with_a_different_timestamp_leaves_the_file_untouched(tmp_path: Path):
    """An unchanged freeze is not rewritten merely because it was regenerated later."""
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    (root / MANIFEST_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_PATH).write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    first = write_freeze(root, build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT))
    path = root / first.output_path
    original_bytes = path.read_bytes()

    second = write_freeze(
        root,
        build_freeze(root, manifest, MANIFEST_PATH, created_at="2030-06-06T06:06:06+00:00"),
    )
    assert second.unchanged
    assert not second.byte_identical, "the timestamp differs, so the bytes would differ"
    assert path.read_bytes() == original_bytes, "but the frozen file must not be rewritten"


def test_writing_a_different_freeze_over_an_existing_one_is_refused(tmp_path: Path):
    """Replacing a freeze is a rewrite of frozen history, so it must raise."""
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    (root / MANIFEST_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_PATH).write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    first = write_freeze(root, build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT))
    path = root / first.output_path

    # Force a collision at the same path with a different identity.
    worker = root / "worker"
    changed = make_dataset(worker)
    changed["record_count"] = 42
    (worker / MANIFEST_PATH).parent.mkdir(parents=True, exist_ok=True)
    (worker / MANIFEST_PATH).write_text(
        json.dumps(changed, sort_keys=True) + "\n", encoding="utf-8"
    )
    conflicting = build_freeze(worker, changed, MANIFEST_PATH, created_at=CREATED_AT)
    conflicting.output_path = first.output_path
    conflicting.freeze["dataset_version"] = "v2"

    with pytest.raises(IntegrityError) as excinfo:
        write_freeze(root, conflicting)
    assert "refusing to overwrite" in str(excinfo.value)
    assert path.exists()


def test_a_freeze_with_build_errors_is_not_written(tmp_path: Path):
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    manifest["source_file_hashes"][ARTIFACT_PATH] = "f" * 64  # disagrees with the bytes
    result = build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT)
    assert result.errors
    written = write_freeze(root, result)
    assert not written.written
    assert not (root / result.output_path).exists()


def test_building_a_freeze_records_the_artifact_digest_from_the_bytes(tmp_path: Path):
    """The recorded digest is verified against disk, not copied from the manifest's claim."""
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    result = build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT)
    assert result.errors == []
    assert result.freeze["artifacts"][0]["sha256"] == content_hash(root / ARTIFACT_PATH)
    assert result.freeze["artifacts"][0]["size_bytes"] == (root / ARTIFACT_PATH).stat().st_size


def test_building_a_freeze_detects_a_manifest_hash_that_disagrees(tmp_path: Path):
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    manifest["source_file_hashes"][ARTIFACT_PATH] = sha256_bytes(b"something else")
    result = build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT)
    assert any("manifest and the bytes disagree" in error for error in result.errors)


def test_building_a_freeze_with_no_artifacts_is_an_error(tmp_path: Path):
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    manifest.pop("source_files")
    manifest.pop("source_file_hashes")
    result = build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT)
    assert any("pins no bytes" in error or "commit to nothing" in error for error in result.errors)


def test_processed_files_take_precedence_over_source_files(tmp_path: Path):
    """A freeze over a processed dataset records what the pipeline emitted."""
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    processed = root / "datasets/processed.jsonl"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_bytes(b"processed\n")
    manifest["processed_files"] = ["datasets/processed.jsonl"]
    manifest["processed_file_hashes"] = {"datasets/processed.jsonl": content_hash(processed)}
    manifest["processing_command"] = "cmd"
    manifest["processing_code_revision"] = "0" * 40
    manifest["processing_config_hash"] = "1" * 64
    manifest["random_seed"] = 0

    result = build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT)
    assert result.errors == []
    assert result.freeze["artifact_role"] == "processed"
    assert [entry["path"] for entry in result.freeze["artifacts"]] == ["datasets/processed.jsonl"]


def test_canonical_freeze_text_is_stable_and_ends_with_a_newline(frozen):
    _root, freeze = frozen
    text = canonical_freeze_text(freeze)
    assert text == canonical_freeze_text(freeze)
    assert text.endswith("\n")
    assert json.loads(text) == freeze


def test_a_freeze_is_written_as_parseable_json(tmp_path: Path):
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    (root / MANIFEST_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_PATH).write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    result = write_freeze(root, build_freeze(root, manifest, MANIFEST_PATH, created_at=CREATED_AT))
    on_disk = json.loads((root / result.output_path).read_text(encoding="utf-8"))
    assert on_disk == result.freeze
    assert verify_freeze(root, on_disk).ok
