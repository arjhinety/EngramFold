"""The single canonical hashing implementation for EngramFold.

Everything in this repository that computes a digest calls into this module. There
is deliberately no second, slightly different implementation anywhere: two hashing
routines that disagree by one byte of normalisation produce two incompatible notions
of identity, and every downstream freeze becomes unverifiable for a reason no one can
see. ``tests/test_anti_patterns.py`` scans the source tree and fails if any other
module implements ``sha256`` file hashing.

Identity kinds, kept distinct on purpose
----------------------------------------

The specification for this repository forbids overloading one field with several
meanings. These are the kinds, and they are not interchangeable:

``content_hash``
    SHA-256 over the exact bytes of one file. Answers "are these the same bytes?".
``directory_hash``
    SHA-256 over a deterministic, ordered census of a directory tree. Answers "is
    this the same tree?".
``manifest_hash``
    SHA-256 over the *semantic* content of a manifest with volatile fields removed.
    Independent of file formatting and of when the manifest was written.
``configuration_hash``
    Same construction, for a configuration document. Distinct from ``manifest_hash``
    because the two hash different classes of document, and a validator that
    conflated them could not tell which identity it had just verified.
``artifact_hash``
    ``content_hash`` of a produced artifact. Named separately because a freeze
    records it in an ``artifact_hash`` field, and a reader should not have to infer
    which of the above it is.
``source_revision``
    A git commit identifier. Explicitly **not** a hash of anything in this module --
    see ``engramfold.provenance.git_state``. A revision is a pointer into history, not
    a digest of content, and recording it in a hash-shaped field would be a category
    error that makes dirty-tree state invisible.

Determinism rules, stated so they can be checked
------------------------------------------------

* Ordering is always total and explicit. Directory entries sort by normalised
  relative path; JSON keys sort lexicographically.
* JSON used for identity is compact, key-sorted, UTF-8, ``ensure_ascii=False``, and
  refuses ``NaN``/``Infinity`` (which have no canonical representation and would
  otherwise hash differently across platforms).
* Volatile fields -- timestamps, hostnames, absolute paths -- are removed from
  identities recursively. They are still *recorded*; they simply do not participate
  in what "the same" means. This is what lets the same dataset materialised twice
  produce the same ``manifest_hash`` while each manifest still says when it happened.
* No identity depends on a machine-specific absolute path.
* Hashing is streamed in fixed-size chunks, so a 200 GB artifact hashes in constant
  memory and the chunk size is part of the format (it does not affect the digest).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engramfold.errors import DocumentError, IntegrityError

# Bumping either of these changes what an existing recorded digest means. They are
# versioned so that a digest can always be traced to the rules that produced it.
CANONICAL_JSON_VERSION = 1
DIRECTORY_HASH_VERSION = 1

# Streamed read size. Deliberately in the module rather than at each call site: two
# call sites picking different chunk sizes is harmless for the digest but is the
# first symptom of a second implementation growing.
STREAM_CHUNK_BYTES = 1 << 20

# Removed from every content identity. These change for reasons that say nothing
# about whether the research object is the same research object.
VOLATILE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "created_at",
        "updated_at",
        "retrieved_at",
        "captured_at",
        "generated_at",
        "recorded_at",
        "started_at",
        "completed_at",
        "frozen_at",
        "freeze_timestamp",
        "timestamp",
        "run_started_at",
        "run_completed_at",
    }
)

# Also removed from content identity, because a digest that depends on which machine
# ran the code is a digest of the machine. These are recorded for diagnostics; they
# are not part of "the same".
MACHINE_LOCAL_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "hostname",
        "hostname_class",
        "absolute_path",
        "resolved_path",
        "local_path",
        "root_path",
        "machine",
    }
)

IDENTITY_EXCLUDED_FIELD_NAMES: frozenset[str] = VOLATILE_FIELD_NAMES | MACHINE_LOCAL_FIELD_NAMES

# Directory-hash exclusions. Explicit, because an implicit exclusion rule is how a
# digest silently starts ignoring a directory that was meant to be covered.
DEFAULT_DIRECTORY_EXCLUDES: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    ".cache",
    ".scratch",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    "Thumbs.db",
)


# ── Byte-level hashing ───────────────────────────────────────────────────────


def sha256_bytes(payload: bytes) -> str:
    """SHA-256 of ``payload``, lowercase hex."""
    return hashlib.sha256(payload).hexdigest()


def hash_stream(handle: Any) -> str:
    """SHA-256 of a binary stream, read in fixed chunks."""
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(STREAM_CHUNK_BYTES), b""):
        digest.update(chunk)
    return digest.hexdigest()


def content_hash(path: Path | str) -> str:
    """SHA-256 over the exact bytes of ``path``.

    Streamed, so constant memory for arbitrarily large artifacts. Raises if the path
    is not a regular file -- a directory or a missing path is not a content hash of
    anything, and returning a digest for either would be a fabricated identity.
    """
    target = Path(path)
    if not target.is_file():
        raise IntegrityError(f"content_hash requires a regular file: {target}")
    with target.open("rb") as handle:
        return hash_stream(handle)


def content_hash_or_none(path: Path | str) -> str | None:
    """``content_hash`` for callers that must distinguish absence.

    ``None`` means the file is not there. It never means unreadable: an I/O failure
    is raised, because a permission error reported as "no file" is precisely the
    silent-skip failure this repository is built to prevent.
    """
    target = Path(path)
    if not target.exists():
        return None
    return content_hash(target)


# ─ Canonical JSON ──────────────────────────────────────────────────────────


def canonical_json_text(value: Any) -> str:
    """Compact, key-sorted, deterministic JSON for a value.

    ``allow_nan=False`` is not a stylistic choice: ``NaN`` and ``Infinity`` are not
    representable in JSON, and Python's default encoder emits bare tokens for them
    that other parsers reject. A digest computed over such output is not portable.
    """
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as exc:
        raise DocumentError(f"value is not canonically serialisable: {exc}") from exc


def canonical_json_bytes(value: Any) -> bytes:
    """UTF-8 encoding of :func:`canonical_json_text`."""
    return canonical_json_text(value).encode("utf-8")


def canonical_json_hash(value: Any) -> str:
    """SHA-256 of the canonical JSON encoding of ``value``."""
    return sha256_bytes(canonical_json_bytes(value))


def pretty_json_text(value: Any) -> str:
    """Human-readable canonical JSON with a trailing newline.

    This is what gets *written* to disk. It is deliberately different from the
    serialisation that gets *hashed*: a manifest's recorded line breaks must not
    change its identity, so ``manifest_hash`` is taken over the compact form.
    """
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


# ─ Identity extraction ──────────────────────────────────────────────────────


def strip_identity_excluded(value: Any, extra: Iterable[str] = ()) -> Any:
    """Recursively drop fields that must not participate in a content identity.

    Removing by exact key name at every depth. Over-removal is possible in principle
    (a data payload containing a literal key called ``timestamp`` would lose it from
    the identity), which is why the removed names are a short, explicit, documented
    list rather than a heuristic, and why the retained payload is returned rather
    than merely hashed -- a caller can always inspect what was hashed.
    """
    excluded = IDENTITY_EXCLUDED_FIELD_NAMES | frozenset(extra)
    if isinstance(value, Mapping):
        return {
            str(key): strip_identity_excluded(item, excluded)
            for key, item in value.items()
            if str(key) not in excluded
        }
    if isinstance(value, (list, tuple)):
        return [strip_identity_excluded(item, excluded) for item in value]
    return value


def manifest_hash(manifest: Mapping[str, Any], *, exclude: Iterable[str] = ()) -> str:
    """Deterministic identity of a manifest's semantic content."""
    return canonical_json_hash(strip_identity_excluded(manifest, exclude))


