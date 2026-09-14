"""Determinism: the same content must produce the same identity, twice.

This module is the executable form of the table in ``docs/REPRODUCIBILITY.md``. Every row of
that table names a property and names this module as its test, so a row without a test here is
a claim the documentation makes and the repository does not check.

The rule being tested is narrow and worth stating exactly:

    Two computations over the same content produce the same identity. Facts that change for
    reasons unrelated to the content — timestamps, hostnames, absolute paths — are recorded but
    excluded from identity.

Determinism here is a property of *identity*, not of timing. Nothing in this module measures
how long anything takes, and no test compares two timestamps.

Each test runs the operation twice and compares bytes or digests. Where a property could pass
trivially — two empty registries are equal in any order — the fixture is populated first, so the
comparison has something to compare.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import registry_repo
from tests.test_artifacts import manifest_for as artifact_manifest_for
from tests.test_dataset_freeze import (
    CREATED_AT,
    MANIFEST_PATH,
    make_dataset,
)
from tests.test_experiments import draft_manifest

from engramfold.artifacts.manifest import artifact_identity
from engramfold.datasets.freeze import (
    build_freeze,
    canonical_freeze_text,
    freeze_identity,
    write_freeze,
)
from engramfold.datasets.manifest import dataset_identity
from engramfold.experiments.freeze import build_experiment_freeze
from engramfold.experiments.manifest import (
    configuration_identity,
    experiment_identity,
)
from engramfold.hashing import (
    canonical_json_hash,
    canonical_json_text,
    directory_hash,
    manifest_hash,
)
from engramfold.provenance.environment import capture_environment
from engramfold.registry.store import load_registry
from engramfold.schemas import check_schema_version

CAPTURED_EARLY = "2026-01-01T00:00:00+00:00"
CAPTURED_LATE = "2026-12-31T23:59:59+00:00"


# ── Hashing and serialisation ───────────────────────────────────────────────


def test_json_key_order_does_not_change_an_identity():
    """Serialisation order must not leak into identity."""
    first = {"b": 2, "a": 1, "nested": {"y": [1, 2], "x": "z"}}
    reordered = {"nested": {"x": "z", "y": [1, 2]}, "a": 1, "b": 2}
    assert canonical_json_hash(first) == canonical_json_hash(reordered)
    assert manifest_hash(first) == manifest_hash(reordered)
    assert canonical_json_text(first) == canonical_json_text(reordered)


def test_a_manifest_serialises_to_identical_bytes_twice():
    """Two serialisations of the same document are byte-identical, so they can be diffed."""
    manifest = draft_manifest()
    assert canonical_json_text(manifest) == canonical_json_text(dict(reversed(manifest.items())))
    assert canonical_json_text(manifest).encode() == canonical_json_text(manifest).encode()
    assert json.loads(canonical_json_text(manifest)) == manifest


def test_directory_hashing_does_not_depend_on_where_the_tree_is(tmp_path: Path):
    """Absolute location is never part of a digest, so two copies agree."""
    first = tmp_path / "one"
    second = tmp_path / "deeper" / "two"
    for base in (first, second):
        base.mkdir(parents=True)
        (base / "a.txt").write_text("a\n", encoding="utf-8")
        (base / "b.txt").write_text("b\n", encoding="utf-8")
    assert directory_hash(first).digest == directory_hash(second).digest
    assert directory_hash(first).digest == directory_hash(first).digest
    assert directory_hash(first).entries == directory_hash(first).entries


# ── The same dataset, described twice ───────────────────────────────────────


def write_dataset_manifest(root: Path, manifest: dict[str, object], *, indent: int = 2) -> None:
    target = root / MANIFEST_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=indent, sort_keys=True) + "\n", encoding="utf-8")


def freeze_result(root: Path, manifest: dict[str, object]):
    """A freeze built with everything fixed, so two builds are comparable."""
    return build_freeze(
        root,
        manifest,
        MANIFEST_PATH,
        created_at=CREATED_AT,
        repository_revision="0" * 40,
        repository_dirty=False,
        frozen_by_command="pytest",
    )


def test_reformatting_a_dataset_manifest_does_not_change_its_identity(tmp_path: Path):
    """Identity is a property of the content, not of how it was indented."""
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    write_dataset_manifest(root, manifest, indent=2)
    indented = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    write_dataset_manifest(root, dict(reversed(list(manifest.items()))), indent=4)
    compacted = json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))
    assert (root / MANIFEST_PATH).read_bytes() != json.dumps(
        manifest, indent=2, sort_keys=True
    ).encode(), "the two writings produced the same bytes, so this proves nothing"
    assert dataset_identity(indented) == dataset_identity(compacted)


def test_the_same_dataset_described_twice_has_one_identity(tmp_path: Path):
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    reordered = dict(reversed(list(manifest.items())))
    reordered["created_at"] = "2099-01-01T00:00:00+00:00"
    assert dataset_identity(manifest) == dataset_identity(reordered), (
        "a timestamp or a key order changed a dataset's identity, so every regeneration of "
        "identical data would be a different dataset"
    )
    assert dataset_identity(manifest) != dataset_identity(dict(manifest, record_count=2))
    assert dataset_identity(manifest) != dataset_identity(dict(manifest, dataset_version="v2"))


# ── Regenerating a freeze ──────────────────────────────────────────────────


def test_regenerating_a_freeze_is_idempotent_and_byte_identical(tmp_path: Path):
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    write_dataset_manifest(root, manifest)

    first = freeze_result(root, manifest)
    second = freeze_result(root, manifest)
    assert first.errors == [] and second.errors == []
    assert canonical_freeze_text(first.freeze) == canonical_freeze_text(second.freeze)
    assert first.freeze["freeze_id"] == second.freeze["freeze_id"]
    assert freeze_identity(first.freeze) == freeze_identity(second.freeze)

    written = write_freeze(root, first)
    assert written.written, written
    target = root / written.output_path
    original = target.read_bytes()
    again = write_freeze(root, second)
    assert target.read_bytes() == original, "writing the same freeze twice changed the file"
    assert again.unchanged, again
    assert again.byte_identical, again


def test_a_freeze_taken_twice_has_one_identity_and_two_provenance_records(tmp_path: Path):
    """The freeze identity is stable; the record of when it was taken is not."""
    root = tmp_path / "repo"
    manifest = make_dataset(root)
    write_dataset_manifest(root, manifest)
    first = freeze_result(root, manifest)
    later = build_freeze(
        root,
        manifest,
        MANIFEST_PATH,
        created_at=CAPTURED_LATE,
        repository_revision="1" * 40,
        repository_dirty=True,
        frozen_by_command="pytest",
    )
    assert freeze_identity(first.freeze) == freeze_identity(later.freeze)
    assert first.freeze["created_at"] != later.freeze["created_at"]
    assert canonical_freeze_text(first.freeze) != canonical_freeze_text(later.freeze)


# ── Registry order ──────────────────────────────────────────────────────────


def test_registry_ordering_is_stable_and_follows_the_file(tmp_path: Path):
    """Two loads must agree, and agree with the order the records were written in."""
    root = registry_repo(
        tmp_path / "r",
        datasets=[
            "  - id: second\n    manifest_path: datasets/manifests/b.json\n",
            "  - id: first\n    manifest_path: datasets/manifests/a.json\n",
        ],
    )
    first_load = load_registry(root)
    second_load = load_registry(root)
    ids = [record["id"] for record in first_load.records("datasets")]
    assert ids == ["second", "first"], ids
    assert ids == [record["id"] for record in second_load.records("datasets")]
    assert first_load.ids("datasets") == second_load.ids("datasets") == {"second", "first"}
    assert first_load.total_records() == second_load.total_records() == 2


# ── The same experiment, described twice ───────────────────────────────────


def test_the_same_experiment_described_twice_has_one_identity():
    manifest = draft_manifest()
    reordered = dict(reversed(list(manifest.items())))
    assert experiment_identity(manifest) == experiment_identity(reordered)
    assert experiment_identity(manifest) != experiment_identity(
        draft_manifest(experiment_id="another-experiment")
    )


def test_the_configuration_is_identified_separately_and_hashes_stably():
    """The configuration identity is its own identity, not a view of the manifest's."""
    configuration = {"scope": "infrastructure-only", "placeholder": True}
    reordered = {"placeholder": True, "scope": "infrastructure-only"}
    assert configuration_identity(configuration) == configuration_identity(reordered)
    assert configuration_identity(configuration) != configuration_identity(
        {"scope": "infrastructure-only", "placeholder": False}
    )
    assert configuration_identity(configuration) == configuration_identity(dict(configuration))


