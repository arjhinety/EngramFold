"""Datasets: manifests that say where data came from, and freezes that pin its bytes.

A manifest describes; a freeze commits. The distinction matters because a validation gate
that only checks manifests can pass while the bytes they describe have moved.

See :mod:`engramfold.datasets.manifest` for the schema and
:mod:`engramfold.datasets.freeze` for what a freeze pins and what it deliberately does
not.
"""

from __future__ import annotations

from engramfold.datasets.freeze import (
    ARTIFACT_ROLES,
    FREEZES_DIRECTORY,
    FreezeResult,
    FreezeVerification,
    build_freeze,
    freeze_format_version,
    freeze_identity,
    freeze_relative_path,
    freeze_summary,
    load_freeze,
    validate_freeze_document,
    verify_freeze,
    write_freeze,
)
from engramfold.datasets.manifest import (
    SOURCE_TYPES,
    dataset_identity,
    dataset_manifest_version,
    load_dataset_manifest,
    validate_dataset_manifest,
)

__all__ = [
    "ARTIFACT_ROLES",
    "FREEZES_DIRECTORY",
    "SOURCE_TYPES",
    "FreezeResult",
    "FreezeVerification",
    "build_freeze",
    "dataset_identity",
    "dataset_manifest_version",
    "freeze_format_version",
    "freeze_identity",
    "freeze_relative_path",
    "freeze_summary",
    "load_dataset_manifest",
    "load_freeze",
    "validate_dataset_manifest",
    "validate_freeze_document",
    "verify_freeze",
    "write_freeze",
]
