"""Source revision provenance: what code existed when an artifact was produced.

``git HEAD`` alone does not answer that question. A checkout at commit ``abc123`` with
four tracked files modified in the working tree is *not* the state ``abc123``
describes, and an artifact produced from it cannot be attributed to ``abc123``. Any
provenance mechanism that records only a commit hash silently attributes dirty work to
a clean commit -- which is worse than recording nothing, because it looks complete.

So this module records, together:

``git_commit``
    The commit the checkout is at.
``git_branch``
    The branch, or a detached-HEAD marker.
``git_dirty``
    Whether any *tracked* file differs from that commit.
``dirty_file_list``
    Which tracked paths differ.
``dirty_diff_hash``
    A digest over the modified content itself, so two different dirty states at the
    same commit are distinguishable, and so restoring the files returns the value to
    the clean constant.
``last_commit_affecting_relevant_file`` / ``last_commit_timestamp``
    The most recent commit touching the files this artifact depends on. Answers "which
    change last touched the code that made this?", which a repository-wide HEAD cannot.

Three deliberate decisions
--------------------------

**Untracked files do not make a tree dirty.** Every run creates untracked outputs --
its own manifests, logs, artifacts. If ``git status`` as a whole decided cleanliness,
then every successful run would report as dirty and "produced from a clean checkout"
would be unsatisfiable. What matters is whether the tracked code and configuration
matched a known commit. Untracked paths are recorded separately, so the fact is not
hidden, only excluded from the cleanliness verdict.

**Fails closed.** When git cannot be queried, ``dirty`` is ``None`` -- unknown, never
``False``. An unreadable repository can therefore never be presented as clean, and
:func:`cleanliness_errors` treats unknown as an error.

**Hashing modified content uses committed-blob semantics for the baseline.** The
recorded ``dirty_diff_hash`` is taken over the working file's bytes against the
committed blob's bytes, so the same modification yields the same digest on any
platform regardless of how the checkout normalised line endings.

This module records ``source_revision``. That is a *pointer into history*, not a
content digest, and it is deliberately never stored in a hash-shaped field -- see
``docs/PROVENANCE.md``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engramfold.hashing import canonical_json_hash, content_hash_or_none, sha256_bytes

#: Recorded so a reader can tell which construction rules produced a provenance record.
PROVENANCE_RECORD_VERSION = 1

#: Recorded when the checkout is not on a branch.
DETACHED_HEAD = "<detached HEAD>"

_GIT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class GitState:
    """The provenance of a checkout, with unknown held apart from clean."""

    available: bool
    commit: str | None = None
    branch: str | None = None
    #: ``None`` means "could not be determined", which is never treated as clean.
    dirty: bool | None = None
    dirty_file_list: tuple[str, ...] = ()
    dirty_diff_hash: str | None = None
    untracked_file_list: tuple[str, ...] = ()
    head_committed_at: str | None = None
    error: str | None = None

    @property
    def clean(self) -> bool:
        """True only when git answered *and* said the tracked tree matches HEAD."""
        return self.available and self.dirty is False

    @property
    def known(self) -> bool:
        return self.dirty is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provenance_record_version": PROVENANCE_RECORD_VERSION,
            "git_available": self.available,
            "git_commit": self.commit,
            "git_branch": self.branch,
            "git_dirty": self.dirty,
            "dirty_file_list": list(self.dirty_file_list),
            "dirty_diff_hash": self.dirty_diff_hash,
            "untracked_file_list": list(self.untracked_file_list),
            "head_committed_at": self.head_committed_at,
            "error": self.error,
        }


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run git, never raising for a non-zero exit.

    ``check=False`` is intentional: a non-zero exit is information (the path is not
    tracked, the file has no history) and is handled by the caller. Genuine inability
    to run git at all raises ``OSError``, which :func:`capture_git_state` converts into
    a fail-closed state rather than an exception, because provenance capture must not
    crash a run -- it must record that it could not tell.
    """
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _decode(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")


def is_git_work_tree(root: Path | str) -> bool:
    """Whether ``root`` is inside a git work tree."""
    try:
        result = _run_git(Path(root), "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == b"true"


def _dirty_entries(root: Path, porcelain: str) -> list[dict[str, Any]]:
    """The modified tracked paths, with the digest of each side of the change.

    The committed side is hashed from the git object store, so it does not depend on
    how this checkout normalised line endings; the working side is hashed from disk.
    Together they make the dirty state a digest of the modification rather than of the
    platform.
    """
    entries: list[dict[str, Any]] = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].strip()
        if " -> " in path:  # a rename reports both sides
            path = path.split(" -> ", 1)[1].strip()
        path = path.strip('"')
        committed: str | None = None
        blob = _run_git(root, "cat-file", "blob", f"HEAD:{path}")
        if blob.returncode == 0:
            committed = sha256_bytes(blob.stdout)
        entries.append(
            {
                "path": path,
                "status": status,
                "head_sha256": committed,
                "worktree_sha256": content_hash_or_none(root / path),
            }
        )
    return sorted(entries, key=lambda entry: str(entry["path"]))


def capture_git_state(
    root: Path | str,
    *,
    relevant_paths: list[str] | None = None,
) -> GitState:
    """Record the provenance of the checkout at ``root``.

    Returns a fail-closed :class:`GitState` rather than raising when git is missing or
    the directory is not a repository: the caller then has an explicit "unknown" to
    handle, which is the honest outcome.
    """
    base = Path(root)
    try:
        head = _run_git(base, "rev-parse", "HEAD")
        if head.returncode != 0:
            reason = _decode(head.stderr).strip() or "rev-parse HEAD failed"
            return GitState(available=False, error=f"not a usable git checkout: {reason}")

        commit = _decode(head.stdout).strip() or None
        branch_result = _run_git(base, "rev-parse", "--abbrev-ref", "HEAD")
        branch = _decode(branch_result.stdout).strip() or None
        if branch == "HEAD":
            branch = DETACHED_HEAD

        committed_at_result = _run_git(base, "log", "-1", "--format=%cI", "HEAD")
        committed_at = _decode(committed_at_result.stdout).strip() or None

        # Tracked-only status: see the module docstring for why untracked outputs must
        # not be allowed to make every run look dirty.
        tracked = _run_git(base, "status", "--porcelain", "--untracked-files=no")
        if tracked.returncode != 0:
            return GitState(
                available=True,
                commit=commit,
                branch=branch,
                dirty=None,
                head_committed_at=committed_at,
                error=f"could not read tracked status: {_decode(tracked.stderr).strip()}",
            )
        porcelain = _decode(tracked.stdout)
        entries = _dirty_entries(base, porcelain)

        untracked = _run_git(base, "ls-files", "--others", "--exclude-standard")
        untracked_paths: tuple[str, ...] = ()
        if untracked.returncode == 0:
            untracked_paths = tuple(
                sorted(line for line in _decode(untracked.stdout).splitlines() if line.strip())
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return GitState(available=False, error=f"git could not be executed: {exc}")

    return GitState(
        available=True,
        commit=commit,
        branch=branch,
        dirty=bool(entries),
        dirty_file_list=tuple(str(entry["path"]) for entry in entries),
        dirty_diff_hash=canonical_json_hash({"dirty": entries}),
        untracked_file_list=untracked_paths,
        head_committed_at=committed_at,
    )


def last_commit_for_paths(
    root: Path | str, paths: list[str] | None
) -> tuple[str | None, str | None]:
    """The most recent commit touching any of ``paths``, and its committer timestamp.

    With no paths, this is the HEAD commit. With paths, it is the last commit that
    changed the code or configuration the caller depends on -- which is what "which
    change last touched this?" actually asks.
    """
    base = Path(root)
    args = ["log", "-1", "--format=%H%x00%cI"]
    if paths:
        args += ["--", *paths]
    try:
        result = _run_git(base, *args)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if result.returncode != 0:
        return None, None
    parts = _decode(result.stdout).strip().split("\x00")
    if len(parts) != 2:
        return None, None
    return parts[0] or None, parts[1] or None


def provenance_record(
    root: Path | str,
    *,
    relevant_paths: list[str] | None = None,
    captured_at: str | None = None,
    dirty_override_reason: str | None = None,
) -> dict[str, Any]:
    """A complete, recordable provenance document for the current checkout.

    ``captured_at`` is recorded but excluded from every content identity (see
    ``engramfold.hashing``), which is what lets two captures of an unchanged checkout
    be recognised as the same state while still saying when each happened.

    ``dirty_override_reason`` does **not** clear the dirty flag. It is recorded
    alongside it. An override that hid the dirty state would defeat the entire point
    of recording it, so the flag and the file list are always written truthfully.
    """
    base = Path(root)
    state = capture_git_state(base)
    last_commit, last_timestamp = last_commit_for_paths(
        base, relevant_paths if relevant_paths is not None else None
    )
    record: dict[str, Any] = {
        "provenance_record_version": PROVENANCE_RECORD_VERSION,
        "captured_at": captured_at,
        "git_available": state.available,
        "git_commit": state.commit,
        "git_branch": state.branch,
        "git_dirty": state.dirty,
        "dirty_file_list": list(state.dirty_file_list),
        "dirty_diff_hash": state.dirty_diff_hash,
        "untracked_file_list": list(state.untracked_file_list),
        "head_committed_at": state.head_committed_at,
        "last_commit_affecting_relevant_file": last_commit,
        "last_commit_timestamp": last_timestamp,
        "relevant_paths": list(relevant_paths or []),
        "dirty_override_reason": dirty_override_reason.strip() if dirty_override_reason else None,
        "error": state.error,
    }
    return record


def cleanliness_errors(
    state: GitState,
    *,
    subject: str,
    dirty_override_reason: str | None = None,
) -> list[str]:
    """Whether ``state`` is acceptable for producing a canonical artifact.

    The rules, and why each is what it is:

    * Git could not be queried -> error. There is no way to establish that the code was
      committed, so it must not be asserted.
    * The tree is dirty and no override was given -> error. This is the default for
      canonical work: an artifact must be attributable to a commit.
    * The tree is dirty with a blank override -> error. An override is a written
      justification, not a flag.
    * The tree is dirty with a written override -> permitted, and the dirty state is
      still recorded in full by :func:`provenance_record`.
    """
    errors: list[str] = []
    if state.available and not state.known:
        errors.append(
            f"{subject}: provenance is unknowable: git answered but its tracked status "
            f"could not be read ({state.error or 'no detail'})"
        )
        return errors
    if not state.available:
        errors.append(
            f"{subject}: provenance is unknowable: {state.error or 'not a git checkout'}; "
            f"a result produced here cannot be attributed to any commit"
        )
        return errors
    if state.dirty:
        if dirty_override_reason is None:
            errors.append(
                f"{subject}: repository is dirty at {short_commit(state.commit)} with "
                f"{len(state.dirty_file_list)} modified tracked file(s) "
                f"({', '.join(state.dirty_file_list[:5])}"
                f"{'…' if len(state.dirty_file_list) > 5 else ''}); commit the change or "
                f"record an explicit dirty override"
            )
        elif not dirty_override_reason.strip():
            errors.append(
                f"{subject}: dirty override was given but is blank; an override must state "
                f"why dirty code is acceptable here"
            )
    return errors


def short_commit(commit: str | None) -> str:
    """First 12 characters of a commit, or a marker when there is none."""
    return "unknown" if not commit else commit[:12]


@dataclass
class RelevantFileHistory:
    """The last commit touching each relevant file, for per-file attribution."""

    entries: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            path: {"commit": commit, "committed_at": timestamp}
            for path, (commit, timestamp) in sorted(self.entries.items())
        }


def per_file_history(root: Path | str, paths: list[str]) -> RelevantFileHistory:
    """Last commit and timestamp for each path, individually.

    An empty ``paths`` list returns an empty history, and callers must not read that as
    "all files are unchanged" -- which is why the returned object is a mapping a reader
    can see is empty rather than a boolean.
    """
    result = RelevantFileHistory()
    for path in paths:
        result.entries[path] = last_commit_for_paths(root, [path])
    return result


__all__ = [
    "DETACHED_HEAD",
    "PROVENANCE_RECORD_VERSION",
    "GitState",
    "RelevantFileHistory",
    "capture_git_state",
    "cleanliness_errors",
    "is_git_work_tree",
    "last_commit_for_paths",
    "per_file_history",
    "provenance_record",
    "short_commit",
]