def configuration_hash(config: Mapping[str, Any], *, exclude: Iterable[str] = ()) -> str:
    """Deterministic identity of a configuration document.

    Key ordering in the source file does not affect this: the value is canonicalised
    before hashing, so reordering a YAML mapping is not a change to the experiment.
    """
    return canonical_json_hash(strip_identity_excluded(config, exclude))


def artifact_hash(path: Path | str) -> str:
    """``content_hash`` under the name the artifact records use."""
    return content_hash(path)


def record_identity(record: Mapping[str, Any], *, exclude: Iterable[str] = ()) -> str:
    """Identity of an arbitrary recorded structure, volatile fields removed."""
    return canonical_json_hash(strip_identity_excluded(record, exclude))


def selective_identity(payload: Mapping[str, Any], fields: Sequence[str]) -> str:
    """Identity computed over exactly ``fields``, in the order given.

    The shared primitive behind every freeze's ``freeze_id``. Each freeze document
    declares its own identity field list -- the *contents* differ, the *construction*
    does not -- so a change to how identities are computed is a change in one place
    rather than one per freeze kind.

    A field named in ``fields`` but absent from ``payload`` is simply omitted, so a
    caller can compute the identity of an incomplete document while it is being built.
    :func:`validate_freeze_document` is what insists the fields are all present before
    any identity is trusted.
    """
    return canonical_json_hash({name: payload[name] for name in fields if name in payload})


