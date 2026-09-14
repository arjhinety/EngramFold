"""Source-level anti-patterns: rules the substrate states, asserted from outside it.

Every rule here is a rule the code claims somewhere — in a module docstring, in
``docs/ARCHITECTURE.md``, or in the text of the ``substrate`` gate — and each is checked
against the tree rather than trusted. The distinction matters: a rule that exists only as
prose is a rule that stops being true the first time someone is in a hurry.

What is checked, and the failure each check prevents:

``no-swallowed-exceptions``
    A handler whose whole body is ``pass``/``continue``/``...``, or a bare ``except:``,
    turns a failure into silence. Parsed with :mod:`ast` rather than searched for as text,
    because a substring search cannot tell a mention from a use.
``single-hashing-implementation``
    Two digest implementations that differ by one byte of normalisation are two
    incompatible notions of identity. Only ``hashing.py`` may reach for a hash library, so
    the check is on import sites.
``every-written-version-key-is-registered``
    A ``*_version`` key written into a document but absent from :mod:`engramfold.schemas` is
    a format that is never version-checked at all: it looks versioned to a reader and is
    unversioned to the code. Writing this check found one such key.
``generated-documents-marked``
    A generated file without a ``generated_by`` marker is indistinguishable from
    hand-authored state, so a reader edits it and a generator overwrites the edit.
``every-existing-directory-is-documented``
    The substrate gate checks one direction (a canonical directory is named in
    ``docs/ARCHITECTURE.md``). The other direction — an extra directory nobody documented —
    was unchecked until this module, and that is how an undecided directory appears.
``every-test-module-named-in-the-readme-exists``
    ``tests/README.md`` described modules that did not exist. Documentation of a check that
    is not there is worse than no documentation, because it is believed.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest
from tests.conftest import REPO_ROOT

from engramfold.errors import SchemaVersionError
from engramfold.hashing import DIRECTORY_HASH_VERSION, directory_hash
from engramfold.registry.store import (
    CANONICAL_DIRECTORIES,
    CLAIM_STATUSES,
    EVIDENCE_KINDS,
    REGISTRY_FILES,
    REQUIRED_RECORD_FIELDS,
)
from engramfold.registry.validate import (
    GENERATED_GLOBS,
    GENERATED_MARKER,
    HAND_AUTHORED_GLOBS,
    HASHING_MODULE,
)
from engramfold.schemas import (
    SUPPORTED_SCHEMA_VERSIONS,
    VERSION_FIELD,
    check_schema_version,
    current_version,
    version_field,
)

SOURCE_ROOT = REPO_ROOT / "src" / "engramfold"

#: Directories that are tooling rather than repository state. They are not documented in
#: ``docs/ARCHITECTURE.md`` because they are not part of the record.
TOOLING_DIRECTORIES: frozenset[str] = frozenset(
    {".git", ".cache", ".scratch", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".venv", "venv"}
)

_VERSION_KEY = re.compile(r"^[a-z][a-z0-9_]*_version$")
_TEST_MODULE_IN_README = re.compile(r"`(test_[a-z0-9_]+\.py)`")


def source_files() -> list[Path]:
    """Every Python source in the installed package, with a non-vacuity assertion.

    Returning an empty list would make every scan in this module pass for the wrong reason,
    so that is a failure rather than a skip.
    """
    files = sorted(SOURCE_ROOT.rglob("*.py"))
    assert files, f"no Python sources found under {SOURCE_ROOT}, so every scan here is vacuous"
    return files


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ─ Exceptions are never discarded ───────────────────────────────────────────


def swallowed_handlers(path: Path) -> list[str]:
    """Problems in ``path``'s exception handlers, as messages naming file and line."""
    problems: list[str] = []
    for node in ast.walk(parse(path)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            problems.append(f"{path.name}:{node.lineno}: bare 'except:' catches everything")
        if len(node.body) != 1:
            continue
        body = node.body[0]
        discards = isinstance(body, (ast.Pass, ast.Continue))
        if isinstance(body, ast.Expr) and isinstance(body.value, ast.Constant):
            discards = discards or body.value.value is Ellipsis
        if discards:
            problems.append(f"{path.name}:{node.lineno}: the handler discards the failure")
    return problems


def test_no_module_discards_an_exception_silently():
    problems = [problem for path in source_files() for problem in swallowed_handlers(path)]
    assert problems == [], (
        "an exception handler discards its failure, so a defect becomes silence:\n"
        + "\n".join(problems)
    )


def test_the_swallowed_exception_scan_detects_a_discarding_handler(tmp_path: Path):
    """The scan above is only worth something if it can fail.

    A detector that reports nothing because it detects nothing is indistinguishable from a
    clean tree, so it is pointed at a file with deliberately swallowed handlers in it.
    """
    offender = tmp_path / "offender.py"
    offender.write_text(
        "def f():\n"
        "    try:\n"
        "        return 1\n"
        "    except ValueError:\n"
        "        pass\n"
        "\n"
        "def g():\n"
        "    try:\n"
        "        return 2\n"
        "    except Exception:\n"
        "        ...\n"
        "\n"
        "def h():\n"
        "    try:\n"
        "        return 3\n"
        "    except:\n"
        "        return None\n",
        encoding="utf-8",
    )
    problems = swallowed_handlers(offender)
    assert len(problems) == 3, problems
    assert "offender.py:4" in problems[0], problems
    assert "offender.py:10" in problems[1], problems
    assert "offender.py:16" in problems[2], problems


# ── One hashing implementation ───────────────────────────────────────────────


def hash_library_importers() -> list[str]:
    offenders: list[str] = []
    for path in source_files():
        if path.name == HASHING_MODULE:
            continue
        for node in ast.walk(parse(path)):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            if any(name == "hashlib" or name.startswith("hashlib.") for name in modules):
                offenders.append(path.relative_to(SOURCE_ROOT).as_posix())
                break
    return offenders


def test_only_the_hashing_module_imports_a_hash_library():
    offenders = hash_library_importers()
    assert offenders == [], (
        f"these modules import a hash library directly, so identity has more than one "
        f"definition: {offenders}"
    )
    canonical = SOURCE_ROOT / HASHING_MODULE
    assert canonical.is_file(), f"{HASHING_MODULE} is missing, so nothing implements hashing"
    assert "import hashlib" in canonical.read_text(encoding="utf-8"), (
        "the module named as the canonical hashing implementation does not import a hash "
        "library, so the exemption it is granted is an exemption from a rule it is not using"
    )


# ── Every written version key is registered ─────────────────────────────────


def is_version_value(node: ast.AST) -> bool:
    """Is this expression a document-format version rather than an ordinary value?

    Recognised: a call to ``*_version()`` or ``current_version(...)``, and a name or
    attribute ending in ``_VERSION``. That is a *syntactic* rule on purpose: it identifies
    the version of a format, not a version-shaped data field such as ``dataset_version`` or
    ``driver_version``, which is content rather than a schema version.
    """
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id.endswith("_version") or node.func.id == "current_version"
    if isinstance(node, ast.Name):
        return node.id.endswith("_VERSION")
    if isinstance(node, ast.Attribute):
        return node.attr.endswith("_VERSION")
    return False


def written_version_keys() -> dict[str, set[str]]:
    """``key -> where it is written``, for every version key written into a payload."""
    written: dict[str, set[str]] = {}
    for path in source_files():
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                    continue
                if not _VERSION_KEY.match(key.value) or not is_version_value(value):
                    continue
                written.setdefault(key.value, set()).add(
                    f"{path.relative_to(SOURCE_ROOT).as_posix()}:{node.lineno}"
                )
    return written


def test_every_written_version_key_is_registered_in_schemas():
    """A format that is written but not registered is never version-checked.

    ``engramfold/schemas.py`` states that every ``*_version`` key the repository writes is a
    key it knows about. This is that statement, made mechanical. It failed when first run:
    ``directory_hash_version`` was written into every directory digest and registered
    nowhere, so the docstring was not quite true. The fix was to register the format, which
    ``test_the_directory_digest_is_a_registered_format`` then checks against a real payload.
    """
    written = written_version_keys()
    expected = {
        "freeze_format_version",
        "environment_manifest_version",
        "provenance_record_version",
        "directory_hash_version",
    }
    assert expected <= set(written), (
        f"the scan found {sorted(written)} and missed "
        f"{sorted(expected - set(written))}, which are known to be written: the scan itself "
        f"stopped working, so its verdict means nothing"
    )
    unregistered = {
        key: sorted(where) for key, where in written.items() if key not in VERSION_FIELD.values()
    }
    assert unregistered == {}, (
        "these version keys are written into documents but are not registered in "
        f"engramfold/schemas.py, so nothing checks them: {unregistered}"
    )


def test_every_registered_kind_declares_a_field_and_a_supported_version():
    assert SUPPORTED_SCHEMA_VERSIONS, "no format is registered, so nothing is version-checked"
    for kind, versions in SUPPORTED_SCHEMA_VERSIONS.items():
        assert kind in VERSION_FIELD, f"{kind!r} is registered without a version field"
        field = VERSION_FIELD[kind]
        assert field.endswith("_version"), field
        assert versions and all(isinstance(v, int) and v >= 1 for v in versions), (kind, versions)
        assert check_schema_version(kind, {field: current_version(kind)}) is None
        assert check_schema_version(kind, {}) is not None, f"{kind}: a missing version is accepted"
        assert check_schema_version(kind, {field: current_version(kind) + 1}) is not None
        assert check_schema_version(kind, {field: True}) is not None, "a bool is not a version"
    with pytest.raises(SchemaVersionError):
        version_field("a_kind_nobody_registered")


def test_the_directory_digest_is_a_registered_format():
    """The digest payload is JSON this repository writes, so its version is registered.

    Registering it is not ceremony: it makes the payload checkable, and this test checks it,
    so the entry cannot rot into a name that nothing produces.
    """
    payload = directory_hash(SOURCE_ROOT).to_dict()
    assert payload["entry_count"] > 0, "the digest covers no files, so it is not evidence"
    assert payload["directory_hash_version"] == DIRECTORY_HASH_VERSION
    assert check_schema_version("directory_digest", payload) is None
    without_version = {k: v for k, v in payload.items() if k != "directory_hash_version"}
    assert check_schema_version("directory_digest", without_version) is not None


# ─ Generated documents are marked; source documents are not ────────────────


def test_generated_documents_carry_the_marker_and_hand_authored_ones_do_not(tmp_path: Path):
    """Both directions, against real documents rather than against nothing.

    The substrate gate inspects the files that exist. At Phase 0 no freeze exists yet, so the
    "generated" half of the rule would be vacuous; here a freeze is built and written first,
    so the rule is exercised against a document that really is generated.
    """
    from tests.test_dataset_freeze import CREATED_AT, MANIFEST_PATH, make_dataset

    from engramfold.datasets.freeze import (
        GENERATED_BY_DEFAULT,
        build_freeze,
        write_freeze,
    )

    assert GENERATED_GLOBS and HAND_AUTHORED_GLOBS
    assert GENERATED_MARKER == "generated_by"

    root = tmp_path / "repo"
    manifest = make_dataset(root)
    manifest_path = root / MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    built = build_freeze(
        root,
        manifest,
        MANIFEST_PATH,
        created_at=CREATED_AT,
        repository_revision="0" * 40,
        repository_dirty=False,
        frozen_by_command="pytest",
    )
    assert built.errors == [], built.errors
    assert built.freeze[GENERATED_MARKER] == GENERATED_BY_DEFAULT, (
        "a built freeze does not name the function that wrote it, so a reader cannot tell it "
        "from a hand-authored document"
    )
    written = write_freeze(root, built)
    assert written.written, written
    generated = sorted(root.glob("datasets/freezes/*.json"))
    assert generated, "the freeze writer wrote nothing, so the marker rule was not exercised"
    for path in generated:
        assert GENERATED_MARKER in path.read_text(encoding="utf-8")

    hand_authored = sorted(REPO_ROOT.glob("registry/*.yaml"))
    assert hand_authored, "no hand-authored registry document exists, so the rule is vacuous"
    for path in hand_authored:
        assert GENERATED_MARKER not in path.read_text(encoding="utf-8"), (
            f"{path.name} carries a generated marker; a source file marked as generated is "
            f"one a reader will not dare edit and a generator will overwrite"
        )


# ── Directories and documents agree in both directions ───────────────────────


def test_every_top_level_directory_is_documented_in_the_architecture_doc():
    """The direction the substrate gate does not check.

    The gate fails when a canonical directory is absent from ``docs/ARCHITECTURE.md``. It has
    nothing to say about a directory that exists and is not canonical, which is how an
    undocumented directory appears without anyone deciding to add one.
    """
    architecture = (REPO_ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    undocumented = [
        entry.name
        for entry in sorted(REPO_ROOT.iterdir())
        if entry.is_dir()
        and entry.name not in TOOLING_DIRECTORIES
        and not entry.name.endswith(".egg-info")
        and entry.name not in architecture
    ]
    assert undocumented == [], (
        f"these top-level directories are not named in docs/ARCHITECTURE.md, so their "
        f"ownership and purpose are undocumented: {undocumented}"
    )


def test_every_canonical_directory_readme_exists_and_is_not_ignored():
    """A clone keeps a canonical directory because of its ``README.md``.

    Git tracks files, not directories. So the directory survives a clone exactly when its
    ``README.md`` is tracked, and it survives as an *empty* directory only if nothing in it
    is ignored. This asserts the precondition: the file is present, non-empty, and not
    excluded by ``.gitignore``.
    """
    for relative in CANONICAL_DIRECTORIES:
        readme = REPO_ROOT / relative / "README.md"
        assert readme.is_file(), f"{relative}/README.md is missing"
        assert readme.read_text(encoding="utf-8").strip(), f"{relative}/README.md is empty"
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", str(readme)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if ignored.returncode not in (0, 1):
            pytest.skip(f"git could not answer for {relative}/README.md: {ignored.stderr}")
        assert ignored.returncode == 1, (
            f"{relative}/README.md is excluded by .gitignore, so the directory will not "
            f"survive a clone"
        )


def test_every_json_schema_in_the_registry_parses():
    """The schemas are the promise that an outside reader can check a document's shape."""
    schemas = sorted((REPO_ROOT / "registry" / "schemas").glob("*.json"))
    assert schemas, "no JSON schema is shipped, so the external-reader promise is empty"
    for path in schemas:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict) and document, path.name
        assert document.get("$schema") or document.get("type"), path.name


def test_the_json_schemas_do_not_contradict_the_python_validators():
    """``docs/`` says the Python validator wins where the two disagree.

    That is only a defensible position if the disagreement is *visible*, so the agreement is
    asserted here: a record that satisfies the JSON schema must not be rejected by the validator
    for a field the schema did not require, and the closed vocabularies must match exactly.
    """
    path = REPO_ROOT / "registry" / "schemas" / "registry.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    definitions = schema["$defs"]
    assert set(schema["properties"]) >= set(REGISTRY_FILES), (
        "the JSON schema does not describe every registry index file"
    )
    for population in REGISTRY_FILES:
        singular = population.removesuffix("s")
        assert singular in definitions, f"{path.name}: no $defs/{singular}"
        required = set(definitions[singular]["required"])
        enforced = set(REQUIRED_RECORD_FIELDS[population])
        assert required <= enforced, (
            f"a {population!r} record can satisfy the JSON schema and still be rejected by the "
            f"validator for missing {sorted(required - enforced)}"
        )

    claim = definitions["claim"]["properties"]
    assert set(claim["status"]["enum"]) == set(CLAIM_STATUSES)
    assert set(claim["evidence"]["items"]["properties"]["kind"]["enum"]) == set(EVIDENCE_KINDS), (
        "the schema's evidence vocabulary is not the validator's, so a document can be valid to "
        "an outside reader and unacceptable here"
    )


def test_every_test_module_named_in_the_readme_exists_and_vice_versa():
    """Documentation of a check that does not exist is worse than none, because it is believed.

    Modules were named in ``tests/README.md`` and did not exist. This is the check that would
    have caught it, in both directions: a named module must be on disk, and a module on disk
    must be described.
    """
    readme = (REPO_ROOT / "tests" / "README.md").read_text(encoding="utf-8")
    named = set(_TEST_MODULE_IN_README.findall(readme))
    on_disk = {path.name for path in (REPO_ROOT / "tests").glob("test_*.py")}
    assert len(named) >= 10, f"the README names only {len(named)} modules; the table was lost"
    assert named - on_disk == set(), (
        f"named in tests/README.md but not on disk: {sorted(named - on_disk)}"
    )
    assert on_disk - named == set(), (
        f"on disk but not named in tests/README.md: {sorted(on_disk - named)}"
    )
