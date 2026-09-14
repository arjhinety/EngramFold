"""Schema versions, and the rule that an unknown version fails safely.

Every long-lived machine-readable format in this repository carries an explicit
version. This module is the single place that says which versions exist, so
"we forgot to bump the version" and "a newer writer produced this" cannot both look
like success.

The registry is deliberately a closed enumeration: adding a format means adding it
here, and ``tests/test_anti_patterns.py`` asserts that every ``*_version`` key the
repository actually writes is a key this module knows about. A format that is
written but not registered would otherwise never be version-checked at all.
"""

from __future__ import annotations

from typing import Any

from engramfold.errors import SchemaVersionError

# kind -> the versions this build can interpret.
#
# A kind absent from this mapping is not "unversioned but fine": writing such a
# document is a contract error, and the anti-pattern test fails on it.
SUPPORTED_SCHEMA_VERSIONS: dict[str, tuple[int, ...]] = {
    "dataset_manifest": (1,),
    "dataset_freeze": (1,),
    "experiment_manifest": (1,),
    "experiment_freeze": (1,),
    "artifact_manifest": (1,),
    "environment_manifest": (1,),
    "provenance_record": (1,),
    "registry": (1,),
    "populations": (1,),
    "gate_contract": (1,),
    # Digest payload formats. These are not documents a loader reads, but they are JSON this
    # repository writes and records, so they carry a version for the same reason the
    # document kinds do: a digest must be traceable to the rules that produced it. They were
    # missing until ``tests/test_anti_patterns.py`` mechanically asserted that every
    # ``*_version`` key written anywhere in the package is a key this module knows about --
    # a rule stated in this docstring that was, at the time, not quite true.
    "directory_digest": (1,),
}

# The key each kind records its version under. Stated once so a reader cannot pick a
# different field name and quietly escape the check.
VERSION_FIELD: dict[str, str] = {
    "dataset_manifest": "dataset_manifest_version",
    "dataset_freeze": "freeze_format_version",
    "experiment_manifest": "experiment_manifest_version",
    "experiment_freeze": "freeze_format_version",
    "artifact_manifest": "artifact_manifest_version",
    "environment_manifest": "environment_manifest_version",
    "provenance_record": "provenance_record_version",
    "registry": "schema_version",
    "populations": "schema_version",
    "gate_contract": "schema_version",
    "directory_digest": "directory_hash_version",
}


def version_field(kind: str) -> str:
    """The field name ``kind`` records its version under."""
    if kind not in VERSION_FIELD:
        raise SchemaVersionError(f"unregistered document kind: {kind!r}")
    return VERSION_FIELD[kind]


def check_schema_version(kind: str, document: dict[str, Any]) -> str | None:
    """Return an error string when ``document``'s version is not interpretable.

    Returns ``None`` when the version is present and supported. A missing version is
    an error and never a defaulted guess: defaulting is exactly how a document from a
    different era gets read as if it were from this one.
    """
    try:
        field = version_field(kind)
    except SchemaVersionError as exc:
        return str(exc)

    supported = SUPPORTED_SCHEMA_VERSIONS[kind]
    if field not in document:
        return (
            f"{kind}: missing required version field {field!r}; "
            f"this build supports {field} in {list(supported)}"
        )
    value = document[field]
    if isinstance(value, bool) or not isinstance(value, int):
        return f"{kind}: {field} must be an integer, found {type(value).__name__}"
    if value not in supported:
        newer = value > max(supported)
        direction = "newer than" if newer else "unknown to"
        return (
            f"{kind}: {field}={value} is {direction} the versions this build "
            f"interprets {list(supported)}; refusing to interpret it"
        )
    return None


def current_version(kind: str) -> int:
    """The newest version this build writes for ``kind``."""
    try:
        return max(SUPPORTED_SCHEMA_VERSIONS[kind])
    except KeyError as exc:  # pragma: no cover - defensive, covered by contract test
        raise SchemaVersionError(f"unregistered document kind: {kind!r}") from exc


__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "VERSION_FIELD",
    "check_schema_version",
    "current_version",
    "version_field",
]
