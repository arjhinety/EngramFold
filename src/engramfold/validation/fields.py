"""Shared field-level checks for every manifest schema in this repository.

Written once so that "is this a sha256?", "is this an immutable revision?" and "is this
path reproducible?" have exactly one answer each. A second implementation of any of
these would eventually disagree with the first, and the disagreement would show up as a
manifest that one gate accepts and another rejects -- with no way to tell which is
right.

Two checks deserve particular attention because their absence is how manifests lie:

:func:`reject_unknown_fields`
    A renamed field must fail. If unknown keys were ignored, then renaming
    ``record_count`` to ``records`` would leave a manifest that validates cleanly while
    silently having no record count at all -- and every downstream check that reads
    ``record_count`` would quietly stop being applied. Unknown fields are errors, not
    warnings, because the entire point of a schema is that a field that is not in it is
    not being interpreted.

:func:`validate_relative_posix_path`
    Recorded paths must be relative and forward-slashed. An absolute path makes a
    manifest describe one machine; a backslash path is not portable; a ``..`` segment
    escapes the repository. All three would make a recorded identity depend on where the
    work happened to be done.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHORT_REVISION_RE = re.compile(r"^[0-9a-f]{7,39}$")
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
BRANCH_LIKE_RE = re.compile(r"^(main|master|head|latest|dev|develop|trunk|stable)$", re.IGNORECASE)


def is_sha256(value: Any) -> bool:
    """Whether ``value`` is a lowercase 64-character hex digest."""
    return isinstance(value, str) and bool(SHA256_RE.match(value))


def is_full_revision(value: Any) -> bool:
    """Whether ``value`` pins a source to one immutable commit.

    Only a full 40-hex commit qualifies. A short revision is rejected deliberately:
    abbreviation is ambiguous and a seven-character prefix can collide, so it pins
    nothing. This is the difference between a revision and a hint at one.
    """
    return isinstance(value, str) and bool(FULL_REVISION_RE.match(value))


def is_short_revision(value: Any) -> bool:
    """Whether ``value`` looks like an abbreviated commit -- useful for diagnosis only."""
    return isinstance(value, str) and bool(SHORT_REVISION_RE.match(value))


def is_branch_like(value: Any) -> bool:
    """Whether ``value`` names a moving ref rather than a commit."""
    return isinstance(value, str) and bool(BRANCH_LIKE_RE.match(value))


def is_identifier(value: Any) -> bool:
    """Whether ``value`` is a usable canonical identifier."""
    return isinstance(value, str) and bool(IDENTIFIER_RE.match(value))


def require_fields(
    document: Mapping[str, Any], required: Sequence[str], *, origin: str
) -> list[str]:
    """Errors for every required field that is missing or empty.

    "Empty" counts: a present-but-blank required string carries no more information than
    an absent one, and treating it as satisfied is how a manifest ends up recording
    ``license: ""`` and passing.
    """
    errors: list[str] = []
    for field_name in required:
        if field_name not in document:
            errors.append(f"{origin}: required field {field_name!r} is missing")
            continue
        value = document[field_name]
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{origin}: required field {field_name!r} is empty")
    return errors


def reject_unknown_fields(
    document: Mapping[str, Any], allowed: Iterable[str], *, origin: str
) -> list[str]:
    """Errors for every field the schema does not define.

    The message names the known fields, so a typo is diagnosable from the failure --
    ``record_count`` misspelled as ``record_counts`` should read as a typo, not as a
    mysterious absence downstream.
    """
    known = set(allowed)
    unknown = sorted(str(key) for key in document if str(key) not in known)
    if not unknown:
        return []
    return [
        f"{origin}: unknown field(s) {unknown}; a field the schema does not define is not "
        f"interpreted by anything. Known fields: {sorted(known)}"
    ]


def require_nonempty_str(document: Mapping[str, Any], field_name: str, *, origin: str) -> list[str]:
    """Error when a field is present but is not a non-blank string."""
    if field_name not in document:
        return []
    value = document[field_name]
    if not isinstance(value, str) or not value.strip():
        return [f"{origin}: {field_name} must be a non-blank string, found {value!r}"]
    return []


def require_int(
    document: Mapping[str, Any],
    field_name: str,
    *,
    origin: str,
    minimum: int | None = None,
) -> list[str]:
    """Error when a field is present but is not an integer, or is below ``minimum``.

    ``bool`` is rejected explicitly: ``True`` is an ``int`` in Python, and a manifest
    recording ``record_count: true`` must not be read as a count of one.
    """
    if field_name not in document:
        return []
    value = document[field_name]
    if isinstance(value, bool) or not isinstance(value, int):
        return [f"{origin}: {field_name} must be an integer, found {type(value).__name__}"]
    if minimum is not None and value < minimum:
        return [f"{origin}: {field_name} must be >= {minimum}, found {value}"]
    return []


def validate_relative_posix_path(value: Any, *, origin: str, label: str) -> list[str]:
    """Error unless ``value`` is a repository-relative, forward-slashed path.

    Rejected, each for a specific reason:

    * an absolute path -- makes the record describe one machine;
    * a backslash -- not portable, and it would hash differently on Windows;
    * a ``..`` segment -- escapes the repository and makes the target ambiguous;
    * a ``.`` prefix -- cosmetically identical to a clean relative path but would
      compare unequal, so a freeze and a manifest could disagree about the same file.
    """
    if not isinstance(value, str) or not value.strip():
        return [f"{origin}: {label} must be a non-blank string path, found {value!r}"]
    if value != value.strip():
        return [f"{origin}: {label} has leading or trailing whitespace: {value!r}"]
    if "\\" in value:
        return [
            f"{origin}: {label} uses a backslash separator ({value!r}); recorded paths "
            f"must use forward slashes so the same tree hashes identically everywhere"
        ]
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return [
            f"{origin}: {label} is absolute ({value!r}); a machine-specific path makes "
            f"this record describe one machine rather than the research object"
        ]
    parts = value.split("/")
    if any(part == ".." for part in parts):
        return [
            f"{origin}: {label} contains a '..' segment ({value!r}), which escapes the repository"
        ]
    if any(part in ("", ".") for part in parts):
        return [f"{origin}: {label} has an empty or '.' path segment ({value!r})"]
    return []


def validate_path_list(value: Any, *, origin: str, label: str) -> list[str]:
    """Validate that a field is a list of repository-relative paths."""
    if not isinstance(value, list):
        return [f"{origin}: {label} must be a list, found {type(value).__name__}"]
    errors: list[str] = []
    for index, item in enumerate(value):
        errors.extend(validate_relative_posix_path(item, origin=origin, label=f"{label}[{index}]"))
    if len({str(item) for item in value}) != len(value):
        errors.append(f"{origin}: {label} contains duplicate entries")
    return errors


def validate_hash_map(value: Any, *, origin: str, label: str) -> list[str]:
    """Validate that a field is a mapping of relative path to sha256 digest."""
    if not isinstance(value, dict):
        return [
            f"{origin}: {label} must be a mapping of path to sha256, found {type(value).__name__}"
        ]
    errors: list[str] = []
    for key, digest in value.items():
        errors.extend(validate_relative_posix_path(key, origin=origin, label=f"{label} key"))
        if not is_sha256(digest):
            errors.append(
                f"{origin}: {label}[{key!r}] must be a lowercase sha256 digest, found {digest!r}"
            )
    return errors


def check_parallel_lists(
    paths: Any,
    hashes: Any,
    *,
    origin: str,
    paths_label: str,
    hashes_label: str,
) -> list[str]:
    """Errors when a path list and its hash list disagree.

    The pairing is checked because the alternative -- a list of files with a shorter or
    reordered list of digests -- means some file has no recorded identity, and a check
    that iterates the pair silently stops covering it.
    """
    errors: list[str] = []
    if isinstance(paths, list) and isinstance(hashes, dict):
        missing = [str(item) for item in paths if str(item) not in hashes]
        extra = [str(key) for key in hashes if str(key) not in {str(i) for i in paths}]
        if missing:
            errors.append(
                f"{origin}: {paths_label} entries have no digest in {hashes_label}: {missing}"
            )
        if extra:
            errors.append(
                f"{origin}: {hashes_label} records digests for paths not in {paths_label}: {extra}"
            )
    elif isinstance(paths, list) and isinstance(hashes, list):
        if len(paths) != len(hashes):
            errors.append(
                f"{origin}: {paths_label} has {len(paths)} entries but {hashes_label} has "
                f"{len(hashes)}; the pairing is ambiguous, so no file's identity is established"
            )
    return errors


def require_immutable_revision(
    document: Mapping[str, Any],
    field_name: str,
    *,
    origin: str,
) -> list[str]:
    """Require a field to be a full commit hash, explaining what is wrong when it is not."""
    if field_name not in document:
        return []
    value = document[field_name]
    if is_full_revision(value):
        return []
    if is_branch_like(value):
        return [
            f"{origin}: {field_name}={value!r} names a moving branch, not an immutable "
            f"revision; a branch resolves to different bytes over time and pins nothing"
        ]
    if is_short_revision(value):
        return [
            f"{origin}: {field_name}={value!r} is an abbreviated revision; abbreviation is "
            f"ambiguous and can collide, so a full 40-character commit is required"
        ]
    return [f"{origin}: {field_name} must be a full 40-character commit hash, found {value!r}"]


def normalize_source_files(value: Any) -> list[str]:
    """A sorted, de-duplicated copy of a source-file list.

    Sorting makes the recorded order canonical; two manifests listing the same files in
    different orders must hash identically.
    """
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value})


#: The fields a git provenance record carries when embedded in a manifest.
CODE_REVISION_FIELDS: tuple[str, ...] = (
    "provenance_record_version",
    "git_commit",
    "git_branch",
    "git_dirty",
)


def validate_code_revision(
    value: Any,
    *,
    origin: str,
    label: str = "code_revision",
    dirty_must_be_known: bool = True,
) -> list[str]:
    """Validate a git provenance record embedded in a manifest.

    Shared by the experiment and artifact manifests so the two cannot drift apart, and so the
    tri-state of ``git_dirty`` is decided once.

    ``git_dirty`` is deliberately validated apart from the other fields rather than through
    :func:`require_fields`. That helper treats ``null`` as "empty", but here ``null`` is a
    meaningful value -- it means *unknowable* -- and rejecting it as blank conflates two
    different things. What matters is that it is never *acceptable*: an artifact or an
    experiment that cannot say whether its code was clean is not attributable, so
    ``dirty_must_be_known`` turns an unknowable state into an error while still allowing the
    value to be represented.
    """
    if not isinstance(value, dict):
        return [
            f"{origin}: {label} must be a provenance record mapping, found "
            f"{type(value).__name__}; a bare commit cannot distinguish clean from dirty"
        ]

    errors: list[str] = []
    errors.extend(
        require_fields(
            value,
            ("provenance_record_version", "git_commit", "git_branch"),
            origin=f"{origin}: {label}",
        )
    )

    if "git_dirty" not in value:
        errors.append(
            f"{origin}: {label} does not record git_dirty; a provenance record that omits the "
            f"dirty state cannot distinguish a clean checkout from one with uncommitted edits"
        )
    else:
        dirty = value["git_dirty"]
        if dirty is None:
            if dirty_must_be_known:
                errors.append(
                    f"{origin}: {label}.git_dirty is null, meaning provenance was unknowable; an "
                    f"artifact or experiment that cannot be attributed to a checkout state is not "
                    f"evidence"
                )
        elif not isinstance(dirty, bool):
            errors.append(f"{origin}: {label}.git_dirty must be a boolean or null, found {dirty!r}")
        elif dirty and not str(value.get("dirty_override_reason") or "").strip():
            errors.append(
                f"{origin}: {label} records a dirty tree with no dirty_override_reason; if "
                f"uncommitted code is acceptable here, the override must be written down rather "
                f"than left implicit"
            )

    commit = value.get("git_commit")
    if commit is not None and not (is_full_revision(commit) or commit == "unknown"):
        errors.append(
            f"{origin}: {label}.git_commit={commit!r} is neither a full 40-character commit nor "
            f"the explicit 'unknown' marker"
        )
    return errors


__all__ = [
    "BRANCH_LIKE_RE",
    "CODE_REVISION_FIELDS",
    "FULL_REVISION_RE",
    "IDENTIFIER_RE",
    "SHA256_RE",
    "SHORT_REVISION_RE",
    "check_parallel_lists",
    "is_branch_like",
    "is_full_revision",
    "is_identifier",
    "is_sha256",
    "is_short_revision",
    "normalize_source_files",
    "reject_unknown_fields",
    "require_fields",
    "require_immutable_revision",
    "require_int",
    "require_nonempty_str",
    "validate_code_revision",
    "validate_hash_map",
    "validate_path_list",
    "validate_relative_posix_path",
]
