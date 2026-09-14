"""Error types, split by whether the failure is absence or corruption.

The distinction is the whole point of this module. A missing optional input and an
unreadable or malformed input must never collapse into the same handling, because
"the manifest is not there" and "the manifest is there and I could not understand
it" are different facts about the world. A validator that treats the second as the
first reports PASS while having silently discarded the thing it was asked to check.

So there is no generic "artifact is missing" error reused for both. Absence is a
state, not an exception (see ``engramfold.documents``); corruption is always an
exception that must reach the caller.
"""

from __future__ import annotations


class EngramFoldError(Exception):
    """Base class for every error this package raises deliberately."""


class IntegrityError(EngramFoldError):
    """A recorded identity does not match the bytes it claims to be the identity of."""


class SchemaVersionError(EngramFoldError):
    """A document declares a schema version this build does not know.

    Raised rather than guessed at. Interpreting a newer schema with an older reader
    is how a field silently changes meaning, so an unknown version always fails.
    """


class DocumentError(EngramFoldError):
    """A document exists but cannot be used.

    Never raised for absence. If this is raised, something was found and rejected.
    """


class MalformedDocumentError(DocumentError):
    """A document exists but does not parse."""


class UnreadableDocumentError(DocumentError):
    """A document exists but could not be read (permissions, I/O, encoding)."""


class ValidationContractError(EngramFoldError):
    """A validation gate was constructed or invoked inconsistently.

    This is a defect in the repository's own machinery rather than in the data, and
    it is kept separate so a gate bug cannot be misread as a research finding.
    """


class CommandError(EngramFoldError):
    """A CLI command could not be carried out as asked."""
