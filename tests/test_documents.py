"""Document loading: absence and corruption must never collapse.

This module is where the repository's most important distinction is tested. Every case here
is a way "the file is not there" could be confused with "the file is there and I could not
use it" -- and the second must always be an error, because a malformed manifest reported as
absent is a validation target that silently disappeared.
"""

from __future__ import annotations

from pathlib import Path

from engramfold.documents import (
    HARD_ERROR_STATES,
    DocumentState,
    discover,
    load_document,
    load_text_document,
    missing_expected,
    verify_all_present,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── State classification ─────────────────────────────────────────────────────


def test_absent_is_absent(tmp_path: Path):
    load = load_document(tmp_path / "nope.json")
    assert load.state is DocumentState.ABSENT
    assert load.is_absent
    assert not load.is_error


def test_malformed_is_not_absent(tmp_path: Path):
    """The core distinction. A broken document must never read as a missing one."""
    path = _write(tmp_path / "broken.json", "{not json at all")
    load = load_document(path)
    assert load.state is DocumentState.MALFORMED
    assert load.is_error
    assert not load.is_absent


def test_wrong_field_name_style_error_does_not_become_absence(tmp_path: Path):
    """A document that parses but is not an object is malformed, not missing."""
    path = _write(tmp_path / "list.json", "[1, 2, 3]")
    load = load_document(path)
    assert load.state is DocumentState.MALFORMED
    assert load.is_error


def test_empty_file_is_malformed_not_absent(tmp_path: Path):
    path = _write(tmp_path / "empty.yaml", "")
    load = load_document(path)
    assert load.state is DocumentState.MALFORMED
    assert load.is_error
    assert "empty" in (load.error or "")


def test_directory_is_unreadable_not_absent(tmp_path: Path):
    target = tmp_path / "adir"
    target.mkdir()
    load = load_document(target)
    assert load.state is DocumentState.UNREADABLE
    assert load.is_error
    assert not load.is_absent


def test_unsupported_extension_is_malformed(tmp_path: Path):
    path = _write(tmp_path / "thing.parquet", "binary-ish")
    load = load_document(path)
    assert load.state is DocumentState.MALFORMED
    assert load.is_error


def test_unknown_schema_version_is_its_own_state(tmp_path: Path):
    path = _write(
        tmp_path / "manifest.json",
        '{"dataset_manifest_version": 99, "dataset_id": "x"}',
    )
    load = load_document(path, kind="dataset_manifest")
    assert load.state is DocumentState.UNKNOWN_SCHEMA
    assert load.is_error
    assert "99" in (load.error or "")


def test_missing_version_field_is_unknown_schema(tmp_path: Path):
    path = _write(tmp_path / "manifest.json", '{"dataset_id": "x"}')
    load = load_document(path, kind="dataset_manifest")
    assert load.state is DocumentState.UNKNOWN_SCHEMA
    assert "missing required version field" in (load.error or "")


def test_supported_version_is_present(tmp_path: Path):
    path = _write(
        tmp_path / "manifest.json",
        '{"dataset_manifest_version": 1, "dataset_id": "x"}',
    )
    load = load_document(path, kind="dataset_manifest")
    assert load.state is DocumentState.PRESENT
    assert load.value == {"dataset_manifest_version": 1, "dataset_id": "x"}


def test_yaml_parses(tmp_path: Path):
    path = _write(tmp_path / "reg.yaml", "schema_version: 1\ndatasets: []\n")
    load = load_document(path, kind="registry")
    assert load.state is DocumentState.PRESENT


def test_hard_error_states_are_exactly_the_three_corruption_states():
    """Pins the definition, so a future state cannot be added without deciding its class."""
    assert {
        DocumentState.UNREADABLE,
        DocumentState.MALFORMED,
        DocumentState.UNKNOWN_SCHEMA,
    } == HARD_ERROR_STATES
    assert DocumentState.ABSENT not in HARD_ERROR_STATES
    assert DocumentState.PRESENT not in HARD_ERROR_STATES


def test_errors_are_empty_for_a_clean_absence(tmp_path: Path):
    """Absence yields no error *from the loader*; the caller decides whether it matters."""
    assert load_document(tmp_path / "nope.json").errors() == []


def test_errors_are_populated_for_corruption(tmp_path: Path):
    path = _write(tmp_path / "broken.json", "{{{")
    errors = load_document(path).errors()
    assert len(errors) == 1
    assert "broken.json" in errors[0]


def test_require_raises_on_corruption_and_absence(tmp_path: Path):
    import pytest

    from engramfold.errors import MalformedDocumentError, UnreadableDocumentError

    broken = _write(tmp_path / "broken.json", "{{{")
    with pytest.raises(MalformedDocumentError):
        load_document(broken).require()
    with pytest.raises(UnreadableDocumentError):
        load_document(tmp_path / "absent.json").require()


# ── Text documents ───────────────────────────────────────────────────────────


def test_text_document_loads_prose_without_a_parser(tmp_path: Path):
    """A README has no schema, so parsing it would manufacture a failure where none exists."""
    path = _write(tmp_path / "README.md", "# Title\n\nBody.\n")
    load = load_text_document(path)
    assert load.state is DocumentState.PRESENT
    assert load.text is not None and "Body." in load.text


def test_text_document_absent_vs_unreadable(tmp_path: Path):
    assert load_text_document(tmp_path / "gone.md").is_absent
    target = tmp_path / "dir"
    target.mkdir()
    assert load_text_document(target).state is DocumentState.UNREADABLE


def test_text_document_reports_emptiness_as_present(tmp_path: Path):
    """Emptiness is the caller's judgement, so the loader surfaces the text and stops."""
    path = _write(tmp_path / "empty.md", "")
    load = load_text_document(path)
    assert load.state is DocumentState.PRESENT
    assert load.text == ""


# ── Discovery accounting ─────────────────────────────────────────────────────


def test_discovery_counts_only_usable_documents(tmp_path: Path):
    _write(tmp_path / "datasets/manifests/good.json", '{"dataset_manifest_version": 1}')
    _write(tmp_path / "datasets/manifests/bad.json", "{{ not json")
    found = discover(tmp_path, "datasets/manifests/*.json", kind="dataset_manifest")
    assert found.discovered_count == 1
    assert found.rejected_count == 1
    assert len(found.errors) == 1
    assert "bad.json" in found.errors[0]


def test_zero_discovered_with_zero_rejected_is_a_clean_empty(tmp_path: Path):
    (tmp_path / "datasets/manifests").mkdir(parents=True)
    found = discover(tmp_path, "datasets/manifests/*.json", kind="dataset_manifest")
    assert found.discovered_count == 0
    assert found.rejected_count == 0
    assert found.errors == []


def test_discovery_errors_are_not_obtainable_without_the_population(tmp_path: Path):
    """The return type couples them: a caller cannot see the population and miss the errors.

    Asserted on an instance, because the derived properties are what a caller uses, and the
    coupling only exists if they are computed from the same object that holds the rejected
    documents.
    """
    from engramfold.documents import Discovery

    empty = Discovery(pattern="x")
    assert empty.discovered_count == 0
    assert empty.rejected_count == 0
    assert empty.errors == []

    broken = _write(tmp_path / "bad.json", "{{{")
    rejected = Discovery(pattern="x", rejected=[load_document(broken)])
    assert rejected.rejected_count == 1
    assert rejected.discovered_count == 0
    assert len(rejected.errors) == 1
    assert "bad.json" in rejected.errors[0]


def test_require_directory_records_a_missing_directory(tmp_path: Path):
    found = discover(
        tmp_path,
        "datasets/manifests/*.json",
        kind="dataset_manifest",
        require_directory=True,
    )
    assert found.discovered_count == 0
    assert len(found.empty_directories) == 1
    assert "datasets" in found.empty_directories[0]


def test_discovery_render_summarises_the_census(tmp_path: Path):
    _write(tmp_path / "datasets/manifests/ok.json", '{"dataset_manifest_version": 1}')
    _write(tmp_path / "datasets/manifests/no.json", "nope")
    text = discover(tmp_path, "datasets/manifests/*.json", kind="dataset_manifest").render()
    assert "1 usable" in text
    assert "1 rejected" in text


# ── Bulk helpers ─────────────────────────────────────────────────────────────


def test_verify_all_present_flags_both_absence_and_corruption(tmp_path: Path):
    good = _write(tmp_path / "good.json", "{}")
    bad = _write(tmp_path / "bad.json", "{{{")
    loads = [load_document(good), load_document(bad), load_document(tmp_path / "gone.json")]
    errors = verify_all_present(loads)
    assert len(errors) == 2
    assert any("required document is missing" in error for error in errors)
    assert any("bad.json" in error for error in errors)


def test_missing_expected_returns_unusable_loads(tmp_path: Path):
    _write(tmp_path / "a.json", "{}")
    result = missing_expected(tmp_path, ["a.json", "b.json"])
    assert len(result) == 1
    assert result[0].path.endswith("b.json")


def test_missing_expected_with_an_empty_list_returns_nothing(tmp_path: Path):
    """An empty expectation is the caller's problem, and the helper must not hide it.

    ``[]`` here is indistinguishable from success, so a caller with a *declared* list must
    check the declaration is non-empty itself. Asserted so that behaviour is pinned rather
    than assumed.
    """
    assert missing_expected(tmp_path, []) == []
