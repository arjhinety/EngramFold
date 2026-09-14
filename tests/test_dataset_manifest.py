"""Dataset manifest schema: required versus optional, and the immutability each source type claims.

The adversarial cases matter more than the happy path. A schema that accepts a
``huggingface`` source without a pinned commit, or a processed dataset without a seed, is a
schema that records a claim it cannot support.
"""

from __future__ import annotations

from typing import Any

import pytest

from engramfold.datasets.manifest import (
    REQUIRED_FIELDS,
    SOURCE_TYPES,
    dataset_identity,
    dataset_manifest_version,
    load_dataset_manifest,
    validate_dataset_manifest,
)
from engramfold.hashing import sha256_bytes

ORIGIN = "fixture"
REVISION = "a" * 40
DIGEST = sha256_bytes(b"content")


def base_manifest(**overrides: Any) -> dict[str, Any]:
    """A minimal valid manifest, so each test can vary exactly one thing."""
    manifest: dict[str, Any] = {
        "dataset_manifest_version": dataset_manifest_version(),
        "schema_version": 1,
        "dataset_id": "example",
        "dataset_version": "v1",
        "source_type": "local",
        "split": "train",
        "license": "Apache-2.0",
        "record_count": 10,
        "adapter_version": "adapter-1",
        "deduplication_policy": "exact-duplicate removal on canonical text",
        "contamination_policy": "unassessed; to be audited before any evaluation use",
        "source_files": ["datasets/raw/data.jsonl"],
        "source_file_hashes": {"datasets/raw/data.jsonl": DIGEST},
    }
    manifest.update(overrides)
    return manifest


def errors_for(manifest: dict[str, Any]) -> list[str]:
    return validate_dataset_manifest(manifest, origin=ORIGIN)


# ── Happy path ──────────────────────────────────────────────────────────────


def test_a_minimal_local_manifest_is_valid():
    assert errors_for(base_manifest()) == []


def test_every_required_field_is_named_in_the_required_set():
    """Pins the declaration against the behaviour directory."""
    for field in REQUIRED_FIELDS:
        manifest = base_manifest()
        manifest.pop(field)
        assert errors_for(manifest), f"removing required field {field} was accepted"


def test_every_source_type_is_recognised():
    assert set(SOURCE_TYPES) == {"huggingface", "local", "remote_url", "synthetic"}


# ─ Version fields ───────────────────────────────────────────────────────────


def test_two_version_fields_are_required_and_distinct():
    """Document-format version and record-schema version are different facts.

    Collapsing them into one ``version`` key would make changing a field name and changing the
    record layout look identical in the record.
    """
    assert errors_for(base_manifest(dataset_manifest_version=1, schema_version=1)) == []
    assert errors_for({**base_manifest(), "schema_version": 0})
    assert errors_for({**base_manifest(), "schema_version": "1"})


# ── Unknown fields ──────────────────────────────────────────────────────────


def test_a_renamed_field_is_rejected_rather_than_ignored():
    """The renamed field must fail, not be skipped, or every reader of it silently stops applying."""
    manifest = base_manifest()
    manifest["records"] = manifest.pop("record_count")
    errors = errors_for(manifest)
    assert any("unknown field" in error for error in errors)
    assert any("record_count" in error for error in errors)


def test_an_extra_advisory_field_is_rejected():
    """There is no free-form escape hatch: an uninterpreted field is an error."""
    assert errors_for(base_manifest(temperature=0.7))


# ── Identifier and path hygiene ──────────────────────────────────────────────


@pytest.mark.parametrize("bad_id", ["Example", "-x", "a b", "", None, 3])
def test_identifier_shape_is_enforced(bad_id: Any):
    assert errors_for(base_manifest(dataset_id=bad_id))


def test_absolute_paths_are_rejected():
    """A machine-specific path makes the record describe one machine, not the object."""
    errors = errors_for(
        base_manifest(
            source_files=["/home/someone/data.jsonl"],
            source_file_hashes={"/home/someone/data.jsonl": DIGEST},
        )
    )
    assert any("absolute" in error for error in errors)


def test_backslash_paths_are_rejected():
    errors = errors_for(
        base_manifest(
            source_files=["datasets\\raw\\data.jsonl"],
            source_file_hashes={"datasets\\raw\\data.jsonl": DIGEST},
        )
    )
    assert any("backslash" in error for error in errors)


def test_parent_traversal_is_rejected():
    errors = errors_for(
        base_manifest(
            source_files=["../outside.jsonl"],
            source_file_hashes={"../outside.jsonl": DIGEST},
        )
    )
    assert any("escapes the repository" in error for error in errors)


def test_path_and_hash_lists_must_pair():
    errors = errors_for(base_manifest(source_file_hashes={}))
    assert any("no digest" in error for error in errors)


def test_a_digest_for_an_unlisted_path_is_an_error():
    errors = errors_for(
        base_manifest(
            source_files=["datasets/raw/data.jsonl"],
            source_file_hashes={
                "datasets/raw/data.jsonl": DIGEST,
                "datasets/raw/other.jsonl": DIGEST,
            },
        )
    )
    assert any("not in source_files" in error for error in errors)