# ── Directory hashing ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TreeEntry:
    """One covered path in a directory census."""

    path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size_bytes": self.size_bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class DirectoryDigest:
    """The digest of a tree, plus the census it was computed from.

    The census is returned and not merely consumed because a digest error is almost
    always a disagreement about *which files were covered*, and a bare hex string
    gives a reader nothing to compare. This is the difference between "the tree
    changed" and "these two files changed".
    """

    root: str
    digest: str
    entries: tuple[TreeEntry, ...] = ()
    excluded: tuple[str, ...] = ()
    directory_hash_version: int = DIRECTORY_HASH_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory_hash_version": self.directory_hash_version,
            "digest": self.digest,
            "entry_count": len(self.entries),
            "entries": [entry.to_dict() for entry in self.entries],
            "excluded": list(self.excluded),
            # The root is recorded for diagnostics only and is not part of the digest.
            "root": self.root,
        }


def _is_excluded(relative: str, name: str, patterns: Sequence[str]) -> bool:
    """Whether a path is covered by an exclusion pattern.

    A pattern is matched against the full relative path, the basename, and **every path
    component**. The component check is the one that matters and was missing at first: a
    directory pattern like ``.git`` does not match the string ``.git/config``, so matching only
    on the full path and the basename silently included every file *inside* an excluded
    directory. That would have put ``.git/objects/...`` into a directory digest -- a digest of
    the object store rather than of the content.

    Matching a component also means ``*.pyc`` excludes a cache file at any depth, which is the
    other half of the intent.
    """
    components = relative.split("/")
    for pattern in patterns:
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern):
            return True
        if any(fnmatch.fnmatch(component, pattern) for component in components):
            return True
    return False


def _walk(root: Path, patterns: Sequence[str]) -> tuple[list[TreeEntry], list[str]]:
    entries: list[TreeEntry] = []
    excluded: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if _is_excluded(relative, path.name, patterns):
            excluded.append(relative)
            continue
        if path.is_symlink():
            # Symlinks are not followed and not covered. Following them would make the
            # digest depend on whatever the link resolved to at hash time, which is
            # neither stable nor part of this repository.
            excluded.append(f"{relative} (symlink not followed)")
            continue
        if path.is_dir():
            # Empty directories are not part of the census. Git does not track them,
            # so including them would make a digest taken on a working tree differ
            # from the same tree freshly cloned -- a digest of the checkout, not of
            # the content.
            if any(path.iterdir()):
                continue
            excluded.append(f"{relative} (empty directory not tracked by git)")
            continue
        if not path.is_file():
            excluded.append(f"{relative} (not a regular file)")
            continue
        entries.append(TreeEntry(relative, path.stat().st_size, content_hash(path)))
    return entries, sorted(excluded)


