"""Git provenance: the three-state test, run against real git repositories.

The states that must never be confused:

* a committed tree with no modifications -> **clean**;
* a committed tree with a tracked file modified -> **dirty**, and the provenance changes;
* the file restored -> **clean again**, with the same provenance as before;
* a repository whose HEAD is unborn, or git absent -> **unknowable**, which is never clean.

These use real ``git`` subprocesses rather than a stub, because the property being tested is
about what git actually reports.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.conftest import git, init_git_repo, write_text

from engramfold.provenance.git_state import (
    DETACHED_HEAD,
    PROVENANCE_RECORD_VERSION,
    capture_git_state,
    cleanliness_errors,
    is_git_work_tree,
    last_commit_for_paths,
    per_file_history,
    provenance_record,
    short_commit,
)

pytestmark = pytest.mark.gitslow


def commit_all(root: Path, message: str = "update") -> None:
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", message, cwd=root)


# ── The three-state test ─────────────────────────────────────────────────────


def test_a_clean_repository_reports_clean(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    state = capture_git_state(root)
    assert state.available
    assert state.dirty is False
    assert state.clean
    assert state.commit and len(state.commit) == 40


def test_modifying_a_tracked_file_changes_the_provenance(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "tracked.txt", "original\n")
    commit_all(root)

    clean = capture_git_state(root)
    write_text(root / "tracked.txt", "modified\n")
    dirty = capture_git_state(root)

    assert dirty.dirty is True
    assert dirty.dirty_diff_hash != clean.dirty_diff_hash
    assert dirty.dirty_file_list == ("tracked.txt",)


def test_restoring_a_file_returns_the_provenance_to_clean(tmp_path: Path):
    """A dirty state must not leave a permanent trace once it is undone."""
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "tracked.txt", "original\n")
    commit_all(root)

    clean = capture_git_state(root)
    write_text(root / "tracked.txt", "modified\n")
    changed = capture_git_state(root)
    write_text(root / "tracked.txt", "original\n")
    restored = capture_git_state(root)

    assert changed.dirty is True
    assert restored.dirty is False
    assert restored.dirty_file_list == ()
    assert restored.dirty_diff_hash == clean.dirty_diff_hash
    assert restored.commit == clean.commit


def test_two_different_modifications_have_different_dirty_hashes(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "tracked.txt", "original\n")
    commit_all(root)

    write_text(root / "tracked.txt", "one\n")
    first = capture_git_state(root)
    write_text(root / "tracked.txt", "two\n")
    second = capture_git_state(root)

    assert first.dirty_diff_hash != second.dirty_diff_hash


def test_a_second_modified_file_changes_the_dirty_hash(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "a.txt", "a\n")
    write_text(root / "b.txt", "b\n")
    commit_all(root)

    write_text(root / "a.txt", "changed\n")
    one = capture_git_state(root)
    write_text(root / "b.txt", "changed\n")
    two = capture_git_state(root)

    assert one.dirty_diff_hash != two.dirty_diff_hash
    assert set(two.dirty_file_list) == {"a.txt", "b.txt"}


# ── Untracked files ─────────────────────────────────────────────────────────


def test_untracked_files_are_recorded_but_do_not_make_a_tree_dirty(tmp_path: Path):
    """Every run creates untracked outputs, so they must not make cleanliness unsatisfiable."""
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "output.json", "{}\n")
    state = capture_git_state(root)

    assert state.dirty is False
    assert state.clean
    assert "output.json" in state.untracked_file_list


def test_untracked_files_are_visible_not_hidden(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "scratch.tmp", "x\n")
    assert "scratch.tmp" in capture_git_state(root).untracked_file_list


# ─ Failing closed ─────────────────────────────────────────────────────────


def test_an_unborn_head_is_unknowable_not_clean(tmp_path: Path):
    """A repository with no commit cannot establish anything, and must not read as clean."""
    root = init_git_repo(tmp_path / "repo", commit=False)
    state = capture_git_state(root)
    assert not state.available
    assert state.dirty is None
    assert not state.clean
    assert state.error


def test_a_non_repository_is_unknowable(tmp_path: Path):
    root = tmp_path / "plain"
    root.mkdir()
    state = capture_git_state(root)
    assert not state.available
    assert state.dirty is None
    assert not state.clean


def test_an_unknowable_state_cannot_be_treated_as_clean_by_the_cleanliness_check(tmp_path: Path):
    root = tmp_path / "plain"
    root.mkdir()
    errors = cleanliness_errors(capture_git_state(root), subject="fixture")
    assert errors
    assert any("unknowable" in error for error in errors)


def test_is_git_work_tree_is_false_outside_a_repository(tmp_path: Path):
    root = tmp_path / "plain"
    root.mkdir()
    assert is_git_work_tree(root) is False


def test_is_git_work_tree_is_true_inside_a_repository(tmp_path: Path):
    assert is_git_work_tree(init_git_repo(tmp_path / "repo")) is True


# ── Cleanliness policy ──────────────────────────────────────────────────────


def test_a_clean_tree_produces_no_cleanliness_errors(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    assert cleanliness_errors(capture_git_state(root), subject="fixture") == []


def test_a_dirty_tree_is_rejected_by_default(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "tracked.txt", "original\n")
    commit_all(root)
    write_text(root / "tracked.txt", "modified\n")

    errors = cleanliness_errors(capture_git_state(root), subject="artifact")
    assert errors
    assert any("dirty" in error for error in errors)
    assert any("tracked.txt" in error for error in errors)


def test_a_dirty_override_permits_the_run_but_is_still_required_to_be_written(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "tracked.txt", "original\n")
    commit_all(root)
    write_text(root / "tracked.txt", "modified\n")
    state = capture_git_state(root)

    assert cleanliness_errors(state, subject="a", dirty_override_reason="   ")
    assert (
        cleanliness_errors(
            state, subject="a", dirty_override_reason="exploratory run, recorded in full"
        )
        == []
    )


def test_the_override_never_clears_the_dirty_state(tmp_path: Path):
    """An override that hid the dirty state would defeat the point of recording it."""
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "tracked.txt", "original\n")
    commit_all(root)
    write_text(root / "tracked.txt", "modified\n")

    record = provenance_record(root, dirty_override_reason="exploratory")
    assert record["git_dirty"] is True
    assert record["dirty_file_list"] == ["tracked.txt"]
    assert record["dirty_override_reason"] == "exploratory"


# ── Provenance records ──────────────────────────────────────────────────────


def test_a_provenance_record_carries_every_required_field(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    record = provenance_record(root, relevant_paths=["pyproject.toml"])

    assert record["provenance_record_version"] == PROVENANCE_RECORD_VERSION
    for field in (
        "git_available",
        "git_commit",
        "git_branch",
        "git_dirty",
        "dirty_file_list",
        "dirty_diff_hash",
        "untracked_file_list",
        "head_committed_at",
        "last_commit_affecting_relevant_file",
        "last_commit_timestamp",
        "relevant_paths",
        "dirty_override_reason",
    ):
        assert field in record, field


def test_the_provenance_record_is_json_serialisable(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    json.dumps(provenance_record(root))


def test_the_recorded_commit_matches_git(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    expected = git("rev-parse", "HEAD", cwd=root).stdout.strip()
    assert provenance_record(root)["git_commit"] == expected


def test_the_branch_is_recorded(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    branch = provenance_record(root)["git_branch"]
    assert branch and branch != DETACHED_HEAD


def test_a_detached_head_is_labelled_as_such(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    git("checkout", "-q", "--detach", "HEAD", cwd=root)
    assert provenance_record(root)["git_branch"] == DETACHED_HEAD


def test_the_dirty_record_is_truthful(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "tracked.txt", "original\n")
    commit_all(root)
    write_text(root / "tracked.txt", "modified\n")

    record = provenance_record(root)
    assert record["git_dirty"] is True
    assert record["dirty_file_list"] == ["tracked.txt"]
    assert record["dirty_diff_hash"]


def test_the_relevant_paths_are_recorded(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    record = provenance_record(root, relevant_paths=["src/x.py", "registry"])
    assert record["relevant_paths"] == ["src/x.py", "registry"]


# ── Last commit affecting a path ────────────────────────────────────────────


def test_last_commit_for_a_path_that_never_changed_is_none(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    commit, timestamp = last_commit_for_paths(root, ["never-existed.txt"])
    assert commit is None
    assert timestamp is None


def test_last_commit_for_a_tracked_path_is_its_most_recent_change(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "file.txt", "one\n")
    commit_all(root, "first")
    first = git("rev-parse", "HEAD", cwd=root).stdout.strip()

    write_text(root / "file.txt", "two\n")
    commit_all(root, "second")
    second = git("rev-parse", "HEAD", cwd=root).stdout.strip()

    assert last_commit_for_paths(root, ["file.txt"])[0] == second
    assert last_commit_for_paths(root, ["file.txt"])[0] != first


def test_last_commit_for_a_path_is_not_the_head_when_other_files_changed(tmp_path: Path):
    """This is what a repository-wide HEAD cannot answer: which change last touched *this*."""
    root = init_git_repo(tmp_path / "repo")
    write_text(root / "code.py", "x = 1\n")
    commit_all(root, "touched code")
    code_commit = git("rev-parse", "HEAD", cwd=root).stdout.strip()

    write_text(root / "unrelated.txt", "y\n")
    commit_all(root, "touched something else")
    head = git("rev-parse", "HEAD", cwd=root).stdout.strip()

    assert head != code_commit
    assert last_commit_for_paths(root, ["code.py"])[0] == code_commit


def test_last_commit_with_no_paths_is_head(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    head = git("rev-parse", "HEAD", cwd=root).stdout.strip()
    assert last_commit_for_paths(root, None)[0] == head
    assert last_commit_for_paths(root, [])[0] == head


def test_per_file_history_is_a_mapping_a_reader_can_see_is_empty(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    empty = per_file_history(root, [])
    assert empty.entries == {}
    assert empty.to_dict() == {}

    populated = per_file_history(root, ["pyproject.toml"])
    assert set(populated.entries) == {"pyproject.toml"}


# ── Helpers ─────────────────────────────────────────────────────────────────


def test_short_commit_handles_none():
    assert short_commit(None) == "unknown"
    assert short_commit("a" * 40) == "a" * 12


def test_git_state_to_dict_is_complete_and_serialisable(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    payload = capture_git_state(root).to_dict()
    assert payload["provenance_record_version"] == PROVENANCE_RECORD_VERSION
    assert payload["git_available"] is True
    assert payload["git_dirty"] is False
    json.dumps(payload)


def test_an_unknowable_state_records_its_reason(tmp_path: Path):
    state = capture_git_state(tmp_path / "plain")
    payload = state.to_dict()
    assert payload["git_available"] is False
    assert payload["git_dirty"] is None
    assert payload["error"]


def test_capturing_a_large_dirty_set_is_deterministic(tmp_path: Path):
    root = init_git_repo(tmp_path / "repo")
    for index in range(12):
        write_text(root / f"f{index}.txt", f"{index}\n")
    commit_all(root)
    for index in range(12):
        write_text(root / f"f{index}.txt", f"changed {index}\n")

    first = capture_git_state(root)
    second = capture_git_state(root)
    assert first.dirty_diff_hash == second.dirty_diff_hash
    assert first.dirty_file_list == second.dirty_file_list
    assert len(first.dirty_file_list) == 12
