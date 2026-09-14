"""Artifacts: metadata for canonical results, identified by id and digest, never by name.

An artifact manifest answers what produced a result and whether its bytes are still those
bytes. Artifacts are keyed by a stable id and a content digest; a filename is never an
identity.

See :mod:`engramfold.artifacts.manifest`.
"""

from __future__ import annotations

from engramfold.artifacts.manifest import (
    STORAGE_KINDS,
    ArtifactVerification,
    artifact_identity,
    artifact_manifest_version,
    load_artifact_manifest,
    validate_artifact_manifest,
    verify_artifact,
)

__all__ = [
    "STORAGE_KINDS",
    "ArtifactVerification",
    "artifact_identity",
    "artifact_manifest_version",
    "load_artifact_manifest",
    "validate_artifact_manifest",
    "verify_artifact",
]
