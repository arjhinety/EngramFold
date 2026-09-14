"""Canonical hashing: determinism, sensitivity, and the exclusion rules.

Every test here asserts on a digest or a census, never on an exit code. A test that asserted
"hashing did not raise" would pass against an implementation that returned a constant.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engramfold.errors import IntegrityError
from engramfold.hashing import (
    DEFAULT_DIRECTORY_EXCLUDES,
    IDENTITY_EXCLUDED_FIELD_NAMES,
    canonical_json_bytes,
    canonical_json_hash,
    canonical_json_text,
    configuration_hash,
    content_hash,
    content_hash_or_none,
    directory_hash,
    hash_stream,
    iter_chunks,
    manifest_hash,
    pretty_json_text,
    record_identity,
    selective_identity,
    sha256_bytes,
    strip_identity_excluded,
    verify_content_hash,
)

# ── Byte-level hashing ───────────────────────────────────────────────────────


def test_sha256_matches_a_known_vector():
    """A fixed vector, so the digest is pinned to a value rather than to itself."""
    assert sha256_bytes(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert (
        sha256_bytes(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_identical_content_gives_identical_hashes(tmp_path: Path):
    first = tmp_path / "a.bin"
    second = tmp_path / "b.bin"
    first.write_bytes(b"the same bytes")
    second.write_bytes(b"the same bytes")
    assert content_hash(first) == content_hash(second)


def test_one_byte_change_changes_the_digest(tmp_path: Path):
    target = tmp_path / "payload.bin"
    target.write_bytes(b"payload")
    before = content_hash(target)
    target.write_bytes(b"payloae")
    after = content_hash(target)
    assert before != after, "a one-byte change must change the digest"


def test_a_one_bit_change_changes_the_digest(tmp_path: Path):
    target = tmp_path / "payload.bin"
    target.write_bytes(bytes([0b0000_0000]))
    before = content_hash(target)
    target.write_bytes(bytes([0b0000_0001]))
    assert before != content_hash(target)


def test_content_hash_of_a_directory_is_an_error(tmp_path: Path):
    """A directory is not a content hash of anything, so it must not yield a digest."""
    with pytest.raises(IntegrityError):
        content_hash(tmp_path)


def test_content_hash_of_a_missing_path_is_an_error(tmp_path: Path):
    with pytest.raises(IntegrityError):
        content_hash(tmp_path / "absent")


def test_content_hash_or_none_distinguishes_absence(tmp_path: Path):
    """``None`` means absent, and only absent."""
    assert content_hash_or_none(tmp_path / "absent") is None
    present = tmp_path / "present.bin"
    present.write_bytes(b"x")
    assert content_hash_or_none(present) == content_hash(present)


def test_streamed_hashing_agrees_with_a_single_shot_hash(tmp_path: Path, monkeypatch):
    """The streamed path must produce the same digest as hashing the whole payload.

    The payload is made larger than the chunk size so the loop actually iterates more than
    once; a one-chunk file would not exercise the streaming boundary at all.
    """
    from engramfold import hashing

    payload = bytes(range(256)) * 4096
    target = tmp_path / "large.bin"
    target.write_bytes(payload)
    monkeypatch.setattr(hashing, "STREAM_CHUNK_BYTES", 4096)

    import hashlib

    assert content_hash(target) == hashlib.sha256(payload).hexdigest()
    assert hash_stream(target.open("rb")) == hashlib.sha256(payload).hexdigest()
    chunks = list(iter_chunks(target))
    assert len(chunks) > 1, "the fixture must span more than one chunk"


# ── Canonical JSON ───────────────────────────────────────────────────────────


def test_canonical_json_is_key_sorted_and_compact():
    assert canonical_json_text({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_canonical_json_rejects_non_finite_numbers():
    """NaN has no canonical JSON representation, so it must fail rather than hash."""
    from engramfold.errors import DocumentError

    with pytest.raises(DocumentError):
        canonical_json_text({"value": float("nan")})


def test_canonical_json_bytes_are_utf8_and_newline_free():
    payload = canonical_json_bytes({"k": "é"})
    assert payload.endswith(b"}")
    assert "é".encode() in payload


def test_pretty_json_differs_from_hashed_json_but_means_the_same_value():
    """Formatting is for humans; identity is taken over the canonical form."""
    value = {"b": 1, "a": 2}
    assert pretty_json_text(value) != canonical_json_text(value)
    assert json.loads(pretty_json_text(value)) == json.loads(canonical_json_text(value))


# ── Identity extraction ──────────────────────────────────────────────────────


def test_strip_removes_volatile_fields_recursively():
    payload = {
        "keep": 1,
        "created_at": "2026-01-01",
        "nested": {"hostname": "box", "keep": 2, "deeper": [{"absolute_path": "/tmp/x"}]},
    }
    stripped = strip_identity_excluded(payload)
    assert stripped == {"keep": 1, "nested": {"keep": 2, "deeper": [{}]}}


def test_every_documented_excluded_name_is_actually_excluded():
    """Guards against the list and the behaviour drifting apart."""
    for name in IDENTITY_EXCLUDED_FIELD_NAMES:
        assert strip_identity_excluded({name: "x"}) == {}, name


def test_manifest_hash_ignores_key_order():
    assert manifest_hash({"a": 1, "b": 2}) == manifest_hash({"b": 2, "a": 1})


def test_manifest_hash_ignores_nesting_order():
    left = {"outer": {"a": 1, "b": 2}}
    right = {"outer": {"b": 2, "a": 1}}
    assert manifest_hash(left) == manifest_hash(right)


def test_manifest_hash_ignores_timestamps():
    assert manifest_hash({"a": 1, "created_at": "2026-01-01"}) == manifest_hash({"a": 1})


def test_manifest_hash_ignores_hostname_and_absolute_path():
    base = {"a": 1}
    assert manifest_hash({**base, "hostname": "box"}) == manifest_hash(base)
    assert manifest_hash({**base, "absolute_path": "/home/x"}) == manifest_hash(base)


def test_manifest_hash_changes_with_content():
    assert manifest_hash({"a": 1}) != manifest_hash({"a": 2})
    assert manifest_hash({"a": 1}) != manifest_hash({"a": 1, "b": 2})


def test_configuration_hash_is_order_independent():
    """Reordering a YAML mapping is not a change to the experiment."""
    assert configuration_hash({"lr": 1e-4, "steps": 10}) == configuration_hash(
        {"steps": 10, "lr": 1e-4}
    )


def test_configuration_hash_distinguishes_values():
    assert configuration_hash({"lr": 1e-4}) != configuration_hash({"lr": 1e-5})


def test_configuration_and_manifest_hashes_are_different_kinds():
    """Same construction, different document classes -- the call sites are what differ.

    This test pins that they are both available under their own names, so a validator cannot
    silently verify the wrong one.
    """
    value = {"a": 1}
    assert configuration_hash(value) == manifest_hash(value)
    assert callable(configuration_hash) and callable(manifest_hash)


def test_record_identity_honours_extra_exclusions():
    with_extra = record_identity({"a": 1, "volatile": "x"}, exclude=("volatile",))
    assert with_extra == record_identity({"a": 1})


def test_selective_identity_uses_exactly_the_named_fields():
    payload = {"a": 1, "b": 2, "c": 3}
    assert selective_identity(payload, ("a", "b")) == selective_identity(
        {"a": 1, "b": 2, "c": 999}, ("a", "b")
    )
    assert selective_identity(payload, ("a",)) != selective_identity(payload, ("b",))


def test_selective_identity_tolerates_a_missing_field():
    """Identity can be computed while a document is still being assembled."""
    assert selective_identity({"a": 1}, ("a", "absent")) == selective_identity({"a": 1}, ("a",))


# ── Directory hashing ────────────────────────────────────────────────────────


def _tree(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def test_identical_trees_hash_identically(tmp_path: Path):
    left = _tree(tmp_path / "a", {"x.txt": "1", "sub/y.txt": "2"})
    right = _tree(tmp_path / "b", {"x.txt": "1", "sub/y.txt": "2"})
    assert directory_hash(left).digest == directory_hash(right).digest


def test_directory_hash_is_independent_of_absolute_location(tmp_path: Path):
    """A digest that depends on where the work happened is a digest of the machine."""
    shallow = _tree(tmp_path / "shallow", {"f.txt": "same"})
    deep = _tree(tmp_path / "a/b/c/d", {"f.txt": "same"})
    assert directory_hash(shallow).digest == directory_hash(deep).digest


def test_directory_hash_changes_when_a_file_changes(tmp_path: Path):
    root = _tree(tmp_path / "t", {"f.txt": "before"})
    before = directory_hash(root).digest
    (root / "f.txt").write_text("after", encoding="utf-8")
    assert directory_hash(root).digest != before


def test_directory_hash_changes_when_a_file_is_added(tmp_path: Path):
    root = _tree(tmp_path / "t", {"f.txt": "x"})
    before = directory_hash(root).digest
    (root / "g.txt").write_text("y", encoding="utf-8")
    assert directory_hash(root).digest != before


def test_directory_hash_is_stable_across_repeated_calls(tmp_path: Path):
    root = _tree(tmp_path / "t", {"a.txt": "1", "b.txt": "2", "sub/c.txt": "3"})
    digests = {directory_hash(root).digest for _ in range(5)}
    assert len(digests) == 1


def test_directory_hash_entries_are_sorted_by_normalised_path(tmp_path: Path):
    root = _tree(
        tmp_path / "t",
        {"z.txt": "1", "a.txt": "2", "m/n.txt": "3", "m/a.txt": "4"},
    )
    paths = [entry.path for entry in directory_hash(root).entries]
    assert paths == sorted(paths)
    assert all("\\" not in path for path in paths), "paths must be POSIX-normalised"
    assert all(not path.startswith("/") for path in paths)


def test_excluded_paths_are_reported_not_silently_dropped(tmp_path: Path):
    """A reader must be able to see what was *not* covered."""
    root = _tree(tmp_path / "t", {"keep.txt": "1"})
    (root / ".git").mkdir()
    (root / ".git/config").write_text("x", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__/m.pyc").write_text("x", encoding="utf-8")

    digest = directory_hash(root)
    assert [entry.path for entry in digest.entries] == ["keep.txt"]
    joined = " ".join(digest.excluded)
    assert ".git" in joined
    assert "__pycache__" in joined


def test_default_exclusions_cover_the_documented_set():
    for expected in (".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "*.pyc"):
        assert expected in DEFAULT_DIRECTORY_EXCLUDES


def test_custom_exclusion_is_honoured(tmp_path: Path):
    root = _tree(tmp_path / "t", {"keep.txt": "1", "skip.me": "2"})
    digest = directory_hash(root, exclude=("*.me",))
    assert [entry.path for entry in digest.entries] == ["keep.txt"]
    assert any("skip.me" in item for item in digest.excluded)


def test_empty_directories_are_not_covered(tmp_path: Path):
    """Git does not track them, so including them would make the digest a checkout digest."""
    root = tmp_path / "t"
    root.mkdir()
    (root / "empty").mkdir()
    plain = tmp_path / "plain"
    plain.mkdir()
    assert directory_hash(root).digest == directory_hash(plain).digest
    assert any("empty" in item for item in directory_hash(root).excluded)


def test_symlinks_are_not_followed_and_are_recorded(tmp_path: Path):
    root = _tree(tmp_path / "t", {"real.txt": "1"})
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create symlinks without elevation")
    digest = directory_hash(root)
    assert [entry.path for entry in digest.entries] == ["real.txt"]
    assert any("symlink" in item for item in digest.excluded)


def test_directory_hash_of_a_file_is_an_error(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(IntegrityError):
        directory_hash(target)


def test_directory_digest_carries_its_version(tmp_path: Path):
    """A digest must be traceable to the rules that produced it."""
    digest = directory_hash(_tree(tmp_path / "t", {"f.txt": "1"}))
    assert digest.directory_hash_version == 1
    assert digest.to_dict()["directory_hash_version"] == 1


def test_directory_digest_records_root_for_diagnostics_only(tmp_path: Path):
    """`root` is recorded, but two trees at different paths must still agree."""
    one = directory_hash(_tree(tmp_path / "one", {"f.txt": "1"}))
    two = directory_hash(_tree(tmp_path / "two", {"f.txt": "1"}))
    assert one.root != two.root
    assert one.digest == two.digest


# ─ Verification helper ──────────────────────────────────────────────────────


def test_verify_content_hash_reports_a_match(tmp_path: Path):
    target = tmp_path / "f.bin"
    target.write_bytes(b"data")
    check = verify_content_hash(target, content_hash(target))
    assert check.ok
    assert check.actual == check.expected


def test_verify_content_hash_reports_a_change(tmp_path: Path):
    target = tmp_path / "f.bin"
    target.write_bytes(b"data")
    recorded = content_hash(target)
    target.write_bytes(b"dat")
    check = verify_content_hash(target, recorded)
    assert not check.ok
    assert any("changed" in error for error in check.errors)


def test_verify_content_hash_reports_a_missing_artifact_distinctly(tmp_path: Path):
    """Missing and changed must not read the same: they are different facts."""
    check = verify_content_hash(tmp_path / "gone.bin", "0" * 64)
    assert not check.ok
    assert any("missing" in error for error in check.errors)
    assert not any("changed" in error for error in check.errors)


def test_verify_content_hash_reports_a_non_file(tmp_path: Path):
    check = verify_content_hash(tmp_path, "0" * 64)
    assert not check.ok
    assert any("non-file" in error for error in check.errors)


# ── Every digest is lowercase hex of the right length ────────────────────────


def test_all_digests_are_sha256_shaped(tmp_path: Path):
    root = _tree(tmp_path / "t", {"f.txt": "1"})
    digests = [
        content_hash(root / "f.txt"),
        directory_hash(root).digest,
        manifest_hash({"a": 1}),
        configuration_hash({"a": 1}),
        canonical_json_hash({"a": 1}),
        record_identity({"a": 1}),
        selective_identity({"a": 1}, ("a",)),
    ]
    for digest in digests:
        assert len(digest) == 64
        assert digest == digest.lower()
        int(digest, 16)
