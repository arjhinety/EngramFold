"""Experiment manifests: the definition, its identity, and its immutable references.

EngramFold has no experiments yet. This module therefore defines the *format* and the
*validation*, and deliberately contains no Study 001 parameters, no hypothesis about
mixture-of-experts consolidation, and no defaults that would constitute a research
decision.

What the format is for
----------------------

An experiment record has to answer, after the fact and without its author present:

* what was run (``description``, ``configuration``);
* against which frozen inputs (``dataset_freezes``);
* from which model revision (``model_id``, ``model_revision``, ``model_hashes``);
* with which code (``code_revision``, which is a full provenance record, not a bare
  commit -- see ``engramfold.provenance.git_state``);
* in which environment (``environment_manifest``, ``hardware_manifest``);
* by which command (``command``);
* producing what (``artifacts``, ``artifact_hashes``).

The rule that makes definition drift detectable
-----------------------------------------------

``configuration_hash`` is recorded *and recomputed on load*. If the configuration block
is edited after the fact, the recomputation disagrees with the recorded value and
validation fails. This is the mechanism behind "has an experiment definition changed
since execution?" -- the recorded hash is not a note about the configuration, it is a
check on it.

Statuses and what each one obliges
----------------------------------

A manifest's obligations grow with its status. A ``DRAFT`` may be incomplete; once an
experiment claims to have run, the fields that make the run attributable become
required. The obligations are listed in one place (:data:`STATUS_OBLIGATIONS`) so that
"what does COMPLETED promise?" has a single answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from engramfold.documents import load_document
from engramfold.hashing import configuration_hash, manifest_hash
from engramfold.schemas import current_version
from engramfold.validation.fields import (
    check_parallel_lists,
    is_full_revision,
    is_identifier,
    is_sha256,
    reject_unknown_fields,
    require_fields,
    require_int,
    require_nonempty_str,
    validate_code_revision,
    validate_path_list,
)

#: Lifecycle states. Intentionally generic: EngramFold has not defined its studies, so
#: this vocabulary describes process, not research.
STATUSES: tuple[str, ...] = (
    "DRAFT",  # being written; obligations minimal
    "FROZEN",  # definition fixed, not yet executed
    "RUNNING",  # execution started
    "COMPLETED",  # execution finished and artifacts were recorded
    "FAILED",  # execution finished without a usable result
    "INVALID",  # preserved as evidence, but not a valid result
    "ARCHIVED",  # superseded or withdrawn
)

#: Statuses for which an experiment is claiming to have actually run.
EXECUTED_STATUSES: tuple[str, ...] = ("RUNNING", "COMPLETED", "FAILED", "INVALID")

#: Statuses that must account for a produced artifact.
ARTIFACT_STATUSES: tuple[str, ...] = ("COMPLETED",)

#: The fields a manifest must carry once its status is at least ``RUNNING``. Listed
#: together so the promise each status makes is legible in one place.
ATTRIBUTION_FIELDS: tuple[str, ...] = (
    "configuration",
    "configuration_hash",
    "seed",
    "code_revision",
    "environment_manifest",
    "command",
    "started_at",
)

STATUS_OBLIGATIONS: dict[str, tuple[str, ...]] = {
    status: (ATTRIBUTION_FIELDS if status in EXECUTED_STATUSES else ()) for status in STATUSES
}

TOP_LEVEL_FIELDS: tuple[str, ...] = (
    # ── Document identity ──
    "experiment_manifest_version",
    # ── Experiment identity ──
    "experiment_id",
    "study_id",
    "description",
    "hypothesis",
    "status",
    "parent_experiment",
    # ── Model ──
    "model_id",
    "model_revision",
    "model_hashes",
    # ── Inputs ──
    "dataset_freezes",
    # ── Definition and its identity ──
    "configuration",
    "configuration_hash",
    "seed",
    # ── Attribution ──
    "code_revision",
    "environment_manifest",
    "hardware_manifest",
    "command",
    # ── Timing ──
    "started_at",
    "completed_at",
    # ─ Outputs ──
    "artifacts",
    "artifact_hashes",
    # ─ Free-form ──
    "notes",
)

REQUIRED_FIELDS: tuple[str, ...] = (
    "experiment_manifest_version",
    "experiment_id",
    "description",
    "status",
    "dataset_freezes",
    "configuration",
    "configuration_hash",
)

#: Fields of a code-revision provenance record that must be present.
CODE_REVISION_FIELDS: tuple[str, ...] = (
    "provenance_record_version",
    "git_commit",
    "git_branch",
    "git_dirty",
)

REQUIRED_STAMP_FIELDS: tuple[str, ...] = ()


def experiment_manifest_version() -> int:
    """The manifest-format version this build writes."""
    return current_version("experiment_manifest")


def experiment_identity(manifest: Mapping[str, Any]) -> str:
    """The deterministic identity of an experiment manifest.

    Timestamps and machine-local fields are excluded, so an experiment's identity does not
    change merely because it was written down twice.
    """
    return manifest_hash(manifest)


def configuration_identity(configuration: Mapping[str, Any]) -> str:
    """The identity of a configuration block.

    Exposed as its own function because the recorded ``configuration_hash`` must be
    recomputed through exactly this path: if the validator and the writer computed a
    configuration's identity differently, the drift check would either always fail or
    never fail, and both are useless.
    """
    return configuration_hash(configuration)


def validate_experiment_manifest(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """Every reason ``manifest`` is not a usable experiment manifest."""
    errors: list[str] = []
    errors.extend(reject_unknown_fields(manifest, TOP_LEVEL_FIELDS, origin=origin))
    errors.extend(require_fields(manifest, REQUIRED_FIELDS, origin=origin))
    if any("required field" in error for error in errors):
        return errors

    experiment_id = manifest.get("experiment_id")
    if not is_identifier(experiment_id):
        errors.append(f"{origin}: experiment_id={experiment_id!r} must match [a-z0-9][a-z0-9._-]*")

    status = manifest.get("status")
    if status not in STATUSES:
        errors.append(f"{origin}: status={status!r} must be one of {list(STATUSES)}")
        return errors

    errors.extend(require_nonempty_str(manifest, "description", origin=origin))
    errors.extend(require_nonempty_str(manifest, "study_id", origin=origin))
    errors.extend(require_nonempty_str(manifest, "notes", origin=origin))

    errors.extend(_validate_configuration(manifest, origin=origin))
    errors.extend(_validate_inputs(manifest, origin=origin))
    errors.extend(_validate_model(manifest, origin=origin, status=str(status)))
    errors.extend(_validate_attribution(manifest, origin=origin, status=str(status)))
    errors.extend(_validate_outputs(manifest, origin=origin, status=str(status)))
    errors.extend(_validate_lineage(manifest, origin=origin))
    return errors


def _validate_configuration(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """The configuration must be a mapping whose recorded hash is recomputable.

    This is the definition-drift check. A recorded hash that does not match the block it
    claims to identify means the definition was edited after it was recorded -- which is
    exactly the question the manifest exists to answer.
    """
    errors: list[str] = []
    configuration = manifest.get("configuration")
    if not isinstance(configuration, dict):
        errors.append(
            f"{origin}: configuration must be a mapping, found {type(configuration).__name__}; "
            f"a configuration that is not structured cannot be hashed or diffed"
        )
        return errors
    recorded = manifest.get("configuration_hash")
    if not is_sha256(recorded):
        errors.append(f"{origin}: configuration_hash must be a sha256 digest, found {recorded!r}")
        return errors
    recomputed = configuration_identity(configuration)
    if recomputed != recorded:
        errors.append(
            f"{origin}: configuration_hash does not match the configuration block: recorded "
            f"{str(recorded)[:12]}…, recomputed {recomputed[:12]}…; the definition changed after "
            f"this hash was written, so the experiment is not the experiment it claims to be"
        )
    return errors


def _validate_inputs(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """Dataset freezes must be named by freeze id, not by filename or dataset name.

    A freeze id is the digest of the frozen content. Referencing an input by a path would
    pin nothing, because a path can hold different bytes tomorrow -- the same failure mode
    that made provenance claims unverifiable before it was addressed.
    """
    errors: list[str] = []
    freezes = manifest.get("dataset_freezes")
    if not isinstance(freezes, list):
        errors.append(
            f"{origin}: dataset_freezes must be a list of freeze ids, "
            f"found {type(freezes).__name__}"
        )
        return errors
    for index, value in enumerate(freezes):
        if not is_sha256(value):
            errors.append(
                f"{origin}: dataset_freezes[{index}]={value!r} is not a freeze id; inputs must be "
                f"pinned by the digest of the frozen content, not by a path or a dataset name"
            )
    if len({str(v) for v in freezes}) != len(freezes):
        errors.append(f"{origin}: dataset_freezes contains duplicate freeze ids")
    return errors


def _validate_model(manifest: Mapping[str, Any], *, origin: str, status: str) -> list[str]:
    """A model-backed experiment must pin an immutable model revision and its hashes."""
    errors: list[str] = []
    errors.extend(require_nonempty_str(manifest, "model_id", origin=origin))
    if "model_id" in manifest or status in EXECUTED_STATUSES:
        errors.extend(require_fields(manifest, ("model_id", "model_revision"), origin=origin))
    if "model_revision" in manifest:
        revision = manifest.get("model_revision")
        if not is_full_revision(revision):
            errors.append(
                f"{origin}: model_revision={revision!r} is not a full 40-character commit; a "
                f"branch or tag resolves to different weights over time and pins no model"
            )
    if "model_hashes" in manifest:
        hashes = manifest.get("model_hashes")
        if not isinstance(hashes, dict) or not hashes:
            errors.append(
                f"{origin}: model_hashes must be a non-empty mapping of weight file to sha256"
            )
        else:
            for key, value in hashes.items():
                if not is_sha256(value):
                    errors.append(
                        f"{origin}: model_hashes[{key!r}] must be a sha256 digest, found {value!r}"
                    )
    return errors


def _validate_attribution(manifest: Mapping[str, Any], *, origin: str, status: str) -> list[str]:
    """Everything needed to attribute a result to the code and environment that made it.

    Required as soon as the manifest claims the experiment ran. A record that says
    ``COMPLETED`` while being unable to name its commit or its environment is not evidence.
    """
    errors: list[str] = []
    if status not in EXECUTED_STATUSES:
        return errors

    errors.extend(require_fields(manifest, ATTRIBUTION_FIELDS, origin=origin))

    code_revision = manifest.get("code_revision")
    if isinstance(code_revision, dict):
        # Only the *shape* of the record is checked here; the record's own semantics -- including
        # the tri-state of git_dirty -- are decided once, in the shared validator, so this
        # manifest and the artifact manifest cannot drift apart on what a dirty tree means.
        errors.extend(
            reject_unknown_fields(
                code_revision,
                (
                    *CODE_REVISION_FIELDS,
                    *(
                        "dirty_file_list",
                        "dirty_diff_hash",
                        "captured_at",
                        "last_commit_affecting_relevant_file",
                        "last_commit_timestamp",
                        "relevant_paths",
                        "dirty_override_reason",
                        "untracked_file_list",
                        "git_available",
                        "head_committed_at",
                        "error",
                    ),
                ),
                origin=f"{origin}: code_revision",
            )
        )
    errors.extend(validate_code_revision(code_revision, origin=origin))

    environment = manifest.get("environment_manifest")
    if not isinstance(environment, dict) or not environment:
        errors.append(
            f"{origin}: environment_manifest must be a non-empty mapping; an artifact that cannot "
            f"say what produced it is not reproducible evidence"
        )
    if "hardware_manifest" in manifest and not isinstance(manifest.get("hardware_manifest"), dict):
        errors.append(f"{origin}: hardware_manifest must be a mapping when present")

    errors.extend(require_int(manifest, "seed", origin=origin, minimum=0))
    errors.extend(require_nonempty_str(manifest, "command", origin=origin))

    if status in ("COMPLETED", "FAILED", "INVALID"):
        errors.extend(require_fields(manifest, ("completed_at",), origin=origin))
    return errors


def _validate_outputs(manifest: Mapping[str, Any], *, origin: str, status: str) -> list[str]:
    """Artifacts must be listed, hashed, and paired.

    A ``COMPLETED`` experiment with no recorded artifacts is a claim without a result, so
    an empty artifact list is an error for that status rather than a quiet zero.
    """
    errors: list[str] = []
    if "artifacts" in manifest:
        errors.extend(validate_path_list(manifest["artifacts"], origin=origin, label="artifacts"))
    hashes = manifest.get("artifact_hashes")
    if "artifact_hashes" in manifest:
        if not isinstance(hashes, dict):
            errors.append(
                f"{origin}: artifact_hashes must be a mapping of artifact id to sha256, found "
                f"{type(hashes).__name__}"
            )
        else:
            for key, value in hashes.items():
                if not is_sha256(value):
                    errors.append(
                        f"{origin}: artifact_hashes[{key!r}] must be a sha256 digest, "
                        f"found {value!r}"
                    )
    if "artifacts" in manifest and "artifact_hashes" in manifest:
        errors.extend(
            check_parallel_lists(
                manifest["artifacts"],
                hashes,
                origin=origin,
                paths_label="artifacts",
                hashes_label="artifact_hashes",
            )
        )
    if status in ARTIFACT_STATUSES:
        artifacts = manifest.get("artifacts")
        if not artifacts:
            errors.append(
                f"{origin}: status={status} but no artifacts are recorded; an experiment that "
                f"claims to have completed must name the result it produced"
            )
        if not hashes:
            errors.append(
                f"{origin}: status={status} but artifact_hashes is empty, so the produced result "
                f"has no identity"
            )
    return errors


def _validate_lineage(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """A parent must be a different experiment."""
    errors: list[str] = []
    parent = manifest.get("parent_experiment")
    if parent is None:
        return errors
    if not is_identifier(parent):
        errors.append(
            f"{origin}: parent_experiment={parent!r} is not a valid experiment identifier"
        )
    if parent == manifest.get("experiment_id"):
        errors.append(
            f"{origin}: parent_experiment names this experiment, so the lineage is a cycle"
        )
    return errors


def load_experiment_manifest(
    root: Path | str, relative: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """Load and validate one experiment manifest, distinguishing absence from corruption."""
    load = load_document(Path(root) / relative, kind="experiment_manifest")
    if load.is_error:
        return None, load.errors()
    if load.is_absent:
        return None, [f"{load.path}: experiment manifest is missing"]
    manifest = load.require()
    return manifest, validate_experiment_manifest(manifest, origin=load.path)


__all__ = [
    "ARTIFACT_STATUSES",
    "ATTRIBUTION_FIELDS",
    "CODE_REVISION_FIELDS",
    "EXECUTED_STATUSES",
    "REQUIRED_FIELDS",
    "STATUSES",
    "STATUS_OBLIGATIONS",
    "TOP_LEVEL_FIELDS",
    "configuration_identity",
    "experiment_identity",
    "experiment_manifest_version",
    "load_experiment_manifest",
    "validate_experiment_manifest",
]
