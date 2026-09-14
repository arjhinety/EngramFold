"""Canonical document loading, with absence and corruption held apart.

Every validator in this repository reads documents through this module, so that the
distinction between "there is nothing here" and "there is something here that I could
not use" is made once, in one place, and cannot be lost by a caller.

Why this module exists
----------------------

The failure it prevents is specific and was observed: a freeze check read an artifact
path out of a field name that the canonical records did not use, so every target
resolved to ``None``, every target was treated as "not applicable", and the gate
reported PASS having examined nothing. Two ingredients made that possible -- a lookup
that could not distinguish a wrong field name from a legitimately absent value, and a
caller permitted to read ``None`` as success.

So a lookup here returns a :class:`DocumentLoad` whose ``state`` is one of:

``PRESENT``
    Found, parsed, and schema-version-checked. ``value`` is usable.
``ABSENT``
    The path genuinely does not exist. Whether that is acceptable is the *caller's*
    policy decision, and it must be made explicitly; ``absent_ok`` is not a default.
``UNREADABLE``
    The path exists and could not be read. Always an error.
``MALFORMED``
    The path exists, was read, and does not parse. Always an error.
``UNKNOWN_SCHEMA``
    Parsed, but declares a version this build does not interpret. Always an error.

The last three are *never* absence. ``DocumentLoad.is_error`` is true for exactly
those three, and callers are expected to treat any ``requires_absence_or_content``
lookup as failed when ``is_error`` holds, regardless of how they handle ``ABSENT``.

There is also a strictness rule for discovery: :func:`discover` returns both the
documents it found and the errors it hit, and a caller that iterates only the
documents while ignoring the errors is detectable, because the error count is part of
the returned structure and the validation gates assert on it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from engramfold.errors import MalformedDocumentError, UnreadableDocumentError
from engramfold.schemas import check_schema_version


class DocumentState(StrEnum):
    """What happened when a document was looked up."""

    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    UNREADABLE = "UNREADABLE"
    MALFORMED = "MALFORMED"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"


#: States that always constitute a failure, whatever the caller's policy on absence.
HARD_ERROR_STATES: frozenset[DocumentState] = frozenset(
    {DocumentState.UNREADABLE, DocumentState.MALFORMED, DocumentState.UNKNOWN_SCHEMA}
)


@dataclass(frozen=True)
class DocumentLoad:
    """The result of loading one document, including how it failed if it did."""

    path: str
    state: DocumentState
    value: dict[str, Any] | None = None
    error: str | None = None
    #: The raw text, when it was read. Kept so a caller can hash it or show it.
    text: str | None = None

    @property
    def is_present(self) -> bool:
        return self.state is DocumentState.PRESENT

    @property
    def is_absent(self) -> bool:
        return self.state is DocumentState.ABSENT

    @property
    def is_error(self) -> bool:
        """True for everything that is not PRESENT and not a clean ABSENT."""
        return self.state in HARD_ERROR_STATES

    def errors(self, *, label: str | None = None) -> list[str]:
        """This load as a list of error strings (empty when usable or cleanly absent)."""
        if not self.is_error:
            return []
        name = label or self.path
        return [f"{name}: {self.error or self.state.value}"]

    def require(self, *, label: str | None = None) -> dict[str, Any]:
        """Return the parsed value, or raise.

        Used where a caller has already established that absence is unacceptable. The
        exception keeps a malformed document from travelling on as ``None``.
        """
        if self.state is DocumentState.MALFORMED:
            raise MalformedDocumentError(f"{label or self.path}: {self.error}")
        if self.state is DocumentState.UNREADABLE:
            raise UnreadableDocumentError(f"{label or self.path}: {self.error}")
        if self.state is not DocumentState.PRESENT or self.value is None:
            raise UnreadableDocumentError(
                f"{label or self.path}: document is {self.state.value}, not usable content"
            )
        return self.value


def _parse_json(text: str, path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"does not parse as JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, f"must be a JSON object at the top level, found {type(parsed).__name__}"
    return parsed, None


def _parse_yaml(text: str, path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        import yaml
    except ImportError:
        # Not absence. The document may be perfectly good; this build simply cannot
        # read the format, and saying so is the honest outcome.
        return None, "PyYAML is not installed, so this YAML document cannot be read"
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, f"does not parse as YAML: {exc}"
    if parsed is None:
        return None, "is empty (parses to null), which is not a document"
    if not isinstance(parsed, dict):
        return None, f"must be a mapping at the top level, found {type(parsed).__name__}"
    return parsed, None


_PARSERS = {".json": _parse_json, ".yaml": _parse_yaml, ".yml": _parse_yaml}


def _read_text(path: Path) -> tuple[str | None, str | None]:
    """Read a text file, distinguishing unreadable from absent."""
    if not path.exists():
        return None, None
    if not path.is_file():
        return None, "exists but is not a regular file"
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"exists but could not be read as UTF-8: {exc}"


def load_text_document(path: Path | str) -> DocumentLoad:
    """Load any text file without interpreting it.

    For documents whose content is prose rather than a structured record -- ``README.md``,
    ``LICENSE``, ``pyproject.toml`` -- there is no schema to check, so parsing them would
    manufacture a failure where there is none. What still matters is the distinction this
    module exists for: a file that is *missing* is ``ABSENT``, while a file that is present
    and *unreadable* is ``UNREADABLE`` and therefore an error.

    Emptiness is returned as ``PRESENT`` with empty text rather than as an error, because
    whether an empty file is acceptable is the caller's judgement; the caller can see
    ``text`` and decide.
    """
    target = Path(path)
    text, problem = _read_text(target)
    if text is None and problem is None:
        return DocumentLoad(path=target.as_posix(), state=DocumentState.ABSENT)
    if text is None:
        return DocumentLoad(path=target.as_posix(), state=DocumentState.UNREADABLE, error=problem)
    return DocumentLoad(path=target.as_posix(), state=DocumentState.PRESENT, text=text)


def _describe_non_file(target: Path) -> str:
    """What kind of non-file was found, for an error message."""
    if target.is_symlink():
        return "symlink"
    if target.is_dir():
        return "directory"
    return "special file"


def load_document(path: Path | str, *, kind: str | None = None) -> DocumentLoad:
    """Read and validate one document, reporting *how* it failed.

    ``kind``, when given, additionally enforces the schema version contract from
    ``engramfold.schemas``. Passing ``kind`` is strongly preferred: without it the
    version field is not checked and a document from an incompatible era would be
    accepted.
    """
    target = Path(path)
    as_string = target.as_posix()

    if not target.exists():
        return DocumentLoad(path=as_string, state=DocumentState.ABSENT)
    if not target.is_file():
        return DocumentLoad(
            path=as_string,
            state=DocumentState.UNREADABLE,
            error=(
                f"exists but is not a regular file (found {_describe_non_file(target)}), so it "
                f"cannot be read as a document"
            ),
        )

    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return DocumentLoad(
            path=as_string,
            state=DocumentState.UNREADABLE,
            error=f"exists but could not be read as UTF-8: {exc}",
        )

    suffix = target.suffix.lower()
    parser = _PARSERS.get(suffix)
    if parser is None:
        return DocumentLoad(
            path=as_string,
            state=DocumentState.MALFORMED,
            text=text,
            error=(
                f"has extension {suffix or '<none>'!r}, which this build has no parser for; "
                f"supported: {sorted(_PARSERS)}"
            ),
        )

    parsed, problem = parser(text, target)
    if problem is not None or parsed is None:
        return DocumentLoad(path=as_string, state=DocumentState.MALFORMED, text=text, error=problem)

    if kind is not None:
        version_error = check_schema_version(kind, parsed)
        if version_error is not None:
            return DocumentLoad(
                path=as_string,
                state=DocumentState.UNKNOWN_SCHEMA,
                text=text,
                value=parsed,
                error=version_error,
            )

    return DocumentLoad(path=as_string, state=DocumentState.PRESENT, text=text, value=parsed)


@dataclass
class Discovery:
    """A discovered population together with the failures met while discovering it.

    Deliberately not a bare list. A caller cannot obtain the population without also
    obtaining the errors, and the validation gates count both, so "I found nothing"
    and "I could not look" cannot be reported as the same thing.
    """

    pattern: str
    loads: list[DocumentLoad] = field(default_factory=list)
    #: Paths that matched the pattern but were rejected as unusable.
    rejected: list[DocumentLoad] = field(default_factory=list)
    #: Directories that exist but are empty of matching documents.
    empty_directories: list[str] = field(default_factory=list)

    @property
    def present(self) -> list[DocumentLoad]:
        return [load for load in self.loads if load.is_present]

    @property
    def errors(self) -> list[str]:
        return [error for load in self.rejected for error in load.errors()]

    @property
    def discovered_count(self) -> int:
        return len(self.present)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    def render(self) -> str:
        return (
            f"{self.pattern}: {self.discovered_count} usable, "
            f"{self.rejected_count} rejected, {len(self.empty_directories)} empty dir(s)"
        )


def discover(
    root: Path | str,
    pattern: str,
    *,
    kind: str | None = None,
    require_directory: bool = False,
) -> Discovery:
    """Find every document matching ``pattern`` under ``root``.

    A matched file that is malformed, unreadable, or version-incompatible goes into
    ``rejected`` and never into ``loads``. It is therefore impossible for a caller to
    get a shorter-than-expected list without a corresponding non-empty error list --
    which is the accounting that makes a silently-dropped manifest detectable.

    ``require_directory`` turns a missing containing directory into a recorded
    ``empty_directories`` entry rather than a silent zero. A canonical directory that
    has been deleted is a different fact from one that has simply never been
    populated, and the gate should be able to say which.
    """
    base = Path(root)
    matches = sorted(base.glob(pattern))
    directory = base / Path(pattern).parts[0] if Path(pattern).parts else base
    result = Discovery(pattern=pattern)
    if require_directory and not directory.is_dir():
        result.empty_directories.append(directory.as_posix())

    for match in matches:
        if not match.is_file():
            continue
        load = load_document(match, kind=kind)
        if load.is_present:
            result.loads.append(load)
        elif load.is_absent:  # pragma: no cover - glob only yields existing paths
            continue
        else:
            result.rejected.append(load)
    return result


def verify_all_present(loads: Iterable[DocumentLoad]) -> list[str]:
    """Errors for any load that is not cleanly present.

    For callers that have already decided every one of these documents must exist, so
    that a missing file and a corrupt file both surface as errors rather than as an
    empty loop.
    """
    errors: list[str] = []
    for load in loads:
        if load.is_error:
            errors.extend(load.errors())
        elif load.is_absent:
            errors.append(f"{load.path}: required document is missing")
    return errors


def missing_expected(
    root: Path | str,
    expected: Sequence[str],
    *,
    kind: str | None = None,
) -> list[DocumentLoad]:
    """Load each path in ``expected``, returning the ones that are not usable.

    Used to assert the repository's declared canonical files exist. A declaration list
    that is silently empty is itself an error, which the caller checks separately --
    this function will happily return ``[]`` for an empty ``expected``, and the
    caller must not read that as success.
    """
    base = Path(root)
    results: list[DocumentLoad] = []
    for relative in expected:
        load = load_document(base / relative, kind=kind)
        if not load.is_present:
            results.append(load)
    return results


__all__ = [
    "HARD_ERROR_STATES",
    "Discovery",
    "DocumentLoad",
    "DocumentState",
    "discover",
    "load_document",
    "load_text_document",
    "missing_expected",
    "verify_all_present",
]