def test_an_uppercase_digest_is_rejected():
    errors = errors_for(
        base_manifest(source_file_hashes={"datasets/raw/data.jsonl": DIGEST.upper()})
    )
    assert any("sha256" in error for error in errors)


# ── Licence ──────────────────────────────────────────────────────────────────


def test_a_licence_must_be_named():
    assert errors_for(base_manifest(license=""))


def test_an_unknown_licence_requires_a_written_rationale():
    """So "we did not check" is distinguishable from "the licence is permissive"."""
    errors = errors_for(base_manifest(license="UNKNOWN"))
    assert any("license_unknown_rationale" in error for error in errors)
    assert (
        errors_for(base_manifest(license="UNKNOWN", license_unknown_rationale="upstream unclear"))
        == []
    )


def test_a_known_licence_needs_no_rationale():
    assert errors_for(base_manifest(license="MIT")) == []


# ─ Source types ─────────────────────────────────────────────────────────────


def test_huggingface_requires_a_full_revision():
    manifest = base_manifest(source_type="huggingface", source_repository="owner/name")
    assert any("source_revision" in error for error in errors_for(manifest))


def test_huggingface_rejects_a_branch_name():
    """A branch resolves to different bytes over time, so it pins nothing."""
    manifest = base_manifest(
        source_type="huggingface",
        source_repository="owner/name",
        source_revision="main",
    )
    errors = errors_for(manifest)
    assert any("moving branch" in error for error in errors)


def test_huggingface_rejects_an_abbreviated_revision():
    manifest = base_manifest(
        source_type="huggingface",
        source_repository="owner/name",
        source_revision="abc1234",
    )
    errors = errors_for(manifest)
    assert any("abbreviated" in error for error in errors)


def test_huggingface_accepts_a_full_commit():
    manifest = base_manifest(
        source_type="huggingface",
        source_repository="owner/name",
        source_revision=REVISION,
    )
    assert errors_for(manifest) == []


def test_huggingface_repository_must_be_owner_slash_name():
    manifest = base_manifest(
        source_type="huggingface",
        source_repository="justaname",
        source_revision=REVISION,
    )
    assert any("owner" in error for error in errors_for(manifest))


def test_local_requires_files_and_hashes():
    manifest = base_manifest(source_type="local")
    manifest.pop("source_files")
    manifest.pop("source_file_hashes")
    assert errors_for(manifest)


def test_remote_url_requires_a_fetch_time_and_hashes():
    manifest = base_manifest(source_type="remote_url", source_url="https://example.invalid/d.jsonl")
    assert any("retrieved_at" in error for error in errors_for(manifest))
    ok = base_manifest(
        source_type="remote_url",
        source_url="https://example.invalid/d.jsonl",
        retrieved_at="2026-01-01T00:00:00Z",
        source_file_hashes={"datasets/raw/data.jsonl": DIGEST},
    )
    assert errors_for(ok) == []


def test_remote_url_must_be_http():
    manifest = base_manifest(
        source_type="remote_url",
        source_url="ftp://example.invalid/d",
        retrieved_at="2026-01-01T00:00:00Z",
    )
    assert any("http(s)" in error for error in errors_for(manifest))


def test_an_unknown_source_type_is_rejected():
    assert errors_for(base_manifest(source_type="scraped"))


# ── Synthetic: schema only ───────────────────────────────────────────────────


def synthetic_manifest(**overrides: Any) -> dict[str, Any]:
    block = {
        "generator_model": "some/model",
        "model_revision": REVISION,
        "generation_config_hash": DIGEST,
        "prompt_template_version": "template-v3",
        "generation_code_revision": REVISION,
        "raw_generation_artifact": "datasets/raw/generated.jsonl",
    }
    block.update(overrides.pop("synthetic", {}))
    manifest = base_manifest(source_type="synthetic")
    manifest["synthetic"] = block
    manifest.update(overrides)
    return manifest


def test_synthetic_requires_a_generator_block():
    manifest = base_manifest(source_type="synthetic")
    errors = errors_for(manifest)
    assert any("synthetic" in error for error in errors)


def test_a_complete_synthetic_block_is_valid():
    assert errors_for(synthetic_manifest()) == []


@pytest.mark.parametrize(
    "missing",
    [
        "generator_model",
        "model_revision",
        "generation_config_hash",
        "prompt_template_version",
        "generation_code_revision",
        "raw_generation_artifact",
    ],
)
def test_each_synthetic_identification_field_is_required(missing: str):
    """If generated data cannot name these, its records are not reproducible."""
    manifest = synthetic_manifest()
    manifest["synthetic"].pop(missing)
    assert errors_for(manifest), f"missing synthetic.{missing} was accepted"


def test_synthetic_model_revision_must_be_immutable():
    assert errors_for(synthetic_manifest(synthetic={"model_revision": "main"}))


