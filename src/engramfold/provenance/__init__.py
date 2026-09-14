"""Provenance: what code and what machine produced a result.

Two record kinds, both required before a result can be attributed to anything:

* :mod:`engramfold.provenance.git_state` -- commit, branch, dirty state, the modified
  files, a digest of the modification, and the last commit touching the relevant files;
* :mod:`engramfold.provenance.environment` -- operating system, interpreter, packages,
  accelerators, container identity, with secrets redacted by construction.

Both record deliberately. Neither decides. Whether a dirty tree or a different GPU
invalidates a comparison is a research judgment, and it is kept visible rather than
automated away: ``git_state.cleanliness_errors`` states the default (canonical work wants
a committed tree) and ``environment.compare_environments`` classifies differences without
judging them.
"""

from __future__ import annotations

from engramfold.provenance.environment import (
    ENVIRONMENT_MANIFEST_VERSION,
    EnvironmentComparison,
    EnvironmentManifest,
    capture_environment,
    capture_environment_variables,
    compare_environments,
    environment_identity,
    is_secret_shaped_name,
    looks_like_secret_value,
    redact_environment_variables,
)
from engramfold.provenance.git_state import (
    DETACHED_HEAD,
    PROVENANCE_RECORD_VERSION,
    GitState,
    capture_git_state,
    cleanliness_errors,
    is_git_work_tree,
    last_commit_for_paths,
    per_file_history,
    provenance_record,
    short_commit,
)

__all__ = [
    "DETACHED_HEAD",
    "ENVIRONMENT_MANIFEST_VERSION",
    "PROVENANCE_RECORD_VERSION",
    "EnvironmentComparison",
    "EnvironmentManifest",
    "GitState",
    "capture_environment",
    "capture_environment_variables",
    "capture_git_state",
    "cleanliness_errors",
    "compare_environments",
    "environment_identity",
    "is_git_work_tree",
    "is_secret_shaped_name",
    "last_commit_for_paths",
    "looks_like_secret_value",
    "per_file_history",
    "provenance_record",
    "redact_environment_variables",
    "short_commit",
]
