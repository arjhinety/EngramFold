"""Experiments: manifests that define a run, and freezes that detect definition drift.

EngramFold has not defined any experiments. These modules define the format, the
obligations each lifecycle status carries, and the mechanism that answers "has this
definition changed since it was frozen?".

See :mod:`engramfold.experiments.manifest` and :mod:`engramfold.experiments.freeze`.
"""

from __future__ import annotations

from engramfold.experiments.freeze import (
    DefinitionDrift,
    ExperimentFreezeResult,
    build_experiment_freeze,
    experiment_freeze_identity,
    load_experiment_freeze,
    validate_experiment_freeze_document,
    verify_experiment_freeze,
    write_experiment_freeze,
)
from engramfold.experiments.manifest import (
    EXECUTED_STATUSES,
    STATUS_OBLIGATIONS,
    STATUSES,
    configuration_identity,
    experiment_identity,
    experiment_manifest_version,
    load_experiment_manifest,
    validate_experiment_manifest,
)

__all__ = [
    "EXECUTED_STATUSES",
    "STATUSES",
    "STATUS_OBLIGATIONS",
    "DefinitionDrift",
    "ExperimentFreezeResult",
    "build_experiment_freeze",
    "configuration_identity",
    "experiment_freeze_identity",
    "experiment_identity",
    "experiment_manifest_version",
    "load_experiment_freeze",
    "load_experiment_manifest",
    "validate_experiment_freeze_document",
    "validate_experiment_manifest",
    "verify_experiment_freeze",
    "write_experiment_freeze",
]
