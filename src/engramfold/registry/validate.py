"""Canonical registry and repository validation.

This is the authoritative validation entry point. It is reachable three ways, and all
three must do the same work:

* ``engramfold validate`` / ``engramfold registry validate`` (via ``engramfold.cli``)
* the ``engramfold-validate`` console script -> :func:`main`
* ``python -m engramfold.registry.validate`` -> the ``__main__`` guard at the foot of
  this file

The third is called out because its absence was a real defect in the repository this
design learned from: without a ``__main__`` guard, ``python -m pkg.module`` imports the
module, executes nothing, and exits 0 -- which is indistinguishable from a passing
validation. ``tests/test_entry_points.py`` runs every entry point as a real subprocess and
asserts they agree with each other and with the in-process result.

The eight gates
---------------

Each gate is a distinct question, declared in
:data:`engramfold.validation.contract.GATE_CONTRACT`. A gate that does not execute is a
coverage failure rather than a shorter report. Each reports a census -- targets
discovered, targets checked, assertions executed, passed, failed, skipped -- and each
must have executed at least one assertion, or named a written reason why executing none
was legitimate.

Nothing here interprets the research. These gates check that the record is well-formed,
resolvable, and still true of the bytes on disk. They do not decide whether a result is
scientifically sound, and they do not claim to.
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path
from typing import Any

from engramfold import STATUS
from engramfold.artifacts.manifest import validate_artifact_manifest, verify_artifact
from engramfold.datasets.freeze import verify_freeze
from engramfold.datasets.manifest import validate_dataset_manifest
from engramfold.documents import discover, load_document, load_text_document
from engramfold.experiments.freeze import (
    validate_experiment_freeze_document,
    verify_experiment_freeze,
)
from engramfold.experiments.manifest import validate_experiment_manifest
from engramfold.hashing import (
    canonical_json_hash,
    content_hash,
    directory_hash,
    manifest_hash,
)
from engramfold.provenance.git_state import (
    PROVENANCE_RECORD_VERSION,
    capture_git_state,
)
from engramfold.registry.store import (
    CANONICAL_DIRECTORIES,
    DECLARED_POPULATIONS,
    GATE_POPULATION,
    REGISTRY_FILES,
    Registry,
    claim_errors,
    load_registry,
    phase_errors,
    population_policy_errors,
    record_errors,
    reference_counts,
    reference_errors,
    registry_file_errors,
)
from engramfold.schemas import SUPPORTED_SCHEMA_VERSIONS, VERSION_FIELD
from engramfold.validation.accounting import (
    BLOCKED_DEPENDENCY,
    CONDITIONALLY_REQUIRED,
    OPTIONAL,
    REQUIRED_NONEMPTY,
    ValidationResult,
    VerificationReport,
)
from engramfold.validation.census import build_result
from engramfold.validation.contract import (
    GATE_CONTRACT,
    VERIFIER_CONTRACT,
)

#: Canonical documents that must exist for the repository to be describable at all.
REQUIRED_DOCUMENTS: tuple[str, ...] = (
    "README.md",
    "ERRATA.md",
    "LICENSE",
    "pyproject.toml",
    ".gitignore",
    ".gitattributes",
    "docs/ARCHITECTURE.md",
    "docs/METHODOLOGY.md",
    "docs/PROVENANCE.md",
    "docs/REPRODUCIBILITY.md",
    "docs/VALIDATION.md",
    "docs/ERRATA_POLICY.md",
    "registry/README.md",
    "registry/populations.yaml",
    "datasets/README.md",
    "experiments/README.md",
    "artifacts/README.md",
    "reports/README.md",
    "provenance/README.md",
    "configs/README.md",
)

#: Named invariants the substrate gate executes. They are the gate's targets, so that
#: "the infrastructure is self-consistent" is an executed claim rather than a mood.
SUBSTRATE_CHECKS: tuple[str, ...] = (
    "schema-versions-registered",
    "single-hashing-implementation",
    "no-swallowed-exceptions",
    "generated-documents-marked",
    "canonical-directories-documented",
    "canonical-hashing-self-check",
)

#: Modules permitted to reach for a hash library directly.
HASHING_MODULE = "hashing.py"

#: Files that are hand-authored canonical state, and must therefore NOT carry a
#: generated marker. If one did, a reader could not tell source from output.
HAND_AUTHORED_GLOBS: tuple[str, ...] = (
    "registry/*.yaml",
    "datasets/manifests/*.json",
    "experiments/manifests/*.json",
    "artifacts/manifests/*.json",
    "configs/*.yaml",
)

#: Files that are produced by a generator, and must say so.
GENERATED_GLOBS: tuple[str, ...] = (
    "datasets/freezes/*.json",
    "experiments/freezes/*.json",
)

#: The field a generated document must carry to declare that a tool wrote it. Named
#: identically here and in the freeze formats so the check and the writers cannot drift.
GENERATED_MARKER = "generated_by"


# ── Gate: substrate ──────────────────────────────────────────────────────────


def _check_schema_versions_registered(root: Path) -> list[str]:
    """Every registered document kind must have a version field and a known version."""
    errors: list[str] = []
    if not SUPPORTED_SCHEMA_VERSIONS:
        return ["schemas: no document kind is registered, so no format is version-checked"]
    for kind, versions in sorted(SUPPORTED_SCHEMA_VERSIONS.items()):
        if kind not in VERSION_FIELD:
            errors.append(f"schemas: kind {kind!r} has no declared version field")
        if not versions:
            errors.append(f"schemas: kind {kind!r} declares no supported version")
        if any(not isinstance(version, int) or version < 1 for version in versions):
            errors.append(f"schemas: kind {kind!r} declares a non-positive-integer version")
    return errors


def _python_sources(root: Path) -> list[Path]:
    return sorted((root / "src/engramfold").rglob("*.py"))


def _check_single_hashing_implementation(root: Path) -> list[str]:
    """Only ``hashing.py`` may import a hash library.

    A second implementation is how two incompatible notions of identity appear. The check is
    on the import sites rather than on behaviour, because behaviour only diverges later --
    after artifacts already exist with digests computed the old way.

    Detection is by parsing the module's AST, not by searching its text. A substring search
    would fire on this very function, whose subject matter is the word ``hashlib``, and a
    check that cannot tell a mention from a use is a check that will be disabled the first
    time it cries wolf.
    """
    errors: list[str] = []
    sources = _python_sources(root)
    if not sources:
        return [
            "single-hashing-implementation: no Python sources found under src/engramfold, so "
            "this invariant could not be checked"
        ]
    for path in sources:
        if path.name == HASHING_MODULE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError) as exc:
            errors.append(f"single-hashing-implementation: {path.name} could not be parsed: {exc}")
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            if any(name == "hashlib" or name.startswith("hashlib.") for name in modules):
                errors.append(
                    f"single-hashing-implementation: "
                    f"{path.relative_to(root).as_posix()} imports a hash library; every digest "
                    f"must come from engramfold.hashing so that identity has exactly one definition"
                )
                break
    return errors


def _has_swallowed_handler(source: Path) -> list[str]:
    """AST detection of exception handlers that discard the failure.

    Regex cannot do this reliably -- comment placement, nesting and multi-line bodies all
    defeat it -- so the source is parsed. Detected:

    * a bare ``except:``, which catches everything including ``KeyboardInterrupt``;
    * a handler whose entire body is ``pass``, ``continue`` or ``...``, which turns a
      failure into silence.

    A handler that records, re-raises or returns is fine; the point is not to forbid
    catching, it is to forbid discarding.
    """
    problems: list[str] = []
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return [f"no-swallowed-exceptions: {source.name} could not be parsed: {exc}"]

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            problems.append(
                f"{source.name}:{node.lineno}: bare 'except:' catches everything, including "
                f"KeyboardInterrupt and SystemExit; name the exceptions expected"
            )
        if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Continue, ast.Expr)):
            body = node.body[0]
            # `pass` and `continue` discard unconditionally; a bare `...` expression is the
            # idiomatic way of writing the same thing.
            discards = isinstance(body, (ast.Pass, ast.Continue))
            if isinstance(body, ast.Expr):
                discards = isinstance(body.value, ast.Constant) and body.value.value is Ellipsis
            if discards:
                problems.append(
                    f"{source.name}:{node.lineno}: the exception handler discards the failure "
                    f"silently; record it, re-raise it, or return a value that carries it"
                )
    return problems


def _check_no_swallowed_exceptions(root: Path) -> list[str]:
    errors: list[str] = []
    sources = _python_sources(root)
    if not sources:
        return [
            "no-swallowed-exceptions: no Python sources found under src/engramfold, so this "
            "invariant could not be checked"
        ]
    for path in sources:
        errors.extend(_has_swallowed_handler(path))
    return errors


def _check_generated_documents_marked(root: Path) -> list[str]:
    """Generated documents carry the marker; hand-authored documents do not.

    Without this, a generator's output and a human's source look identical on disk, and a
    reviewer cannot tell which files may be edited by hand. A generated file that lacks the
    marker is the failure that matters most: it invites a hand edit that the next
    regeneration silently destroys.
    """
    errors: list[str] = []
    inspected = 0

    for pattern in GENERATED_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            inspected += 1
            load = load_document(path)
            if load.is_error:
                errors.extend(load.errors())
                continue
            marker = (load.value or {}).get(GENERATED_MARKER)
            if not isinstance(marker, str) or not marker.strip():
                errors.append(
                    f"{path.relative_to(root).as_posix()}: generated document has no "
                    f"{GENERATED_MARKER!r} field, so it is indistinguishable from hand-authored "
                    f"canonical source"
                )

    for pattern in HAND_AUTHORED_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            inspected += 1
            load = load_document(path)
            if load.is_error:
                # Version-checked loading happens in the dedicated gates; here the only
                # question is whether the marker is present.
                continue
            if GENERATED_MARKER in (load.value or {}):
                errors.append(
                    f"{path.relative_to(root).as_posix()}: hand-authored document carries "
                    f"{GENERATED_MARKER!r}; a source file marked as generated is one a reader "
                    f"will not dare edit and a generator will happily overwrite"
                )

    if inspected == 0:
        errors.append(
            "generated-documents-marked: no documents were inspected, so this invariant did "
            "not execute against anything"
        )
    return errors


def _check_canonical_directories_documented(root: Path) -> list[str]:
    """Every canonical directory states its ownership and is named in the architecture doc.

    The requirement is that no directory exists merely for tidiness. A directory that
    holds canonical state must say who owns it and why it exists, and must appear in
    ``docs/ARCHITECTURE.md``, so that adding one is a documented act.
    """
    errors: list[str] = []
    architecture = root / "docs/ARCHITECTURE.md"
    try:
        architecture_text = architecture.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"canonical-directories-documented: docs/ARCHITECTURE.md is unreadable: {exc}"]

    for relative in CANONICAL_DIRECTORIES:
        directory = root / relative
        if not directory.is_dir():
            errors.append(
                f"canonical-directories-documented: {relative}/ does not exist; a canonical "
                f"directory missing from a checkout means its contents cannot be enumerated"
            )
            continue
        readme = directory / "README.md"
        if not readme.is_file():
            errors.append(
                f"canonical-directories-documented: {relative}/README.md is missing; every "
                f"directory holding canonical state must state its ownership and purpose"
            )
        else:
            try:
                if not readme.read_text(encoding="utf-8").strip():
                    errors.append(
                        f"canonical-directories-documented: {relative}/README.md is empty"
                    )
            except (OSError, UnicodeDecodeError) as exc:
                errors.append(
                    f"canonical-directories-documented: {relative}/README.md is unreadable: {exc}"
                )
        if relative not in architecture_text:
            errors.append(
                f"canonical-directories-documented: {relative}/ is not named in "
                f"docs/ARCHITECTURE.md, so its ownership is undocumented"
            )
    return errors


def _check_canonical_hashing_self_check(root: Path) -> list[str]:
    """Execute the hashing implementation and assert its determinism properties live.

    This is not a restatement of the unit tests; it is the substrate gate refusing to
    certify a repository whose identity primitive is broken at the moment of validation.
    Each property asserted here has a specific failure it would catch:

    * two hashes of the same bytes must agree (a non-deterministic implementation);
    * key order must not matter (serialisation leakage into identity);
    * volatile fields must not matter (timestamps leaking into identity);
    * a one-byte change must matter (a digest that is not a function of the content);
    * a directory digest must be order- and location-independent.
    """
    errors: list[str] = []

    payload = {"b": 2, "a": 1, "nested": {"y": [1, 2], "x": "z"}}
    if canonical_json_hash(payload) != canonical_json_hash(payload):
        errors.append("canonical-hashing-self-check: hashing the same value twice disagreed")

    reordered = {"nested": {"x": "z", "y": [1, 2]}, "a": 1, "b": 2}
    if canonical_json_hash(payload) != canonical_json_hash(reordered):
        errors.append(
            "canonical-hashing-self-check: key ordering changed the identity of a value, so "
            "reformatting a document would change what it is"
        )

    if manifest_hash({"a": 1, "created_at": "2026-01-01"}) != manifest_hash({"a": 1}):
        errors.append(
            "canonical-hashing-self-check: a timestamp changed a manifest identity; volatile "
            "fields must be excluded from identity"
        )

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        first = base / "a.bin"
        first.write_bytes(b"payload")
        initial = content_hash(first)
        first.write_bytes(b"payloae")
        if content_hash(first) == initial:
            errors.append(
                "canonical-hashing-self-check: a one-byte change did not change the content "
                "hash, so the digest is not a function of the bytes"
            )

        tree_a = base / "tree-a"
        tree_b = base / "tree-b" / "deeper"
        tree_a.mkdir()
        tree_b.mkdir(parents=True)
        for name in ("one.txt", "two.txt"):
            (tree_a / name).write_text(f"{name}\n", encoding="utf-8")
            (tree_b / name).write_text(f"{name}\n", encoding="utf-8")
        digest_a = directory_hash(tree_a).digest
        digest_b = directory_hash(tree_b).digest
        if digest_a != digest_b:
            errors.append(
                "canonical-hashing-self-check: identical trees at different locations hashed "
                "differently, so directory identity depends on where the work was done"
            )
        if directory_hash(tree_a).digest != digest_a:
            errors.append("canonical-hashing-self-check: directory hashing is not deterministic")
    return errors


_SUBSTRATE_IMPLEMENTATIONS = {
    "schema-versions-registered": _check_schema_versions_registered,
    "single-hashing-implementation": _check_single_hashing_implementation,
    "no-swallowed-exceptions": _check_no_swallowed_exceptions,
    "generated-documents-marked": _check_generated_documents_marked,
    "canonical-directories-documented": _check_canonical_directories_documented,
    "canonical-hashing-self-check": _check_canonical_hashing_self_check,
}


def check_substrate(root: Path | str) -> ValidationResult:
    """Is the integrity infrastructure itself self-consistent and functional?

    The targets are the named invariants in :data:`SUBSTRATE_CHECKS`. Missing an
    implementation for one is an error rather than a shorter list, so deleting a check
    fails the gate instead of shrinking it.
    """
    base = Path(root)
    errors: list[str] = []
    executed = 0
    for name in SUBSTRATE_CHECKS:
        implementation = _SUBSTRATE_IMPLEMENTATIONS.get(name)
        if implementation is None:
            errors.append(
                f"<gate:substrate>: the substrate check {name!r} has no implementation, so it "
                f"cannot have executed"
            )
            continue
        executed += 1
        try:
            errors.extend(implementation(base))
        except (OSError, ValueError, TypeError, SyntaxError) as exc:
            # A crashing invariant is a failed invariant. The exception is recorded against
            # the check that raised it rather than allowed to abort the gate.
            errors.append(f"{name}: raised {type(exc).__name__} while executing: {exc}")
    return build_result(
        "substrate",
        REQUIRED_NONEMPTY,
        list(SUBSTRATE_CHECKS),
        errors,
        checks_executed=executed,
    )


# ── Gate: documents ──────────────────────────────────────────────────────────


def check_documents(root: Path | str) -> ValidationResult:
    """Do the canonical documents exist, load, and advertise the right status?"""
    base = Path(root)
    errors: list[str] = []
    executed = 0

    for relative in REQUIRED_DOCUMENTS:
        executed += 1
        load = load_text_document(base / relative)
        if load.is_absent:
            errors.append(f"{relative}: required canonical document is missing")
            continue
        if load.is_error:
            errors.extend(load.errors())
            continue
        if load.text is not None and not load.text.strip():
            errors.append(f"{relative}: required canonical document is empty")

    executed += 1
    readme = base / "README.md"
    try:
        text = readme.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(
            f"README.md: unreadable, so the repository states nothing about itself: {exc}"
        )
    else:
        if f"STATUS: {STATUS}" not in text:
            errors.append(
                f"README.md: does not carry the line 'STATUS: {STATUS}'; the advertised phase "
                f"must match engramfold.STATUS, because the population policy is only permitted "
                f"to authorise empty populations during that phase"
            )
        for overclaim in (
            "we show that",
            "our method outperforms",
            "state of the art",
            "proves that",
        ):
            if overclaim in text.lower():
                errors.append(
                    f"README.md: contains the claim-shaped phrase {overclaim!r}; Phase 0 has no "
                    f"results, and README text is not evidence"
                )

    return build_result(
        "documents",
        REQUIRED_NONEMPTY,
        [*list(REQUIRED_DOCUMENTS), "README.md:status"],
        errors,
        checks_executed=executed,
    )


# ── Gate: registry ───────────────────────────────────────────────────────────


def check_registry(root: Path | str, registry: Registry | None = None) -> ValidationResult:
    """Are the registries well-formed, resolvable, and consistent with their policy?"""
    base = Path(root)
    registry = registry if registry is not None else load_registry(base)
    errors: list[str] = []
    executed = 0

    executed += 1
    errors.extend(registry_file_errors(registry))
    executed += 1
    errors.extend(record_errors(registry))
    executed += 1
    errors.extend(population_policy_errors(registry))
    executed += 1
    errors.extend(phase_errors(base, registry.populations))
    executed += 1
    reference_errors_list = reference_errors(registry, base)
    errors.extend(reference_errors_list)
    executed += 1
    errors.extend(claim_errors(registry, base))

    # Every registry record and every declared population is a target, so an empty
    # registry still enumerates the populations it is required to declare.
    targets = [f"file:{name}" for name in REGISTRY_FILES]
    targets += [f"population:{name}" for name in DECLARED_POPULATIONS]
    targets += [
        f"{population}:{record.get('id')}"
        for population in REGISTRY_FILES
        for record in registry.records(population)
    ]

    counts = reference_counts(registry)
    populations = registry.populations
    declared_optional = populations is not None and all(
        populations.policy_for(name) == OPTIONAL
        for name in ("datasets", "models", "experiments", "artifacts", "reports", "claims")
    )
    return build_result(
        "registry",
        CONDITIONALLY_REQUIRED if declared_optional else REQUIRED_NONEMPTY,
        targets,
        errors,
        checks_executed=executed,
        precondition=("no registry population holds records yet" if declared_optional else None),
        detail={"reference_edges_examined": sum(counts.values())},
    )


# ── Gate: datasets ───────────────────────────────────────────────────────────


def repo_relative(base: Path, path: str) -> str:
    """A repository-relative POSIX path for a discovered document.

    Gates must compare *relative* paths. Discovery yields paths anchored at the repository
    root, while the registry records paths relative to it, so comparing the two directly
    makes every declared document look undiscovered and every discovered document look
    orphaned -- a reconciliation that reports errors regardless of the actual state. It also
    keeps error messages free of machine-specific absolute paths.
    """
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def empty_authorisation(reason: str | None, discovered: int, errors: list[str]) -> str | None:
    """Whether a written authorisation to discover nothing actually applies.

    An authorisation becomes incoherent once the gate has found targets or produced errors.
    The first would let a gate carry a "nothing to see here" note over a populated census; the
    second would let it claim the emptiness was by design while simultaneously reporting that
    something is wrong. Both are mixed signals this repository exists to refuse, so the
    authorisation is dropped in either case and the gate is judged on its errors.
    """
    if reason is None or discovered > 0 or errors:
        return None
    return reason


def _population_authorisation(
    registry: Registry, population: str
) -> tuple[str, str | None, str | None]:
    """Derive a gate's policy, recorded-empty reason, and precondition from the registry.

    Reading the policy from data rather than hardcoding it is what makes the authorisation
    auditable: changing which gates may be empty means editing a versioned, validated
    document, not editing a gate. Callers pass a *population* name, obtained from
    :data:`engramfold.registry.store.GATE_POPULATION`, never a gate name -- a gate that read
    the wrong population's policy would silently be authorised (or refused) for the wrong
    reason.
    """
    populations = registry.populations
    declared = populations.policy_for(population) if populations else None
    reason = populations.empty_allowed_reason(population) if populations else None
    if declared == OPTIONAL:
        return OPTIONAL, reason, None
    if declared == CONDITIONALLY_REQUIRED:
        return CONDITIONALLY_REQUIRED, None, f"population {population!r} is conditionally required"
    if declared == REQUIRED_NONEMPTY:
        return REQUIRED_NONEMPTY, None, None
    return REQUIRED_NONEMPTY, None, None


def check_datasets(root: Path | str, registry: Registry | None = None) -> ValidationResult:
    """Do the dataset manifests satisfy their schema and their registry entries?"""
    base = Path(root)
    registry = registry if registry is not None else load_registry(base)
    discovery = discover(base, "datasets/manifests/*.json", kind="dataset_manifest")

    errors: list[str] = list(discovery.errors)
    executed = 0

    executed += 1
    if not (base / "datasets/manifests").is_dir():
        errors.append(
            "datasets: datasets/manifests/ does not exist, so dataset discovery cannot run at all"
        )

    executed += 1
    if not discovery.present and discovery.rejected:
        errors.append(
            f"datasets: {discovery.rejected_count} dataset manifest(s) were found but none were "
            f"usable; malformed input is not absence"
        )

    ids: list[str] = []
    for load in discovery.present:
        manifest = load.require()
        executed += 1
        errors.extend(validate_dataset_manifest(manifest, origin=load.path))
        ids.append(str(manifest.get("dataset_id")))

    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    executed += 1
    if duplicates:
        errors.append(f"datasets: duplicate dataset_id values across manifests: {duplicates}")

    # Registry reconciliation: a record indexing a manifest that is not there, or a
    # manifest nothing indexes, means the two views of the research record disagree.
    declared_manifests = {}
    for record in registry.records("datasets"):
        path = record.get("manifest_path")
        if isinstance(path, str):
            declared_manifests[str(record.get("id"))] = path
    discovered_paths = {repo_relative(base, load.path) for load in discovery.present}
    executed += 1
    for record_id, path in sorted(declared_manifests.items()):
        if path not in discovered_paths and not (base / path).is_file():
            errors.append(
                f"datasets:{record_id}: registry indexes manifest {path!r}, which was not "
                f"discovered; the registry and the filesystem disagree"
            )
    if declared_manifests or discovered_paths:
        executed += 1
        orphaned = sorted(discovered_paths - set(declared_manifests.values()))
        if orphaned:
            errors.append(
                f"datasets: {orphaned} exist on disk but no registry record indexes them, so they "
                f"are unreachable from the research record"
            )

    # A registry that declares datasets while the gate discovers no manifests is a
    # contradiction regardless of the declared policy.
    executed += 1
    if registry.records("datasets") and not discovery.present:
        errors.append(
            f"datasets: the registry declares {len(registry.records('datasets'))} dataset "
            f"record(s) but the gate discovered 0 usable manifests; discovery is broken, so a "
            f"passing result here would be vacuous"
        )

    policy, reason, precondition = _population_authorisation(registry, GATE_POPULATION["datasets"])
    if discovery.present:
        # Manifests exist, so the population is not empty whatever the policy says.
        reason = None
    if not discovery.present and reason is None and policy == OPTIONAL:
        errors.append(
            "datasets: the registry authorises an empty dataset population but states no reason; "
            "an empty population must be declared with a written justification"
        )

    return build_result(
        "datasets",
        policy,
        [load.path for load in discovery.present],
        errors,
        checks_executed=executed,
        precondition=precondition,
        allowed_empty_reason=empty_authorisation(reason, discovery.discovered_count, errors),
        detail={
            "manifests_discovered": discovery.discovered_count,
            "manifests_rejected": discovery.rejected_count,
            "registry_records": len(registry.records("datasets")),
        },
    )


# ── Gate: dataset freezes ────────────────────────────────────────────────────


def check_freezes(root: Path | str, registry: Registry | None = None) -> ValidationResult:
    """Do the dataset freezes still describe the bytes and manifests they pinned?"""
    base = Path(root)
    registry = registry if registry is not None else load_registry(base)
    discovery = discover(base, "datasets/freezes/*.json", kind="dataset_freeze")

    errors: list[str] = list(discovery.errors)
    executed = 0
    verified = 0
    missing_artifacts = 0
    changed_artifacts = 0
    ids: list[str] = []

    for load in discovery.present:
        freeze = load.require()
        ids.append(str(freeze.get("freeze_id")))
        executed += 1
        verification = verify_freeze(base, freeze, path=load.path)
        errors.extend(verification.errors)
        if verification.ok:
            verified += 1
        missing_artifacts += verification.artifacts_missing
        changed_artifacts += verification.artifacts_changed

    executed += 1
    if not (base / "datasets/freezes").is_dir():
        errors.append(
            "freezes: datasets/freezes/ does not exist, so freeze discovery cannot run at all"
        )

    executed += 1
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        errors.append(f"freezes: the same freeze_id appears in more than one file: {duplicates}")

    # Every freeze must be referenced by something, or nothing will ever verify it again.
    referenced: set[str] = set()
    for population in ("datasets", "experiments", "artifacts"):
        for record in registry.records(population):
            for field_name in ("freeze_ids", "dataset_freezes"):
                value = record.get(field_name)
                if isinstance(value, list):
                    referenced.update(str(item) for item in value)
    executed += 1
    orphans = sorted(set(ids) - referenced)

    # A freeze whose manifest has vanished is the case the spec calls out: referenced files
    # disappearing must fail, not be skipped.
    executed += 1
    for load in discovery.present:
        freeze = load.require()
        manifest_relative = str(freeze.get("manifest_path", ""))
        if manifest_relative and not (base / manifest_relative).is_file():
            errors.append(
                f"{load.path}: the manifest this freeze pins ({manifest_relative}) is missing, so "
                f"the frozen dataset can no longer be re-derived"
            )

    policy, reason, precondition = _population_authorisation(registry, GATE_POPULATION["freezes"])
    if discovery.present:
        reason = None
    if not discovery.present and reason is None and policy == OPTIONAL:
        errors.append(
            "freezes: the registry authorises an empty freeze population but states no reason"
        )

    warnings: list[str] = []
    if orphans:
        warnings.append(
            f"freezes: {len(orphans)} freeze(s) are not referenced by any registry record "
            f"({[o[:12] + '…' for o in orphans]}); nothing will re-verify them automatically"
        )

    return build_result(
        "freezes",
        policy,
        [load.path for load in discovery.present],
        errors,
        checks_executed=executed,
        precondition=precondition,
        allowed_empty_reason=empty_authorisation(reason, discovery.discovered_count, errors),
        warnings=warnings,
        detail={
            "freezes_discovered": discovery.discovered_count,
            "freezes_rejected": discovery.rejected_count,
            "freezes_fully_verified": verified,
            "artifacts_missing": missing_artifacts,
            "artifacts_changed": changed_artifacts,
            "unreferenced_freezes": len(orphans),
        },
    )


# ── Gate: experiments ────────────────────────────────────────────────────────


def check_experiments(root: Path | str, registry: Registry | None = None) -> ValidationResult:
    """Are experiment manifests well-formed, resolvable, and unchanged since freezing?"""
    base = Path(root)
    registry = registry if registry is not None else load_registry(base)
    manifest_discovery = discover(base, "experiments/manifests/*.json", kind="experiment_manifest")
    freeze_discovery = discover(base, "experiments/freezes/*.json", kind="experiment_freeze")

    errors: list[str] = list(manifest_discovery.errors) + list(freeze_discovery.errors)
    executed = 0
    targets: list[str] = []
    drift_checked = 0

    freeze_ids: set[str] = set()
    for load in discover(base, "datasets/freezes/*.json").present:
        value = (load.value or {}).get("freeze_id")
        if isinstance(value, str):
            freeze_ids.add(value)

    for load in manifest_discovery.present:
        manifest = load.require()
        targets.append(load.path)
        executed += 1
        errors.extend(validate_experiment_manifest(manifest, origin=load.path))
        # Input references must resolve to real dataset freezes.
        for index, value in enumerate(manifest.get("dataset_freezes") or []):
            executed += 1
            if isinstance(value, str) and value not in freeze_ids:
                errors.append(
                    f"{load.path}: dataset_freezes[{index}]={value!r} does not resolve to any "
                    f"freeze in datasets/freezes/; the experiment names a frozen input that does "
                    f"not exist"
                )

    definition_freezes: dict[str, dict[str, Any]] = {}
    for load in freeze_discovery.present:
        freeze = load.require()
        targets.append(load.path)
        executed += 1
        errors.extend(validate_experiment_freeze_document(freeze, origin=load.path))
        experiment_id = str(freeze.get("experiment_id"))
        if experiment_id in definition_freezes:
            errors.append(
                f"{load.path}: more than one definition freeze exists for experiment "
                f"{experiment_id!r}; a definition is frozen once"
            )
        definition_freezes[experiment_id] = freeze

    for load in freeze_discovery.present:
        freeze = load.require()
        executed += 1
        drift_checked += 1
        errors.extend(verify_experiment_freeze(base, freeze, path=load.path).errors)

    # An experiment manifest that claims to have executed but has no frozen definition is
    # the case where "has the definition changed?" cannot be answered at all.
    executed += 1
    for load in manifest_discovery.present:
        manifest = load.require()
        status = str(manifest.get("status"))
        experiment_id = str(manifest.get("experiment_id"))
        if status in ("FROZEN", "RUNNING", "COMPLETED", "FAILED", "INVALID") and (
            experiment_id not in definition_freezes
        ):
            errors.append(
                f"{load.path}: status={status} but no definition freeze exists at "
                f"experiments/freezes/{experiment_id}.json, so there is no record of what the "
                f"definition was before execution and drift cannot be detected"
            )

    executed += 1
    declared = {
        str(record.get("manifest_path"))
        for record in registry.records("experiments")
        if isinstance(record.get("manifest_path"), str)
    }
    discovered = {repo_relative(base, load.path) for load in manifest_discovery.present}
    for path in sorted(declared - discovered):
        if not (base / path).is_file():
            errors.append(
                f"experiments: registry indexes manifest {path!r}, which was not discovered"
            )
    if declared or discovered:
        executed += 1
        orphaned = sorted(discovered - declared)
        if orphaned:
            errors.append(
                f"experiments: {orphaned} exist on disk but no registry record indexes them"
            )

    executed += 1
    if registry.records("experiments") and not manifest_discovery.present:
        errors.append(
            f"experiments: the registry declares {len(registry.records('experiments'))} experiment "
            f"record(s) but the gate discovered 0 usable manifests"
        )

    policy, reason, precondition = _population_authorisation(
        registry, GATE_POPULATION["experiments"]
    )
    if manifest_discovery.present or freeze_discovery.present:
        reason = None
    if not targets and reason is None and policy == OPTIONAL:
        errors.append(
            "experiments: the registry authorises an empty experiment population but states no "
            "reason"
        )

    return build_result(
        "experiments",
        policy,
        targets,
        errors,
        checks_executed=executed,
        precondition=precondition,
        allowed_empty_reason=empty_authorisation(reason, len(targets), errors),
        detail={
            "manifests_discovered": manifest_discovery.discovered_count,
            "manifests_rejected": manifest_discovery.rejected_count,
            "definition_freezes": freeze_discovery.discovered_count,
            "definitions_drift_checked": drift_checked,
            "registry_records": len(registry.records("experiments")),
        },
    )


# ── Gate: artifacts ──────────────────────────────────────────────────────────


def check_artifacts(root: Path | str, registry: Registry | None = None) -> ValidationResult:
    """Do artifact manifests name resolvable inputs, and do their bytes still match?"""
    base = Path(root)
    registry = registry if registry is not None else load_registry(base)
    discovery = discover(base, "artifacts/manifests/*.json", kind="artifact_manifest")

    errors: list[str] = list(discovery.errors)
    executed = 0
    targets: list[str] = []
    bytes_verified = 0
    unverifiable = 0

    experiment_ids = registry.ids("experiments")
    freeze_ids = {
        str((load.value or {}).get("freeze_id"))
        for load in discover(base, "datasets/freezes/*.json").present
    }

    for load in discovery.present:
        manifest = load.require()
        targets.append(load.path)
        executed += 1
        errors.extend(validate_artifact_manifest(manifest, origin=load.path))

        verification = verify_artifact(base, manifest, path=load.path)
        executed += 1
        errors.extend(verification.errors)
        if verification.bytes_verified:
            bytes_verified += 1
        if verification.unverifiable_reason:
            unverifiable += 1

        executed += 1
        creator = manifest.get("created_by_experiment")
        if experiment_ids and creator not in experiment_ids:
            errors.append(
                f"{load.path}: created_by_experiment={creator!r} does not exist in the experiment "
                f"registry"
            )
        for index, value in enumerate(manifest.get("input_dataset_freezes") or []):
            executed += 1
            if isinstance(value, str) and freeze_ids and value not in freeze_ids:
                errors.append(
                    f"{load.path}: input_dataset_freezes[{index}]={value!r} does not resolve to a "
                    f"freeze in datasets/freezes/"
                )

    executed += 1
    declared = {
        str(record.get("manifest_path"))
        for record in registry.records("artifacts")
        if isinstance(record.get("manifest_path"), str)
    }
    discovered = {repo_relative(base, load.path) for load in discovery.present}
    for path in sorted(declared - discovered):
        if not (base / path).is_file():
            errors.append(
                f"artifacts: registry indexes manifest {path!r}, which was not discovered"
            )
    if declared or discovered:
        executed += 1
        orphaned = sorted(discovered - declared)
        if orphaned:
            errors.append(
                f"artifacts: {orphaned} exist on disk but no registry record indexes them"
            )

    executed += 1
    if registry.records("artifacts") and not discovery.present:
        errors.append(
            f"artifacts: the registry declares {len(registry.records('artifacts'))} artifact "
            f"record(s) but the gate discovered 0 usable manifests"
        )

    policy, reason, precondition = _population_authorisation(registry, GATE_POPULATION["artifacts"])
    if discovery.present:
        reason = None
    if not discovery.present and reason is None and policy == OPTIONAL:
        errors.append(
            "artifacts: the registry authorises an empty artifact population but states no reason"
        )

    return build_result(
        "artifacts",
        policy,
        targets,
        errors,
        checks_executed=executed,
        precondition=precondition,
        allowed_empty_reason=empty_authorisation(reason, discovery.discovered_count, errors),
        detail={
            "manifests_discovered": discovery.discovered_count,
            "manifests_rejected": discovery.rejected_count,
            "artifact_bytes_verified": bytes_verified,
            "artifact_bytes_unverifiable": unverifiable,
            "registry_records": len(registry.records("artifacts")),
        },
    )


# ── Gate: provenance ─────────────────────────────────────────────────────────


def check_provenance(root: Path | str, registry: Registry | None = None) -> ValidationResult:
    """Is the checkout's provenance knowable, and are recorded provenance records consistent?

    A dirty tree is a warning, not a failure: uncommitted work is normal during
    development, and a gate that failed on it would be switched off within a day. An
    *unknowable* tree -- git absent, or its status unreadable -- blocks the gate, because
    nothing about attribution can be established and silence would be worse than a
    refusal.
    """
    base = Path(root)
    registry = registry if registry is not None else load_registry(base)
    discovery = discover(base, "provenance/records/*.json", kind="provenance_record")

    errors: list[str] = list(discovery.errors)
    warnings: list[str] = []
    blocked_reasons: list[str] = []
    executed = 0

    targets: list[str] = ["current-checkout"]
    executed += 1
    state = capture_git_state(base)
    blocked = False
    if not state.available:
        blocked = True
        blocked_reasons.append(
            f"git provenance is unknowable: {state.error or 'not a git checkout'}; the commit, "
            f"branch and dirty state of this checkout cannot be established"
        )
    elif not state.known:
        blocked = True
        blocked_reasons.append(
            f"git is available but its tracked status could not be read: "
            f"{state.error or 'no detail'}"
        )
    elif state.dirty:
        warnings.append(
            f"the checkout is dirty at {str(state.commit)[:12]} with "
            f"{len(state.dirty_file_list)} modified tracked file(s); acceptable while developing, "
            f"but not for producing a canonical artifact"
        )
    else:
        warnings.append(f"checkout is clean at {str(state.commit)[:12]} on branch {state.branch!r}")

    for load in discovery.present:
        freeze_record = load.require()
        targets.append(load.path)
        executed += 1
        if freeze_record.get("provenance_record_version") != PROVENANCE_RECORD_VERSION:
            errors.append(
                f"{load.path}: provenance_record_version="
                f"{freeze_record.get('provenance_record_version')!r} is not the version this build "
                f"interprets ({PROVENANCE_RECORD_VERSION})"
            )
        dirty = freeze_record.get("git_dirty")
        if dirty is True and not freeze_record.get("dirty_override_reason"):
            errors.append(
                f"{load.path}: records a dirty tree with no dirty_override_reason; a result "
                f"produced from uncommitted code must say so rather than leave it implicit"
            )
        if dirty is None:
            errors.append(
                f"{load.path}: git_dirty is null, meaning provenance was unknowable when the "
                f"record was written; an unknowable provenance record is not evidence"
            )

    executed += 1
    if not discovery.present and not (base / "provenance/records").is_dir():
        errors.append(
            "provenance: provenance/records/ does not exist, so recorded provenance cannot be "
            "enumerated"
        )

    policy, reason, precondition = _population_authorisation(
        registry, GATE_POPULATION["provenance"]
    )
    # The checkout itself is always a target, so the population is never empty.
    return build_result(
        "provenance",
        policy if not discovery.present else CONDITIONALLY_REQUIRED,
        targets,
        errors,
        checks_executed=executed,
        blocked_status=BLOCKED_DEPENDENCY if blocked else None,
        blocked_reasons=blocked_reasons,
        warnings=warnings,
        precondition=precondition,
        allowed_empty_reason=None if blocked else empty_authorisation(reason, 0, errors),
        detail={
            "provenance_records_discovered": discovery.discovered_count,
            "provenance_records_rejected": discovery.rejected_count,
            "git_available": int(state.available),
            "git_dirty": int(bool(state.dirty)),
            "dirty_files": len(state.dirty_file_list),
        },
    )


# ── Aggregation ──────────────────────────────────────────────────────────────


def audit(root: Path | str) -> VerificationReport:
    """Run every gate in the contract and return the census-bearing report.

    The registry is loaded once and shared, so every gate is describing the same
    registry rather than five slightly different readings of it.
    """
    base = Path(root)
    registry = load_registry(base)
    report = VerificationReport(contract=VERIFIER_CONTRACT, expected_gates=GATE_CONTRACT)
    report.add(check_substrate(base))
    report.add(check_documents(base))
    report.add(check_registry(base, registry))
    report.add(check_datasets(base, registry))
    report.add(check_freezes(base, registry))
    report.add(check_experiments(base, registry))
    report.add(check_artifacts(base, registry))
    report.add(check_provenance(base, registry))
    return report


def validate(root: Path | str) -> list[str]:
    """Every error from every gate, as a flat list.

    Retained as the simple interface. It carries gate-level context in each message so a
    caller that discards the census still sees which gate spoke.
    """
    report = audit(root)
    errors = report.all_errors()
    for result in report.results:
        if result.status != "PASS":
            errors.append(f"[{result.status}] {result.name}: {result.counts()}")
    return errors


def audit_json(root: Path | str) -> str:
    """The whole report as canonical JSON, for machine consumption."""
    from engramfold.hashing import pretty_json_text

    return pretty_json_text(audit(root).to_dict())


def main(argv: list[str] | None = None) -> int:
    """Entry point shared by ``engramfold-validate`` and ``python -m``.

    Exit codes: 0 only when the overall verdict is PASS; 1 when any gate failed; 3 when a
    gate is blocked. A blocked verdict is deliberately non-zero -- it is not a pass.

    With no positional argument the target is the repository containing the *working
    directory*, resolved the same way the CLI resolves it. It must not default to this file's
    own location: an installed package's location is not the repository the operator is
    standing in, and defaulting to it made the validator report a clean pass over a completely
    different tree.
    """
    from engramfold.paths import find_repo_root
    from engramfold.validation.accounting import PASS

    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    positional = [arg for arg in args if not arg.startswith("-")]
    target = Path(positional[0]) if positional else find_repo_root()

    report = audit(target)
    if as_json:
        from engramfold.hashing import pretty_json_text

        print(pretty_json_text(report.to_dict()))
    else:
        print(report.render())
    verdict = report.overall
    if verdict == PASS:
        return 0
    if verdict.startswith("BLOCKED"):
        return 3
    return 1


if __name__ == "__main__":
    # This guard is load-bearing, not decoration.
    #
    # Without it, `python -m engramfold.registry.validate` imports this module, executes
    # nothing, and exits 0 -- which is indistinguishable from a passing validation. Any
    # report that this repository validated successfully via `-m` would have been vacuous.
    # tests/test_entry_points.py executes this exact command and asserts its exit code
    # agrees with the in-process verdict.
    raise SystemExit(main())


__all__ = [
    "GENERATED_GLOBS",
    "GENERATED_MARKER",
    "HAND_AUTHORED_GLOBS",
    "REQUIRED_DOCUMENTS",
    "SUBSTRATE_CHECKS",
    "audit",
    "audit_json",
    "check_artifacts",
    "check_datasets",
    "check_documents",
    "check_experiments",
    "check_freezes",
    "check_provenance",
    "check_registry",
    "check_substrate",
    "empty_authorisation",
    "main",
    "repo_relative",
    "validate",
]