def test_rebuilding_an_experiment_freeze_gives_the_same_identity_and_bytes(tmp_path: Path):
    """A definition freeze is a function of the definition, not of when it was taken."""
    manifest = draft_manifest()
    first = build_experiment_freeze(
        manifest,
        "experiments/manifests/example.json",
        frozen_at=CREATED_AT,
        repository_revision="0" * 40,
        repository_dirty=False,
        frozen_by_command="pytest",
    )
    second = build_experiment_freeze(
        manifest,
        "experiments/manifests/example.json",
        frozen_at=CAPTURED_LATE,
        repository_revision="1" * 40,
        repository_dirty=True,
        frozen_by_command="pytest",
    )
    assert first.errors == [] and second.errors == []
    assert first.freeze["definition_hash"] == second.freeze["definition_hash"]
    assert first.freeze["configuration_hash"] == second.freeze["configuration_hash"]
    assert first.freeze["freeze_id"] == second.freeze["freeze_id"]
    assert first.freeze["frozen_at"] != second.freeze["frozen_at"]


# ── Environment capture ────────────────────────────────────────────────────


def test_environment_capture_is_stable_across_two_captures():
    """Hardware probes are skipped: they describe the host, not the environment identity."""
    first = capture_environment(captured_at=CAPTURED_EARLY, probe_hardware=False)
    second = capture_environment(captured_at=CAPTURED_LATE, probe_hardware=False)
    assert first.manifest_hash == second.manifest_hash
    assert first.identity_payload() == second.identity_payload()
    assert first.captured_at != second.captured_at
    assert first.manifest_hash == manifest_hash(first.identity_payload())


