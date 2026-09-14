"""Artifact manifests: what produced this, from what, and is it still those bytes.

Every canonical artifact answers the same set of questions without its producer present:

* what created me? (``created_by_experiment``, ``command``)
* from which code? (``code_revision``)
* from which frozen inputs? (``input_dataset_freezes``)
* from which model? (``model_id``, ``model_revision``, ``model_hashes``)
* under which configuration? (``configuration_hash``)
* in which environment? (``environment_manifest_hash``)
* what is my SHA-256? (``artifact_sha256``)
* when was I created? (``created_at``)

Identity: ids and hashes, never filenames
-----------------------------------------

Three distinct things, deliberately not merged:

``artifact_id``
    A stable, human-readable identifier. References between documents use this, so the
    reference survives the file being moved.
``artifact_sha256``
    The digest of the artifact's bytes. This is what makes the artifact itself
    identifiable, independently of where it lives.
``artifact_manifest_hash``
    The identity of this manifest's semantic content.

A filename is never an identity. Two artifacts with the same basename are different
artifacts; the same artifact at two paths is one artifact. Anything that keyed identity on
a path would get both of those backwards.

Where the bytes live matters
----------------------------

``storage_kind`` states whether the artifact's bytes are in this repository
(``repository``), stored externally (``external``), or fetched from a remote
(``remote``). Only ``repository`` artifacts can have their digest verified offline. The
other two are reported as *unverifiable* rather than as passing -- an artifact whose bytes
cannot be re-read has not been checked, and saying otherwise would be the exact failure
this repository is built to avoid.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engramfold.documents import load_document
from engramfold.hashing import content_hash, manifest_hash
from engramfold.schemas import current_version
from engramfold.validation.fields import (
    is_identifier,
    is_sha256,
    reject_unknown_fields,
    require_fields,
    require_int,
    require_nonempty_str,
    validate_relative_posix_path,
)

#: Where an artifact's bytes are, which decides how (and whether) they can be verified.
STORAGE_KINDS: tuple[str, ...] = ("repository", "external", "remote")

TOP_LEVEL_FIELDS: tuple[str, ...] = (
    # ── Document identity ──
    "artifact_manifest_version",
    # ── Artifact identity ──
    "artifact_id",
    "description",
    # ── Where the bytes are ──
    "storage_kind",
    "artifact_path",
    "artifact_uri",
    "artifact_sha256",
    "artifact_size_bytes",
    # ── What created it ──
    "created_by_experiment",
    "command",
    "code_revision",
    "configuration_hash",
    "environment_manifest_hash",
    # ── What it was made from ──
    "input_dataset_freezes",
    "model_id",
    "model_revision",
    "model_hashes",
    # ─ When ──
    "created_at",
    # ─ Free-form ──
    "notes",
)

REQUIRED_FIELDS: tuple[str, ...] = (
    "artifact_manifest_version",
    "artifact_id",
    "artifact_sha256",
    "storage_kind",
    "created_by_experiment",
    "command",
    "code_revision",
    "environment_manifest_hash",
    "input_dataset_freezes",
    "created_at",
)

#: Fields of a code-revision provenance record that must be present.
CODE_REVISION_FIELDS: tuple[str, ...] = (
    "provenance_record_version",
    "git_commit",
    "git_branch",
    "git_dirty",
)


def artifact_manifest_version() -> int:
    """The manifest-format version this build writes."""
    return current_version("artifact_manifest")


def artifact_identity(manifest: Mapping[str, Any]) -> str:
    """The semantic identity of an artifact manifest.

    Distinct from ``artifact_sha256``: this identifies the *description*, that identifies
    the *bytes*. Two manifests can describe the same bytes differently, and one manifest
    can describe bytes that later change.
    """
    return manifest_hash(manifest)


def validate_artifact_manifest(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """Every reason ``manifest`` is not a usable artifact manifest."""
    errors: list[str] = []
    errors.extend(reject_unknown_fields(manifest, TOP_LEVEL_FIELDS, origin=origin))
    errors.extend(require_fields(manifest, REQUIRED_FIELDS, origin=origin))
    if any("required field" in error for error in errors):
        return errors

    if not is_identifier(manifest.get("artifact_id")):
        errors.append(
            f"{origin}: artifact_id={manifest.get('artifact_id')!r} must match [a-z0-9][a-z0-9._-]*"
        )
    if not is_identifier(manifest.get("created_by_experiment")):
        errors.append(
            f"{origin}: created_by_experiment={manifest.get('created_by_experiment')!r} is not a "
            f"valid experiment identifier"
        )
    if not is_sha256(manifest.get("artifact_sha256")):
        errors.append(
            f"{origin}: artifact_sha256 must be a sha256 digest, found "
            f"{manifest.get('artifact_sha256')!r}; an artifact without a content digest has no "
            f"identity beyond its filename"
        )
    if not is_sha256(manifest.get("environment_manifest_hash")):
        errors.append(
            f"{origin}: environment_manifest_hash must be a sha256 digest, found "
            f"{manifest.get('environment_manifest_hash')!r}"
        )
    errors.extend(require_nonempty_str(manifest, "command", origin=origin))
    errors.extend(require_nonempty_str(manifest, "description", origin=origin))
    errors.extend(require_int(manifest, "artifact_size_bytes", origin=origin, minimum=0))

    errors.extend(_validate_storage(manifest, origin=origin))
    errors.extend(_validate_code_revision(manifest, origin=origin))
    errors.extend(_validate_inputs(manifest, origin=origin))
    errors.extend(_validate_model(manifest, origin=origin))
    return errors


def _validate_storage(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """The storage kind decides which locator fields are required."""
    errors: list[str] = []
    kind = manifest.get("storage_kind")
    if kind not in STORAGE_KINDS:
        errors.append(f"{origin}: storage_kind={kind!r} must be one of {list(STORAGE_KINDS)}")
        return errors
    if kind == "repository":
        errors.extend(require_fields(manifest, ("artifact_path",), origin=origin))
        errors.extend(
            validate_relative_posix_path(
                manifest.get("artifact_path"), origin=origin, label="artifact_path"
            )
        )
    else:
        errors.extend(require_fields(manifest, ("artifact_uri",), origin=origin))
        errors.extend(require_nonempty_str(manifest, "artifact_uri", origin=origin))
    return errors


def _validate_code_revision(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """The code revision must be a full provenance record, not a bare commit.

    A bare commit cannot distinguish a clean checkout from one with uncommitted edits, so
    it cannot establish what code produced the artifact. A dirty tree here must carry its
    override reason, so the fact is visible in the manifest itself.
    """
    errors: list[str] = []
    code_revision = manifest.get("code_revision")
    if not isinstance(code_revision, dict):
        errors.append(
            f"{origin}: code_revision must be a provenance record mapping, found "
            f"{type(code_revision).__name__}"
        )
        return errors
    errors.extend(
        require_fields(code_revision, CODE_REVISION_FIELDS, origin=f"{origin}: code_revision")
    )
    dirty = code_revision.get("git_dirty")
    if dirty is True and not code_revision.get("dirty_override_reason"):
        errors.append(
            f"{origin}: code_revision records a dirty tree with no dirty_override_reason; an "
            f"artifact produced from uncommitted code must say so explicitly"
        )
    return errors


def _validate_inputs(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """Inputs must be pinned by freeze id, so the input bytes are fixed."""
    errors: list[str] = []
    freezes = manifest.get("input_dataset_freezes")
    if not isinstance(freezes, list):
        errors.append(
            f"{origin}: input_dataset_freezes must be a list of freeze ids, found "
            f"{type(freezes).__name__}"
        )
        return errors
    for index, value in enumerate(freezes):
        if not is_sha256(value):
            errors.append(
                f"{origin}: input_dataset_freezes[{index}]={value!r} is not a freeze id; an input "
                f"named by path pins nothing"
            )
    if len({str(v) for v in freezes}) != len(freezes):
        errors.append(f"{origin}: input_dataset_freezes contains duplicate freeze ids")
    if not freezes:
        errors.append(
            f"{origin}: input_dataset_freezes is empty; an artifact that consumed no recorded "
            f"input must say so with an explicit empty-input rationale rather than an empty list"
        )
    return errors


def _validate_model(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """A model-derived artifact must pin the model revision immutably."""
    errors: list[str] = []
    if "model_id" in manifest and "model_revision" not in manifest:
        errors.append(f"{origin}: model_id is recorded without model_revision")
    if "model_revision" in manifest:
        from engramfold.validation.fields import is_full_revision

        if not is_full_revision(manifest.get("model_revision")):
            errors.append(
                f"{origin}: model_revision={manifest.get('model_revision')!r} is not a full "
                f"40-character commit"
            )
    return errors


@dataclass
class ArtifactVerification:
    """The outcome of checking one artifact manifest against the repository."""

    artifact_id: str
    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    bytes_verified: bool = False
    unverifiable_reason: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def verified(self) -> bool:
        """True only when the bytes were actually re-read and matched.

        Kept separate from ``ok`` so that "the manifest is well-formed but the bytes could
        not be checked" is never reported as a verification.
        """
        return self.bytes_verified and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "path": self.path,
            "ok": self.ok,
            "verified": self.verified,
            "bytes_verified": self.bytes_verified,
            "unverifiable_reason": self.unverifiable_reason,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def verify_artifact(
    root: Path | str, manifest: Mapping[str, Any], *, path: str = ""
) -> ArtifactVerification:
    """Check an artifact manifest, and re-read the artifact's bytes when they are local.

    A non-``repository`` artifact is reported as unverifiable with the reason, and
    ``bytes_verified`` stays false. That is deliberately not an error -- the artifact may be
    perfectly fine -- but it is emphatically not a verification either, and the two are
    distinguished in the returned object.
    """
    base = Path(root)
    artifact_id = str(manifest.get("artifact_id", "<missing>"))
    result = ArtifactVerification(artifact_id=artifact_id, path=path or artifact_id)
    result.errors.extend(validate_artifact_manifest(manifest, origin=result.path))
    if result.errors:
        return result

    kind = manifest.get("storage_kind")
    if kind != "repository":
        result.unverifiable_reason = (
            f"artifact bytes are {kind} ({manifest.get('artifact_uri')!r}); their digest cannot "
            f"be re-derived from this repository, so this manifest is well-formed but unverified"
        )
        result.warnings.append(f"{result.path}: {result.unverifiable_reason}")
        return result

    artifact_path = str(manifest["artifact_path"])
    target = base / artifact_path
    if not target.is_file():
        result.errors.append(
            f"{result.path}: artifact bytes are missing at {artifact_path}, so artifact_sha256 "
            f"cannot be verified at all"
        )
        return result

    try:
        actual = content_hash(target)
    except OSError as exc:
        result.errors.append(f"{result.path}: artifact at {artifact_path} is unreadable: {exc}")
        return result

    recorded = str(manifest["artifact_sha256"])
    if actual != recorded:
        result.errors.append(
            f"{result.path}: artifact changed: manifest records {recorded[:12]}…, "
            f"{artifact_path} hashes to {actual[:12]}…"
        )
        return result

    size = manifest.get("artifact_size_bytes")
    actual_size = target.stat().st_size
    if isinstance(size, int) and size != actual_size:
        result.errors.append(
            f"{result.path}: artifact size changed: manifest records {size} bytes, "
            f"{artifact_path} is {actual_size}"
        )
        return result

    result.bytes_verified = True
    return result


def load_artifact_manifest(
    root: Path | str, relative: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """Load and validate one artifact manifest."""
    load = load_document(Path(root) / relative, kind="artifact_manifest")
    if load.is_error:
        return None, load.errors()
    if load.is_absent:
        return None, [f"{load.path}: artifact manifest is missing"]
    return load.require(), validate_artifact_manifest(load.require(), origin=load.path)


__all__ = [
    "CODE_REVISION_FIELDS",
    "REQUIRED_FIELDS",
    "STORAGE_KINDS",
    "TOP_LEVEL_FIELDS",
    "ArtifactVerification",
    "artifact_identity",
    "artifact_manifest_version",
    "load_artifact_manifest",
    "validate_artifact_manifest",
    "verify_artifact",
]
