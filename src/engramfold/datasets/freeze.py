"""Dataset freezes: this exact research stage consumed these exact bytes under this exact manifest.

A freeze is the point at which a dataset stops being a description and becomes a
commitment. It says: the work downstream of here read these files, whose digests are
these, described by this manifest, whose identity is this. Everything that answers "has a
frozen input changed?" is answered from this file.

What a freeze pins, and what it deliberately does not
----------------------------------------------------

Pinned (and therefore part of ``freeze_id``):

* the freeze format version, so the file's own meaning is versioned;
* the dataset id and version;
* the manifest's path *and* its content hash -- so both "which document" and "which
  revision of it" are fixed;
* the record schema version and the record counts;
* every artifact: its repository-relative path, its SHA-256, and its size;
* the role of those artifacts, because "these are the processed shards" and "these are
  the raw inputs" are different claims about the same file list.

Not pinned (recorded, but outside ``freeze_id``):

* ``created_at`` -- when the freeze was taken. Two freezes of identical content are the
  same freeze, whenever they happened;
* ``repository_revision`` and ``repository_dirty`` -- which commit was checked out. That
  is provenance for the *freeze event*, not a property of the data;
* ``frozen_by_command`` -- the command that wrote it.

Separating these is what lets a freeze be regenerated reproducibly. If the timestamp
participated in the identity, every regeneration would produce a new freeze of the same
data, and "has this changed?" would be unanswerable.

Idempotence and frozen history
------------------------------

:func:`write_freeze` will not overwrite a freeze whose identity differs. Regenerating an
existing freeze from unchanged inputs is a no-op that leaves the file byte-for-byte
alone; regenerating one whose identity has moved is a refusal, because silently
replacing a freeze is precisely the act the frozen-history policy forbids. A genuine
change means a *new* freeze id, and therefore a new file alongside the old one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engramfold.datasets.manifest import dataset_identity
from engramfold.documents import load_document
from engramfold.errors import IntegrityError
from engramfold.hashing import (
    content_hash,
    pretty_json_text,
    selective_identity,
)
from engramfold.schemas import current_version
from engramfold.validation.fields import (
    is_sha256,
    reject_unknown_fields,
    require_fields,
    require_int,
    validate_relative_posix_path,
)

#: The fields that constitute a freeze's identity. Everything else is provenance for the
#: freeze event and is excluded from ``freeze_id`` -- see the module docstring.
IDENTITY_FIELDS: tuple[str, ...] = (
    "freeze_format_version",
    "dataset_id",
    "dataset_version",
    "manifest_path",
    "manifest_hash",
    "record_schema_version",
    "record_counts",
    "artifact_role",
    "artifacts",
)

#: Recorded for the freeze event, excluded from its identity.
VOLATILE_FIELDS: tuple[str, ...] = (
    "freeze_id",
    "created_at",
    "repository_revision",
    "repository_dirty",
    "frozen_by_command",
    "generated_by",
)

TOP_LEVEL_FIELDS: tuple[str, ...] = IDENTITY_FIELDS + VOLATILE_FIELDS

REQUIRED_FIELDS: tuple[str, ...] = (*IDENTITY_FIELDS, "created_at", "generated_by")

#: Why a freeze must declare itself generated: a freeze is written by a tool, never by
#: hand. Without the marker it is indistinguishable from hand-authored canonical source,
#: which invites an edit that the next regeneration would silently destroy.
GENERATED_BY_DEFAULT = "engramfold.datasets.freeze.build_freeze"

#: Whether the frozen files are the processed outputs or the raw inputs.
ARTIFACT_ROLES: tuple[str, ...] = ("processed", "source", "mixed")

FREEZES_DIRECTORY = "datasets/freezes"
MANIFESTS_DIRECTORY = "datasets/manifests"


def freeze_format_version() -> int:
    """The freeze-format version this build writes."""
    return current_version("dataset_freeze")


@dataclass
class FreezeResult:
    """What building a freeze did, so a caller can report it rather than assume it."""

    freeze: dict[str, Any]
    output_path: str
    written: bool
    unchanged: bool
    artifacts_verified: int
    #: Whether the bytes on disk already equal the bytes this build would write. Only
    #: meaningful when ``unchanged`` is true; it is what the idempotence test asserts on,
    #: because "the freeze was not rewritten" and "the freeze would have been identical
    #: anyway" are different guarantees.
    byte_identical: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def freeze_identity(payload: Mapping[str, Any]) -> str:
    """The deterministic identity of a freeze, timestamps and release provenance excluded."""
    return selective_identity(payload, IDENTITY_FIELDS)


def artifact_entries(
    root: Path | str,
    manifest: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """The artifacts a manifest declares, with their verified digests.

    Returns ``(entries, role, errors)``. Prefers ``processed_files`` when present,
    because a freeze over a processed dataset is a freeze over what the pipeline emitted;
    falls back to ``source_files`` for a dataset that is consumed as-is.

    Each entry's digest is taken from the manifest *and* checked against the bytes on
    disk. Recording the manifest's claim without checking it would make the freeze a
    copy of an assertion rather than a verification of one.
    """
    base = Path(root)
    processed = manifest.get("processed_files")
    source = manifest.get("source_files")

    if isinstance(processed, list) and processed:
        declared = manifest.get("processed_file_hashes") or {}
        role = "processed"
        paths = [str(item) for item in processed]
    elif isinstance(source, list) and source:
        declared = manifest.get("source_file_hashes") or {}
        role = "source"
        paths = [str(item) for item in source]
    else:
        return (
            [],
            "processed",
            [
                "manifest declares neither processed_files nor source_files, so it pins no "
                "bytes and a freeze over it would commit to nothing"
            ],
        )

    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted(paths):
        target = base / path
        if not target.is_file():
            errors.append(
                f"artifact {path} is declared by the manifest but is not present in the "
                f"repository, so its digest cannot be verified"
            )
            continue
        actual = content_hash(target)
        recorded = declared.get(path) if isinstance(declared, Mapping) else None
        if is_sha256(recorded) and recorded != actual:
            errors.append(
                f"artifact {path} hashes to {actual[:12]}… but the manifest records "
                f"{str(recorded)[:12]}…; the manifest and the bytes disagree"
            )
            continue
        entries.append(
            {
                "path": path,
                "sha256": actual,
                "size_bytes": target.stat().st_size,
            }
        )
    return entries, role, errors


def build_freeze(
    root: Path | str,
    manifest: Mapping[str, Any],
    manifest_path: str,
    *,
    created_at: str,
    repository_revision: str | None = None,
    repository_dirty: bool | None = None,
    frozen_by_command: str | None = None,
) -> FreezeResult:
    """Construct a freeze document for ``manifest``.

    ``created_at`` is required rather than defaulted to "now": a freeze whose timestamp
    the caller cannot control is a freeze that cannot be regenerated byte-for-byte, and
    the determinism tests depend on that being possible.
    """
    base = Path(root)
    errors: list[str] = []
    errors.extend(
        validate_relative_posix_path(manifest_path, origin="freeze", label="manifest_path")
    )

    dataset_id = manifest.get("dataset_id")
    if not isinstance(dataset_id, str) or not dataset_id.strip():
        errors.append("freeze: manifest has no usable dataset_id")

    entries, role, artifact_errors = artifact_entries(base, manifest)
    errors.extend(artifact_errors)
    if not entries and not artifact_errors:
        errors.append("freeze: no artifacts to pin, so the freeze would commit to nothing")

    record_count = manifest.get("record_count")
    record_counts = {
        "total": record_count
        if isinstance(record_count, int) and not isinstance(record_count, bool)
        else 0
    }

    payload: dict[str, Any] = {
        "freeze_format_version": freeze_format_version(),
        "dataset_id": dataset_id,
        "dataset_version": manifest.get("dataset_version"),
        "manifest_path": manifest_path,
        # The manifest's semantic identity, not the digest of its file bytes, and taken
        # through the same `dataset_identity` the manifest module exposes so that the two
        # can never disagree about what a manifest's identity is. Reordering or
        # reformatting the manifest must not be a change to the frozen data.
        "manifest_hash": dataset_identity(manifest),
        "record_schema_version": manifest.get("schema_version"),
        "record_counts": record_counts,
        "artifact_role": role,
        "artifacts": entries,
        "created_at": created_at,
        "repository_revision": repository_revision,
        "repository_dirty": repository_dirty,
        "frozen_by_command": frozen_by_command,
        "generated_by": GENERATED_BY_DEFAULT,
    }
    payload["freeze_id"] = freeze_identity(payload)
    return FreezeResult(
        freeze=payload,
        output_path=freeze_relative_path(str(payload["freeze_id"])),
        written=False,
        unchanged=False,
        artifacts_verified=len(entries),
        errors=errors,
    )


def freeze_relative_path(freeze_id: str) -> str:
    """Where a freeze with this id lives."""
    return f"{FREEZES_DIRECTORY}/{freeze_id}.json"


def write_freeze(root: Path | str, result: FreezeResult) -> FreezeResult:
    """Write a freeze, refusing to rewrite one whose identity has changed.

    * No existing file -> write it, and report ``written=True``.
    * Existing file with the same ``freeze_id`` -> leave it byte-for-byte and report
      ``unchanged=True``. This is what makes regenerating a freeze idempotent.
    * Existing file with a different ``freeze_id`` -> raise. Replacing a freeze is a
      rewrite of frozen history; a genuine change must produce a new freeze id and a new
      file, so that the old one remains checkable.

    A refusal is an exception rather than an error string because continuing past it would
    mean a caller could proceed believing the freeze had been recorded.
    """
    base = Path(root)
    if result.errors:
        return result

    target = base / result.output_path
    target.parent.mkdir(parents=True, exist_ok=True)
    new_text = pretty_json_text(result.freeze)

    if target.exists():
        existing_text = target.read_text(encoding="utf-8")
        existing = load_document(target, kind="dataset_freeze")
        existing_id = (existing.value or {}).get("freeze_id") if existing.is_present else None
        if existing_id == result.freeze.get("freeze_id"):
            identical = existing_text == new_text
            return FreezeResult(
                freeze=result.freeze,
                output_path=result.output_path,
                written=False,
                unchanged=True,
                artifacts_verified=result.artifacts_verified,
                byte_identical=identical,
                errors=[],
            )
        raise IntegrityError(
            f"refusing to overwrite an existing freeze at {result.output_path}: it records "
            f"freeze_id={existing_id!r} and this build computed "
            f"{result.freeze.get('freeze_id')!r}. A freeze is immutable; a changed identity "
            f"means a new freeze, and the existing record must stay checkable"
        )

    target.write_text(new_text, encoding="utf-8")
    return FreezeResult(
        freeze=result.freeze,
        output_path=result.output_path,
        written=True,
        unchanged=False,
        artifacts_verified=result.artifacts_verified,
        errors=[],
    )


def validate_freeze_document(freeze: Mapping[str, Any], *, origin: str) -> list[str]:
    """Structural validation of a freeze document, without touching the filesystem."""
    errors: list[str] = []
    errors.extend(reject_unknown_fields(freeze, TOP_LEVEL_FIELDS, origin=origin))
    errors.extend(require_fields(freeze, REQUIRED_FIELDS, origin=origin))
    if any("required field" in error for error in errors):
        return errors

    errors.extend(
        validate_relative_posix_path(
            freeze.get("manifest_path"), origin=origin, label="manifest_path"
        )
    )
    errors.extend(require_int(freeze, "record_schema_version", origin=origin, minimum=1))

    recorded_id = freeze.get("freeze_id")
    if not is_sha256(recorded_id):
        errors.append(f"{origin}: freeze_id must be a sha256 digest, found {recorded_id!r}")
    else:
        recomputed = freeze_identity(freeze)
        if recomputed != recorded_id:
            errors.append(
                f"{origin}: freeze_id does not match its own contents: recorded "
                f"{str(recorded_id)[:12]}…, recomputed {recomputed[:12]}…; the document has "
                f"been edited since it was frozen"
            )

    counts = freeze.get("record_counts")
    if not isinstance(counts, dict):
        errors.append(f"{origin}: record_counts must be a mapping, found {type(counts).__name__}")
    else:
        if "total" not in counts:
            errors.append(f"{origin}: record_counts must contain 'total'")
        for key, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append(
                    f"{origin}: record_counts[{key!r}] must be a non-negative integer, "
                    f"found {value!r}"
                )

    role = freeze.get("artifact_role")
    if role not in ARTIFACT_ROLES:
        errors.append(f"{origin}: artifact_role={role!r} must be one of {list(ARTIFACT_ROLES)}")

    artifacts = freeze.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append(f"{origin}: artifacts must be a list, found {type(artifacts).__name__}")
    elif not artifacts:
        errors.append(
            f"{origin}: artifacts is empty, so this freeze pins no bytes and cannot detect "
            f"that an input changed"
        )
    else:
        seen: set[str] = set()
        for index, entry in enumerate(artifacts):
            label = f"artifacts[{index}]"
            if not isinstance(entry, dict):
                errors.append(f"{origin}: {label} must be a mapping")
                continue
            errors.extend(
                reject_unknown_fields(
                    entry, ("path", "sha256", "size_bytes"), origin=f"{origin}: {label}"
                )
            )
            errors.extend(require_fields(entry, ("path", "sha256"), origin=f"{origin}: {label}"))
            errors.extend(
                validate_relative_posix_path(
                    entry.get("path"), origin=origin, label=f"{label}.path"
                )
            )
            if not is_sha256(entry.get("sha256")):
                errors.append(
                    f"{origin}: {label}.sha256 must be a sha256 digest, "
                    f"found {entry.get('sha256')!r}"
                )
            errors.extend(require_int(entry, "size_bytes", origin=f"{origin}: {label}", minimum=0))
            path = str(entry.get("path"))
            if path in seen:
                errors.append(f"{origin}: {label} duplicates artifact path {path!r}")
            seen.add(path)
    return errors


@dataclass
class FreezeVerification:
    """The outcome of checking one freeze against the repository."""

    freeze_id: str
    path: str
    errors: list[str] = field(default_factory=list)
    artifacts_checked: int = 0
    artifacts_missing: int = 0
    artifacts_changed: int = 0
    manifest_checked: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "freeze_id": self.freeze_id,
            "path": self.path,
            "ok": self.ok,
            "artifacts_checked": self.artifacts_checked,
            "artifacts_missing": self.artifacts_missing,
            "artifacts_changed": self.artifacts_changed,
            "manifest_checked": self.manifest_checked,
            "errors": list(self.errors),
        }


def verify_freeze(
    root: Path | str, freeze: Mapping[str, Any], *, path: str = ""
) -> FreezeVerification:
    """Check every claim a freeze makes against the repository as it is now.

    Fails when:

    * the freeze is structurally invalid or has been edited since it was written;
    * the manifest it names is missing, malformed, or has a different identity;
    * the manifest is now a *different dataset* than the freeze pinned;
    * the manifest no longer declares the artifacts the freeze pinned;
    * an artifact has disappeared;
    * an artifact's bytes have changed;
    * an artifact's size disagrees with what was recorded;
    * the record count has moved.

    Each of these is a separate error message naming the specific disagreement, so a
    reader learns *what* changed rather than only that something did.
    """
    base = Path(root)
    freeze_id = str(freeze.get("freeze_id", "<missing>"))
    result = FreezeVerification(freeze_id=freeze_id, path=path or freeze_relative_path(freeze_id))

    result.errors.extend(validate_freeze_document(freeze, origin=result.path))
    if result.errors:
        return result

    manifest_path = str(freeze["manifest_path"])
    load = load_document(base / manifest_path, kind="dataset_manifest")
    if load.is_absent:
        result.errors.append(
            f"{result.path}: the manifest this freeze pins is missing ({manifest_path}), so the "
            f"frozen dataset can no longer be described or re-checked"
        )
        return result
    if load.is_error:
        result.errors.extend(load.errors(label=f"{result.path} -> {manifest_path}"))
        return result

    manifest = load.require()
    result.manifest_checked = True

    # "The manifest points elsewhere": the freeze and the manifest must agree about which
    # dataset this is. A freeze that names manifest A while recording dataset B is the
    # most dangerous kind of drift, because both files validate individually.
    for field_name, freeze_value in (
        ("dataset_id", freeze.get("dataset_id")),
        ("dataset_version", freeze.get("dataset_version")),
    ):
        manifest_value = manifest.get(field_name)
        if manifest_value != freeze_value:
            result.errors.append(
                f"{result.path}: freeze and manifest disagree about {field_name}: freeze records "
                f"{freeze_value!r}, {manifest_path} records {manifest_value!r}"
            )

    recorded_manifest_hash = freeze.get("manifest_hash")
    if is_sha256(recorded_manifest_hash):
        actual_hash = dataset_identity(manifest)
        if actual_hash != recorded_manifest_hash:
            result.errors.append(
                f"{result.path}: manifest contents changed since the freeze: recorded "
                f"{str(recorded_manifest_hash)[:12]}…, {manifest_path} now hashes to "
                f"{actual_hash[:12]}…"
            )

    declared_by_manifest = {
        str(item)
        for item in (
            manifest.get("processed_files")
            if freeze.get("artifact_role") == "processed"
            else manifest.get("source_files")
        )
        or []
    }
    frozen_paths = {str(entry["path"]) for entry in freeze["artifacts"]}

    for missing in sorted(frozen_paths - declared_by_manifest):
        result.errors.append(
            f"{result.path}: {missing} is frozen but the manifest no longer declares it, so the "
            f"dataset's composition changed"
        )
    for added in sorted(declared_by_manifest - frozen_paths):
        result.errors.append(
            f"{result.path}: {added} is declared by the manifest but was not frozen, so the "
            f"frozen set is incomplete"
        )

    counts = freeze["record_counts"]
    manifest_count = manifest.get("record_count")
    if counts.get("total") != manifest_count:
        result.errors.append(
            f"{result.path}: record count changed: freeze records {counts.get('total')}, "
            f"{manifest_path} records {manifest_count!r}"
        )

    for entry in freeze["artifacts"]:
        artifact_path = str(entry["path"])
        target = base / artifact_path
        if not target.is_file():
            result.artifacts_missing += 1
            result.errors.append(
                f"{result.path}: frozen artifact {artifact_path} is missing, so its identity can "
                f"no longer be verified at all"
            )
            continue
        result.artifacts_checked += 1
        try:
            actual = content_hash(target)
        except OSError as exc:
            result.errors.append(
                f"{result.path}: frozen artifact {artifact_path} is unreadable: {exc}"
            )
            continue
        if actual != entry["sha256"]:
            result.artifacts_changed += 1
            result.errors.append(
                f"{result.path}: frozen artifact {artifact_path} changed: recorded "
                f"{str(entry['sha256'])[:12]}…, found {actual[:12]}…"
            )
        size = entry.get("size_bytes")
        if isinstance(size, int) and size != target.stat().st_size:
            result.errors.append(
                f"{result.path}: frozen artifact {artifact_path} size changed: recorded "
                f"{size} bytes, found {target.stat().st_size}"
            )
    return result


def load_freeze(root: Path | str, path: Path | str) -> tuple[dict[str, Any] | None, list[str]]:
    """Load one freeze document.

    A missing file yields an error, not an empty result: a caller that asked for a freeze
    by path has already decided it must exist.
    """
    load = load_document(Path(root) / path, kind="dataset_freeze")
    if load.is_error:
        return None, load.errors()
    if load.is_absent:
        return None, [f"{load.path}: freeze is missing"]
    return load.require(), []


def canonical_freeze_text(freeze: Mapping[str, Any]) -> str:
    """The exact bytes a freeze is written as.

    Exposed so a determinism test can compare two constructions byte-for-byte without
    going through the filesystem.
    """
    return pretty_json_text(freeze)


def freeze_summary(freeze: Mapping[str, Any]) -> str:
    """A one-line description, for logs and CLI output."""
    counts = freeze.get("record_counts") or {}
    return (
        f"{str(freeze.get('freeze_id', '<no id>'))[:12]}… dataset={freeze.get('dataset_id')} "
        f"records={counts.get('total')} artifacts={len(freeze.get('artifacts') or [])}"
    )


__all__ = [
    "ARTIFACT_ROLES",
    "FREEZES_DIRECTORY",
    "IDENTITY_FIELDS",
    "MANIFESTS_DIRECTORY",
    "REQUIRED_FIELDS",
    "TOP_LEVEL_FIELDS",
    "VOLATILE_FIELDS",
    "FreezeResult",
    "FreezeVerification",
    "artifact_entries",
    "build_freeze",
    "canonical_freeze_text",
    "freeze_format_version",
    "freeze_identity",
    "freeze_relative_path",
    "freeze_summary",
    "load_freeze",
    "validate_freeze_document",
    "verify_freeze",
    "write_freeze",
]