def test_synthetic_sampling_requires_a_seed():
    """A generator run at a non-zero temperature without a seed cannot be repeated."""
    manifest = synthetic_manifest(synthetic={"generation_config": {"temperature": 0.9}})
    errors = errors_for(manifest)
    assert any("random_seed" in error for error in errors)
    with_seed = synthetic_manifest(
        synthetic={"generation_config": {"temperature": 0.9}, "random_seed": 7}
    )
    assert errors_for(with_seed) == []


def test_synthetic_greedy_decoding_needs_no_seed():
    manifest = synthetic_manifest(synthetic={"generation_config": {"temperature": 0.0}})
    assert errors_for(manifest) == []


def test_synthetic_generation_config_hash_must_be_a_digest():
    assert errors_for(synthetic_manifest(synthetic={"generation_config_hash": "not-a-digest"}))


# ─ Processing reproducibility ───────────────────────────────────────────────


def processed_manifest(**overrides: Any) -> dict[str, Any]:
    manifest = base_manifest()
    manifest.update(
        {
            "processed_files": ["datasets/processed/shard-0000.jsonl"],
            "processed_file_hashes": {"datasets/processed/shard-0000.jsonl": DIGEST},
            "processing_command": "python -m adapter.prepare --seed 7",
            "processing_code_revision": REVISION,
            "processing_config_hash": DIGEST,
            "random_seed": 7,
        }
    )
    manifest.update(overrides)
    return manifest


def test_a_fully_described_processed_dataset_is_valid():
    assert errors_for(processed_manifest()) == []


@pytest.mark.parametrize(
    "field",
    ["processing_command", "processing_code_revision", "processing_config_hash", "random_seed"],
)
def test_processing_without_a_reproducibility_field_is_rejected(field: str):
    """Processed bytes whose transformation is unrecoverable are not reproducible evidence."""
    manifest = processed_manifest()
    manifest.pop(field)
    errors = errors_for(manifest)
    assert errors, f"processed_files without {field} was accepted"


def test_processing_code_revision_must_be_immutable():
    assert errors_for(processed_manifest(processing_code_revision="feature-branch"))


def test_an_unexpanded_command_template_is_rejected():
    """A command still containing a placeholder is not the command that ran."""
    errors = errors_for(processed_manifest(processing_command="python -m adapter {{SPLIT}}"))
    assert any("placeholder" in error for error in errors)


def test_processed_and_source_hashes_are_validated_independently():
    manifest = processed_manifest(processed_file_hashes={})
    assert any("no digest" in error for error in errors_for(manifest))


# ── Counts ───────────────────────────────────────────────────────────────────


def test_zero_records_requires_an_intended_empty_rationale():
    """Zero is usually a filter that matched nothing, so it must be declared."""
    errors = errors_for(base_manifest(record_count=0))
    assert any("empty_dataset_rationale" in error for error in errors)
    ok = base_manifest(
        record_count=0, empty_dataset_rationale="the negative-control split is empty"
    )
    assert errors_for(ok) == []


def test_a_rationale_on_a_non_empty_dataset_is_a_contradiction():
    errors = errors_for(base_manifest(record_count=5, empty_dataset_rationale="stale note"))
    assert any("remove the rationale" in error for error in errors)


def test_a_boolean_record_count_is_rejected():
    """``True`` is an ``int`` in Python, and a count of one is not what was meant."""
    errors = errors_for(base_manifest(record_count=True))
    assert any("integer" in error for error in errors)


def test_a_negative_record_count_is_rejected():
    assert errors_for(base_manifest(record_count=-1))


# ── Identity ─────────────────────────────────────────────────────────────────


def test_identity_ignores_timestamps_and_formatting():
    left = base_manifest(retrieved_at="2026-01-01T00:00:00Z")
    right = base_manifest(retrieved_at="2027-09-09T12:00:00Z")
    assert dataset_identity(left) == dataset_identity(right)


def test_identity_changes_with_content():
    assert dataset_identity(base_manifest()) != dataset_identity(base_manifest(record_count=11))


def test_identity_is_independent_of_key_order():
    first = base_manifest()
    second = {key: first[key] for key in reversed(list(first))}
    assert dataset_identity(first) == dataset_identity(second)


# ── Loading ─────────────────────────────────────────────────────────────────


def test_a_missing_manifest_yields_an_error_not_a_silent_none(tmp_path):
    manifest, errors = load_dataset_manifest(tmp_path, "datasets/manifests/gone.json")
    assert manifest is None
    assert errors and "missing" in errors[0]


def test_a_malformed_manifest_yields_an_error_not_a_silent_none(tmp_path):
    path = tmp_path / "datasets/manifests/bad.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    manifest, errors = load_dataset_manifest(tmp_path, "datasets/manifests/bad.json")
    assert manifest is None
    assert errors


def test_loading_never_returns_none_with_empty_errors(tmp_path):
    """``(None, [])`` would be indistinguishable from a valid empty manifest."""
    for content in ("{ not json", "[]", ""):
        path = tmp_path / "datasets/manifests/x.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        manifest, errors = load_dataset_manifest(tmp_path, "datasets/manifests/x.json")
        assert not (manifest is None and errors == []), content