def directory_hash(
    root: Path | str,
    *,
    exclude: Sequence[str] = (),
    include_only: Sequence[str] = (),
) -> DirectoryDigest:
    """Deterministic digest of every covered file under ``root``.

    Two trees produce the same digest when, and only when, they contain the same
    covered relative paths with the same bytes. Absolute location is irrelevant, which
    is what makes a digest taken on one machine comparable on another.
    """
    base = Path(root)
    if not base.is_dir():
        raise IntegrityError(f"directory_hash requires a directory: {base}")

    patterns = tuple(DEFAULT_DIRECTORY_EXCLUDES) + tuple(exclude)
    entries, excluded = _walk(base, patterns)
    if include_only:
        kept = [
            entry
            for entry in entries
            if _is_excluded(entry.path, Path(entry.path).name, include_only)
        ]
        dropped = [entry.path for entry in entries if entry not in kept]
        entries = kept
        excluded = sorted([*excluded, *(f"{path} (not in include_only)" for path in dropped)])

    payload = {
        "directory_hash_version": DIRECTORY_HASH_VERSION,
        "entries": [entry.to_dict() for entry in entries],
    }
    return DirectoryDigest(
        root=base.name,
        digest=canonical_json_hash(payload),
        entries=tuple(entries),
        excluded=tuple(excluded),
    )


def directory_hash_digest(root: Path | str, **kwargs: Any) -> str:
    """Just the digest, for callers that do not need the census."""
    return directory_hash(root, **kwargs).digest


# ── Verification helpers ─────────────────────────────────────────────────────


@dataclass
class HashCheck:
    """The outcome of checking one recorded digest against reality."""

    path: str
    expected: str | None
    actual: str | None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def verify_content_hash(
    path: Path | str,
    expected: str,
    *,
    label: str = "",
) -> HashCheck:
    """Compare a recorded digest against the bytes now at ``path``.

    Constructed so the three outcomes stay distinguishable in the message:

    * the file is gone -- the identity can no longer be checked at all;
    * the file is there with different bytes -- the identity moved;
    * the file matches.

    Collapsing the first into the third is the failure this function exists to make
    impossible.
    """
    target = Path(path)
    name = label or target.as_posix()
    if not target.exists():
        return HashCheck(
            path=str(target),
            expected=expected,
            actual=None,
            errors=[
                f"{name}: recorded digest {_short(expected)} has no artifact: {target} is missing"
            ],
        )
    if not target.is_file():
        return HashCheck(
            path=str(target),
            expected=expected,
            actual=None,
            errors=[f"{name}: recorded digest {_short(expected)} points at a non-file: {target}"],
        )
    try:
        actual = content_hash(target)
    except OSError as exc:
        return HashCheck(
            path=str(target),
            expected=expected,
            actual=None,
            errors=[f"{name}: artifact exists but is unreadable: {exc}"],
        )
    if actual != expected:
        return HashCheck(
            path=str(target),
            expected=expected,
            actual=actual,
            errors=[
                f"{name}: artifact changed: recorded {_short(expected)}, found {_short(actual)}"
            ],
        )
    return HashCheck(path=str(target), expected=expected, actual=actual)


def _short(digest: str | None) -> str:
    """First 12 hex characters, for a message a human can compare."""
    return "missing" if digest is None else f"{digest[:12]}…"


def iter_chunks(path: Path | str) -> Iterator[bytes]:
    """Yield a file's bytes in canonical chunk sizes.

    Exposed so tests can assert the streamed path produces the same digest as a
    single-shot hash of the whole payload, rather than trusting that it does.
    """
    with Path(path).open("rb") as handle:
        yield from iter(lambda: handle.read(STREAM_CHUNK_BYTES), b"")


__all__ = [
    "CANONICAL_JSON_VERSION",
    "DEFAULT_DIRECTORY_EXCLUDES",
    "DIRECTORY_HASH_VERSION",
    "IDENTITY_EXCLUDED_FIELD_NAMES",
    "MACHINE_LOCAL_FIELD_NAMES",
    "STREAM_CHUNK_BYTES",
    "VOLATILE_FIELD_NAMES",
    "DirectoryDigest",
    "HashCheck",
    "TreeEntry",
    "artifact_hash",
    "canonical_json_bytes",
    "canonical_json_hash",
    "canonical_json_text",
    "configuration_hash",
    "content_hash",
    "content_hash_or_none",
    "directory_hash",
    "directory_hash_digest",
    "hash_stream",
    "iter_chunks",
    "manifest_hash",
    "pretty_json_text",
    "record_identity",
    "selective_identity",
    "sha256_bytes",
    "strip_identity_excluded",
    "verify_content_hash",
]
