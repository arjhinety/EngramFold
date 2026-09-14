"""Artifact manifests: identity by id and digest, and verification of the bytes.

Two properties carry the weight here. An artifact is identified by a stable id plus a content
digest, never by a filename; and an artifact whose bytes cannot be re-read is reported as
*unverifiable with a reason* rather than as verified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engramfold.artifacts.manifest import (
    REQUIRED_FIELDS,
    STORAGE_KINDS,
    artifact_identity,
    artifact_manifest_version,
    load_artifact_manifest,
    validate_artifact_manifest,
    verify_artifact,
)
from engramfold.hashing import content_hash

ORIGIN = "fixture"
REVISION = "a" * 40
DIGEST = "b" * 64
FREEZE_ID = "c" * 64
CREATED_AT = "2026-01-01T00:00:00+00:00"


def code_revision() -> dict[str, Any]:
    return {
        "provenance_record_version": 1,
        "git_commit": REVISION,
        "git_branch": "main",
        "git_dirty": False,
    }


def manifest_for(
    path: str | None = "artifacts/data/result.json", **overrides: Any
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "artifact_manifest_version": artifact_manifest_version(),
        "artifact_id": "example-result",
        "storage_kind": "repository",
        "artifact_sha256": DIGEST,
        "created_by_experiment": "example",
        "command": "engramfold-run example",
        "code_revision": code_revision(),
        "environment_manifest_hash": DIGEST,
        "input_dataset_freezes": [FREEZE_ID],
        "created_at": CREATED_AT,
    }
    if path is not None:
        manifest["artifact_path"] = path
    manifest.update(overrides)
    return manifest


def errors_for(manifest: dict[str, Any]) -> list[str]:
    return validate_artifact_manifest(manifest, origin=ORIGIN)


# ── Shape ────────────────────────────────────────────────────────────────────


def test_a_minimal_repository_artifact_is_valid():
    assert errors_for(manifest_for()) == []


def test_every_declared_required_field_is_enforced():
    for field in REQUIRED_FIELDS:
        manifest = manifest_for()
        manifest.pop(field)
        assert errors_for(manifest), f"removing {field} was accepted"


def test_an_unknown_field_is_rejected():
    assert errors_for(manifest_for(filename="result.json"))


def test_the_storage_kinds_are_a_closed_vocabulary():
    assert set(STORAGE_KINDS) == {"repository", "external", "remote"}
    assert errors_for(manifest_for(storage_kind="somewhere"))


# ── Identity: ids and hashes, never filenames ───────────────────────────────


def test_the_artifact_id_shape_is_enforced():
    assert errors_for(manifest_for(artifact_id="Example Result"))


def test_the_artifact_digest_must_be_a_sha256():
    errors = errors_for(manifest_for(artifact_sha256="not-a-digest"))
    assert any("no identity beyond its filename" in error for error in errors)


def test_the_manifest_identity_is_distinct_from_the_artifact_digest():
    """This identifies the description; artifact_sha256 identifies the bytes."""
    manifest = manifest_for()
    assert artifact_identity(manifest) != manifest["artifact_sha256"]
    assert len(artifact_identity(manifest)) == 64


def test_the_manifest_identity_ignores_timestamps():
    assert artifact_identity(
        manifest_for(created_at="2026-01-01T00:00:00+00:00")
    ) == artifact_identity(manifest_for(created_at="2030-01-01T00:00:00+00:00"))


def test_the_artifact_digest_must_be_recorded_even_for_external_storage():
    """Without a digest, identity would rest on a filename or a URI."""
    manifest = manifest_for(path=None, storage_kind="external", artifact_uri="s3://b/k")
    manifest.pop("artifact_sha256")
    errors = errors_for(manifest)
    assert any("artifact_sha256" in error for error in errors)

    # Present but not a digest is the other case, and it says why the digest matters.
    invalid = manifest_for(
        path=None, storage_kind="external", artifact_uri="s3://b/k", artifact_sha256="xyz"
    )
    messages = errors_for(invalid)
    assert any("no identity beyond its filename" in error for error in messages)


def test_an_external_artifact_with_a_digest_is_valid():
    """The digest is what makes the artifact identifiable wherever its bytes live."""
    manifest = manifest_for(path=None, storage_kind="external", artifact_uri="s3://bucket/key")
    assert errors_for(manifest) == []


# ── Storage ─────────────────────────────────────────────────────────────────


def test_repository_storage_requires_a_relative_path():
    assert errors_for(manifest_for(storage_kind="repository", artifact_path="/abs/result.json"))


def test_external_storage_requires_a_uri():
    manifest = manifest_for(path=None, storage_kind="external")
    errors = errors_for(manifest)
    assert any("artifact_uri" in error for error in errors)

    with_uri = manifest_for(path=None, storage_kind="external", artifact_uri="s3://bucket/key")
    assert errors_for(with_uri) == []


def test_a_bare_filename_is_not_treated_as_a_repository_path():
    """It is a valid relative path, but it must not be mistaken for an identity."""
    manifest = manifest_for(artifact_path="result.json")
    assert errors_for(manifest) == []
    assert artifact_identity(manifest) != artifact_identity(manifest_for())


# ── Code revision ───────────────────────────────────────────────────────────


def test_a_bare_commit_is_not_a_provenance_record():
    errors = errors_for(manifest_for(code_revision=REVISION))
    assert any("provenance record" in error for error in errors)


def test_a_dirty_tree_must_carry_an_override():
    dirty = {**code_revision(), "git_dirty": True}
    errors = errors_for(manifest_for(code_revision=dirty))
    assert any("dirty_override_reason" in error for error in errors)


def test_a_dirty_tree_with_an_override_is_accepted_and_the_dirt_is_still_recorded():
    dirty = {
        **code_revision(),
        "git_dirty": True,
        "dirty_override_reason": "exploratory; modification recorded in full",
    }
    manifest = manifest_for(code_revision=dirty)
    assert errors_for(manifest) == []
    assert manifest["code_revision"]["git_dirty"] is True


def test_an_unknowable_dirty_state_is_rejected():
    """An artifact that cannot be attributed to a checkout state is not evidence."""
    unknown = {**code_revision(), "git_dirty": None}
    errors = errors_for(manifest_for(code_revision=unknown))
    assert any("unknowable" in error for error in errors)


# ── Inputs ─────────────────────────────────────────────────────────────────


def test_inputs_are_pinned_by_freeze_id():
    errors = errors_for(manifest_for(input_dataset_freezes=["datasets/records.jsonl"]))
    assert any("not a freeze id" in error for error in errors)


def test_an_empty_input_list_is_rejected():
    """An artifact consuming no recorded input must say so, not carry a bare empty list."""
    errors = errors_for(manifest_for(input_dataset_freezes=[]))
    assert any("empty-input rationale" in error for error in errors)


def test_duplicate_inputs_are_rejected():
    assert errors_for(manifest_for(input_dataset_freezes=[FREEZE_ID, FREEZE_ID]))


# ── Model pinning ───────────────────────────────────────────────────────────


def test_a_model_id_without_a_revision_is_rejected():
    assert errors_for(manifest_for(model_id="some/model"))


def test_a_model_revision_must_be_immutable():
    assert errors_for(manifest_for(model_id="some/model", model_revision="main"))
    assert errors_for(manifest_for(model_id="some/model", model_revision=REVISION)) == []


# ── Byte verification ────────────────────────────────────────────────────────


def test_a_repository_artifact_whose_bytes_match_is_verified(tmp_path: Path):
    payload = b'{"result": 1}\n'
    target = tmp_path / "artifacts/data/result.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)

    manifest = manifest_for(artifact_sha256=content_hash(target), artifact_size_bytes=len(payload))
    verification = verify_artifact(tmp_path, manifest)
    assert verification.ok, verification.errors
    assert verification.bytes_verified is True


def test_a_repository_artifact_whose_bytes_changed_fails(tmp_path: Path):
    target = tmp_path / "artifacts/data/result.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"result": 1}\n')
    recorded = content_hash(target)
    target.write_bytes(b'{"result": 2}\n')

    verification = verify_artifact(tmp_path, manifest_for(artifact_sha256=recorded))
    assert not verification.ok
    assert verification.bytes_verified is False
    assert any("artifact changed" in error for error in verification.errors)


def test_a_missing_repository_artifact_fails_and_is_named(tmp_path: Path):
    verification = verify_artifact(tmp_path, manifest_for())
    assert not verification.ok
    assert verification.bytes_verified is False
    assert any("missing" in error for error in verification.errors)


def test_a_size_mismatch_fails(tmp_path: Path):
    target = tmp_path / "artifacts/data/result.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"abc")
    manifest = manifest_for(artifact_sha256=content_hash(target), artifact_size_bytes=999)
    verification = verify_artifact(tmp_path, manifest)
    assert not verification.ok
    assert any("size changed" in error for error in verification.errors)


def test_an_external_artifact_is_unverifiable_not_verified(tmp_path: Path):
    """The distinction between well-formed and verified must survive into the report."""
    manifest = manifest_for(path=None, storage_kind="external", artifact_uri="s3://bucket/key")
    verification = verify_artifact(tmp_path, manifest)
    assert verification.ok, "the manifest is well-formed"
    assert verification.bytes_verified is False
    assert verification.verified is False, "unverifiable must never present as verified"
    assert verification.unverifiable_reason
    assert verification.warnings


def test_a_remote_artifact_is_unverifiable_not_verified(tmp_path: Path):
    manifest = manifest_for(path=None, storage_kind="remote", artifact_uri="https://x/y")
    verification = verify_artifact(tmp_path, manifest)
    assert verification.verified is False
    assert verification.unverifiable_reason


def test_an_invalid_manifest_is_not_reported_as_unverifiable(tmp_path: Path):
    """A malformed manifest is a failure, not a storage limitation."""
    manifest = manifest_for(artifact_sha256="nope")
    verification = verify_artifact(tmp_path, manifest)
    assert not verification.ok
    assert verification.unverifiable_reason is None


def test_verification_to_dict_distinguishes_ok_from_verified(tmp_path: Path):
    manifest = manifest_for(path=None, storage_kind="external", artifact_uri="s3://b/k")
    payload = verify_artifact(tmp_path, manifest).to_dict()
    assert payload["ok"] is True
    assert payload["verified"] is False
    assert payload["bytes_verified"] is False


# ── Loading ─────────────────────────────────────────────────────────────────


def test_loading_a_missing_manifest_reports_absence(tmp_path: Path):
    manifest, errors = load_artifact_manifest(tmp_path, "artifacts/manifests/gone.json")
    assert manifest is None
    assert errors and "missing" in errors[0]


def test_loading_a_malformed_manifest_reports_corruption(tmp_path: Path):
    path = tmp_path / "artifacts/manifests/bad.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    manifest, errors = load_artifact_manifest(tmp_path, "artifacts/manifests/bad.json")
    assert manifest is None
    assert errors


def test_an_unknown_schema_version_is_rejected(tmp_path: Path):
    path = tmp_path / "artifacts/manifests/future.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"artifact_manifest_version": 99}), encoding="utf-8")
    manifest, errors = load_artifact_manifest(tmp_path, "artifacts/manifests/future.json")
    assert manifest is None
    assert any("99" in error for error in errors)


def test_a_valid_manifest_round_trips_through_disk(tmp_path: Path):
    target = tmp_path / "artifacts/data/result.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"payload")
    manifest = manifest_for(artifact_sha256=content_hash(target))
    path = tmp_path / "artifacts/manifests/example.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded, errors = load_artifact_manifest(tmp_path, "artifacts/manifests/example.json")
    assert errors == []
    assert loaded == manifest
    assert verify_artifact(tmp_path, loaded).verified is True
