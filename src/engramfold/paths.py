"""Repository location, resolved one way for every entry point.

Both the CLI and the validator need to answer "which repository am I looking at?", and they
must answer it the same way. When they did not, ``python -m engramfold.registry.validate`` run
from a fixture directory silently validated *the installed package's own repository* instead --
a validation that passed, reported eight gates, and had nothing to do with the directory the
operator was standing in.

Resolution walks up from the working directory looking for a marker. Falling back to the
working directory rather than to the package's install location is deliberate: a validator that
silently reports on a different repository than the one you are in is worse than one that
reports the canonical documents are missing.
"""

from __future__ import annotations

from pathlib import Path

#: Files that mark a repository root.
ROOT_MARKERS: tuple[str, ...] = ("pyproject.toml", ".git")


def find_repo_root(start: Path | None = None) -> Path:
    """The nearest ancestor of ``start`` (default: the working directory) containing a marker.

    Falls back to ``start`` itself. A caller running outside a checkout then gets a validation
    *failure* explaining that the canonical documents are missing -- a statement about the
    directory it was actually pointed at -- rather than a usage error that says nothing.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    return current


__all__ = ["ROOT_MARKERS", "find_repo_root"]
