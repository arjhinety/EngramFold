"""Dataset manifest schema: where every input came from, and how to re-derive it.

A dataset manifest is the only place a piece of research data is allowed to come from.
It records the upstream source pinned to something immutable, the licence, the
processing that turned raw bytes into usable records, and the identity of the result.
Its purpose is to make the question "where did this come from, exactly?" answerable
without asking anyone.

Two version fields, deliberately not merged
-------------------------------------------

``dataset_manifest_version``
    The version of *this document format* -- which fields exist and what they mean. A
    reader needs it to interpret the file at all.
``schema_version``
    The version of *the records themselves* -- the layout of an individual training or
    evaluation example. Two manifests can use the same document format while describing
    records with different layouts.

These are different facts and are recorded in different fields. Collapsing them into one
``version`` key would mean that changing a field name and changing the record layout
looked identical in the record.

Required versus optional
------------------------

Required: the fields without which the manifest does not identify anything --
``dataset_id``, ``dataset_version``, ``source_type``, ``split``, ``license``,
``record_count``, ``adapter_version``, ``deduplication_policy``,
``contamination_policy``, plus both version fields.

Conditionally required, by ``source_type``: a Hugging Face source must pin a full
commit; a local source must record digests of the files it read; a URL source must
record when it was fetched; a synthetic source must identify its generator. The
conditions encode the claim each source type makes, so a manifest cannot assert
"this came from a pinned upstream release" while omitting the pin.

Everything else is optional and may be absent without weakening the manifest -- but a
field that is present is always validated. There is no mode in which a malformed value
is skipped because the field was optional.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from engramfold.documents import load_document
from engramfold.hashing import manifest_hash
from engramfold.schemas import current_version
from engramfold.validation.fields import (
    check_parallel_lists,
    is_identifier,
    is_sha256,
    reject_unknown_fields,
    require_fields,
    require_immutable_revision,
    require_int,
    require_nonempty_str,
    validate_hash_map,
    validate_path_list,
)

#: Recognised provenance classes for a dataset.
SOURCE_TYPES: tuple[str, ...] = ("huggingface", "local", "remote_url", "synthetic")

SYNTHETIC_FIELDS: tuple[str, ...] = (
    "generator_model",
    "model_revision",
    "provider",
    "generation_config",
    "generation_config_hash",
    "prompt_template_version",
    "random_seed",
    "generation_code_revision",
    "raw_generation_artifact",
)

TOP_LEVEL_FIELDS: tuple[str, ...] = (
    # ── Document identity ──
    "dataset_manifest_version",
    "schema_version",
    # ── Dataset identity ──
    "dataset_id",
    "dataset_version",
    "split",
    # ── Upstream source ──
    "source_type",
    "source_repository",
    "source_revision",
    "source_url",
    "source_files",
    "source_file_hashes",
    "retrieved_at",
    # ── Licensing ──
    "license",
    "license_url",
    "license_unknown_rationale",
    # ── Processing ──
    "processed_files",
    "processed_file_hashes",
    "record_count",
    "empty_dataset_rationale",
    "adapter_version",
    "processing_command",
    "processing_code_revision",
    "processing_config_hash",
    "random_seed",
    "deduplication_policy",
    "contamination_policy",
    # ── Synthetic provenance (not implemented; schema only) ──
    "synthetic",
    # ── Free-form ──
    "notes",
)

REQUIRED_FIELDS: tuple[str, ...] = (
    "dataset_manifest_version",
    "schema_version",
    "dataset_id",
    "dataset_version",
    "source_type",
    "split",
    "license",
    "record_count",
    "adapter_version",
    "deduplication_policy",
    "contamination_policy",
)


def dataset_manifest_version() -> int:
    """The manifest-format version this build writes."""
    return current_version("dataset_manifest")


def validate_dataset_manifest(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """Every reason ``manifest`` is not a usable dataset manifest.

    Returns error strings prefixed with ``origin``. An empty list means the manifest is
    valid; it does *not* mean the dataset exists, which is what the freeze gate is for.
    """
    errors: list[str] = []
    errors.extend(reject_unknown_fields(manifest, TOP_LEVEL_FIELDS, origin=origin))
    errors.extend(require_fields(manifest, REQUIRED_FIELDS, origin=origin))
    if errors and any("required field" in error for error in errors):
        # Structural problems first: continuing would produce a cascade of errors about
        # fields whose absence has already been reported.
        return errors

    dataset_id = manifest.get("dataset_id")
    if not is_identifier(dataset_id):
        errors.append(
            f"{origin}: dataset_id={dataset_id!r} must match [a-z0-9][a-z0-9._-]*; "
            f"identifiers are referenced from other documents and must be stable and "
            f"unambiguous"
        )

    errors.extend(require_nonempty_str(manifest, "dataset_version", origin=origin))
    errors.extend(require_nonempty_str(manifest, "adapter_version", origin=origin))
    errors.extend(require_nonempty_str(manifest, "deduplication_policy", origin=origin))
    errors.extend(require_nonempty_str(manifest, "contamination_policy", origin=origin))
    errors.extend(require_nonempty_str(manifest, "split", origin=origin))
    errors.extend(require_int(manifest, "schema_version", origin=origin, minimum=1))
    errors.extend(require_int(manifest, "record_count", origin=origin, minimum=0))
    errors.extend(_validate_license(manifest, origin=origin))
    errors.extend(_validate_source(manifest, origin=origin))
    errors.extend(_validate_processing(manifest, origin=origin))
    errors.extend(_validate_counts(manifest, origin=origin))
    return errors


def _validate_license(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """A licence must be named, and an unknown licence must be declared as unknown.

    ``license: "UNKNOWN"`` is permitted -- much upstream data genuinely has an unclear
    licence -- but only with a written rationale. That keeps "we did not check" from
    being indistinguishable from "the licence is permissive".
    """
    errors: list[str] = []
    license_value = manifest.get("license")
    if not isinstance(license_value, str) or not license_value.strip():
        errors.append(f"{origin}: license must be a non-blank string, found {license_value!r}")
        return errors
    if license_value.strip().upper() in {"UNKNOWN", "UNSPECIFIED", "NONE", "TBD"}:
        rationale = manifest.get("license_unknown_rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(
                f"{origin}: license={license_value!r} declares the licence unknown, so "
                f"license_unknown_rationale must state why the data is being used anyway"
            )
    errors.extend(require_nonempty_str(manifest, "license_url", origin=origin))
    return errors


def _validate_source(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """Per-source-type requirements.

    Each branch encodes the immutability claim that source type makes. The point is that
    a manifest cannot say "pinned Hugging Face revision" and then not pin one.
    """
    errors: list[str] = []
    source_type = manifest.get("source_type")
    if source_type not in SOURCE_TYPES:
        errors.append(f"{origin}: source_type={source_type!r} must be one of {list(SOURCE_TYPES)}")
        return errors

    if "source_files" in manifest:
        errors.extend(
            validate_path_list(manifest["source_files"], origin=origin, label="source_files")
        )
    if "source_file_hashes" in manifest:
        errors.extend(
            validate_hash_map(
                manifest["source_file_hashes"], origin=origin, label="source_file_hashes"
            )
        )
    if "source_files" in manifest and "source_file_hashes" in manifest:
        errors.extend(
            check_parallel_lists(
                manifest["source_files"],
                manifest["source_file_hashes"],
                origin=origin,
                paths_label="source_files",
                hashes_label="source_file_hashes",
            )
        )

    if source_type == "huggingface":
        errors.extend(
            require_fields(
                manifest, ("source_repository", "source_revision", "source_files"), origin=origin
            )
        )
        errors.extend(require_immutable_revision(manifest, "source_revision", origin=origin))
        repository = manifest.get("source_repository")
        if isinstance(repository, str) and "/" not in repository.strip():
            errors.append(
                f"{origin}: source_repository={repository!r} is not a '<owner>/<name>' "
                f"repository identifier"
            )
    elif source_type == "local":
        errors.extend(
            require_fields(manifest, ("source_files", "source_file_hashes"), origin=origin)
        )
    elif source_type == "remote_url":
        errors.extend(
            require_fields(
                manifest, ("source_url", "retrieved_at", "source_file_hashes"), origin=origin
            )
        )
        url = manifest.get("source_url")
        if isinstance(url, str) and not url.startswith(("http://", "https://")):
            errors.append(f"{origin}: source_url={url!r} is not an http(s) URL")
    elif source_type == "synthetic":
        errors.extend(_validate_synthetic(manifest, origin=origin))
    return errors


def _validate_synthetic(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """Schema support for generated data.

    Generation is **not implemented** in this repository, and this function does not
    create anything -- it only refuses a synthetic manifest that cannot identify its
    generator. If generated data cannot name the model, the revision, the generation
    configuration and the code that ran it, the records are not reproducible and must not
    enter the canonical record.

    ``random_seed`` is required once ``generation_config`` implies sampling, because a
    generator run at a non-zero temperature without a recorded seed cannot be repeated.
    """
    errors: list[str] = []
    block = manifest.get("synthetic")
    if not isinstance(block, dict):
        errors.append(
            f"{origin}: source_type='synthetic' requires a 'synthetic' block identifying the "
            f"generator; found {type(block).__name__}"
        )
        return errors
    errors.extend(reject_unknown_fields(block, SYNTHETIC_FIELDS, origin=f"{origin}: synthetic"))
    errors.extend(
        require_fields(
            block,
            (
                "generator_model",
                "model_revision",
                "generation_config_hash",
                "prompt_template_version",
                "generation_code_revision",
                "raw_generation_artifact",
            ),
            origin=f"{origin}: synthetic",
        )
    )
    if not is_sha256(block.get("generation_config_hash")):
        errors.append(
            f"{origin}: synthetic.generation_config_hash must be a sha256 digest of the "
            f"generation configuration, found {block.get('generation_config_hash')!r}"
        )
    errors.extend(
        require_immutable_revision(block, "model_revision", origin=f"{origin}: synthetic")
    )
    errors.extend(
        validate_path_list(
            [block.get("raw_generation_artifact")],
            origin=f"{origin}: synthetic",
            label="raw_generation_artifact",
        )
        if block.get("raw_generation_artifact") is not None
        else []
    )

    config = block.get("generation_config")
    sampling = False
    if isinstance(config, dict):
        for key in ("temperature", "top_p", "top_k"):
            value = config.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                sampling = True
    if sampling and "random_seed" not in block:
        errors.append(
            f"{origin}: synthetic.generation_config samples (temperature/top_p/top_k > 0) but "
            f"no random_seed is recorded, so the generation cannot be repeated"
        )
    return errors


def _validate_processing(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """Processing that produced files must be described well enough to re-derive them.

    The rule: if ``processed_files`` is non-empty, the manifest must record the command,
    the code revision, the configuration hash and the seed. Without all four, the
    processed bytes exist but the transformation that made them is unrecoverable -- and a
    freeze over them would pin an output nobody can reproduce.
    """
    errors: list[str] = []
    if "processed_files" in manifest:
        errors.extend(
            validate_path_list(manifest["processed_files"], origin=origin, label="processed_files")
        )
    if "processed_file_hashes" in manifest:
        errors.extend(
            validate_hash_map(
                manifest["processed_file_hashes"], origin=origin, label="processed_file_hashes"
            )
        )
    if "processed_files" in manifest and "processed_file_hashes" in manifest:
        errors.extend(
            check_parallel_lists(
                manifest["processed_files"],
                manifest["processed_file_hashes"],
                origin=origin,
                paths_label="processed_files",
                hashes_label="processed_file_hashes",
            )
        )

    processed = manifest.get("processed_files")
    if isinstance(processed, list) and processed:
        errors.extend(
            require_fields(
                manifest,
                (
                    "processing_command",
                    "processing_code_revision",
                    "processing_config_hash",
                    "random_seed",
                ),
                origin=origin,
            )
        )
        errors.extend(
            require_immutable_revision(manifest, "processing_code_revision", origin=origin)
        )
        if not is_sha256(manifest.get("processing_config_hash")):
            errors.append(
                f"{origin}: processing_config_hash must be a sha256 digest of the processing "
                f"configuration, found {manifest.get('processing_config_hash')!r}"
            )
        command = manifest.get("processing_command")
        if isinstance(command, str) and "{{" in command:
            errors.append(
                f"{origin}: processing_command still contains an unexpanded template "
                f"placeholder, so it is not the command that ran"
            )
    errors.extend(require_int(manifest, "random_seed", origin=origin, minimum=0))
    return errors


def _validate_counts(manifest: Mapping[str, Any], *, origin: str) -> list[str]:
    """A count of zero must be declared, not merely asserted.

    An empty dataset is occasionally correct and much more often a sign that a filter
    matched nothing. Requiring a rationale makes the two distinguishable at the point the
    manifest is written, rather than after a training run has consumed nothing.
    """
    errors: list[str] = []
    count = manifest.get("record_count")
    if isinstance(count, int) and not isinstance(count, bool) and count == 0:
        rationale = manifest.get("empty_dataset_rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            errors.append(
                f"{origin}: record_count is 0, which is usually a filter that matched nothing; "
                f"empty_dataset_rationale must state why an empty dataset is intended here"
            )
    if "empty_dataset_rationale" in manifest and isinstance(count, int) and count > 0:
        errors.append(
            f"{origin}: empty_dataset_rationale is present but record_count is {count}; "
            f"remove the rationale or correct the count"
        )
    return errors


def dataset_identity(manifest: Mapping[str, Any]) -> str:
    """The deterministic identity of a dataset manifest.

    Timestamps and machine-local fields are excluded (see ``engramfold.hashing``), so the
    same dataset described twice has one identity while each manifest still records when
    it was written.
    """
    return manifest_hash(manifest)


def load_dataset_manifest(
    root: Path | str, relative: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """Load and validate one dataset manifest.

    Returns ``(manifest, errors)``. A malformed or version-incompatible file yields
    ``(None, errors)`` -- never ``(None, [])``, which would be indistinguishable from a
    valid empty manifest and is exactly how a broken document becomes a silent skip.
    """
    load = load_document(Path(root) / relative, kind="dataset_manifest")
    if load.is_error:
        return None, load.errors()
    if load.is_absent:
        return None, [f"{load.path}: dataset manifest is missing"]
    manifest = load.require()
    errors = validate_dataset_manifest(manifest, origin=load.path)
    # The manifest is returned even when it has errors, so a caller can report *what* is
    # wrong rather than only that it is unusable. A caller that reads only the manifest and
    # discards `errors` is the bug this signature is shaped to expose.
    return manifest, errors


__all__ = [
    "REQUIRED_FIELDS",
    "SOURCE_TYPES",
    "SYNTHETIC_FIELDS",
    "TOP_LEVEL_FIELDS",
    "dataset_identity",
    "dataset_manifest_version",
    "load_dataset_manifest",
    "validate_dataset_manifest",
]