def test_an_environment_identity_is_not_self_referential():
    """The recorded hash must be recomputable from the recorded document.

    Computing it over a payload that included the hash itself, or the host and the timestamp,
    made a manifest's recorded identity something other than the identity of what it recorded:
    no reader could recompute it, and two captures of the same environment disagreed.
    """
    manifest = capture_environment(captured_at=CAPTURED_EARLY, probe_hardware=False)
    payload = manifest.identity_payload()
    assert "environment_manifest_hash" not in payload, "the identity hashes itself"
    for volatile in ("captured_at", "hostname", "hostname_class"):
        assert volatile not in payload, volatile
    assert payload["environment_manifest_version"] == 1
    recorded = manifest.to_dict()
    assert recorded["environment_manifest_hash"] == manifest_hash(payload)
    assert check_schema_version("environment_manifest", recorded) is None


# ── Artifact metadata ──────────────────────────────────────────────────────


def test_artifact_metadata_is_byte_stable_and_order_independent():
    manifest = artifact_manifest_for()
    reordered = dict(reversed(list(manifest.items())))
    assert canonical_json_text(manifest) == canonical_json_text(reordered)
    assert artifact_identity(manifest) == artifact_identity(reordered)
    assert artifact_identity(manifest) != artifact_identity(
        artifact_manifest_for(artifact_sha256="d" * 64)
    )
    assert artifact_identity(manifest) != artifact_identity(
        artifact_manifest_for(artifact_id="another-artifact")
    )
    assert artifact_identity(manifest) == artifact_identity(artifact_manifest_for())
