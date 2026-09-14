"""The twelve adversarial attacks, executed.

This module is the executable form of the specification's adversarial review. Each test performs
one attack on a real repository fixture and asserts the outcome, and prints what was observed so
that the review can be *recorded* rather than summarised:

    python -m pytest tests/test_adversarial_review.py -v -s

Two outcomes are acceptable for each attack: identical deterministic output, or a loud, correct
failure. What is never acceptable is a quieter report than the truth — a shorter population, a
check that skipped its target, or a status word that changed without the state changing.

Attacks 2, 4, 10, 11 and 12 also have dedicated tests elsewhere. They are repeated here because
the review is meant to be run as a set: re-running them from a fresh fixture is the point, and
citing a test name is not evidence.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.conftest import (
    OPTIONAL_POLICIES,
    REQUIRED_POLICIES,
    registry_repo,
)
from tests.test_dataset_freeze import CREATED_AT, MANIFEST_PATH, make_dataset
from tests.test_experiments import draft_manifest

from engramfold.datasets.freeze import build_freeze, freeze_identity, verify_freeze, write_freeze
from engramfold.experiments.freeze import build_experiment_freeze
from engramfold.experiments.manifest import configuration_identity, experiment_identity
from engramfold.hashing import content_hash, directory_hash
from engramfold.provenance.git_state import capture_git_state
from engramfold.registry.store import load_registry, population_policy_errors
from engramfold.registry.validate import (
    audit,
    check_datasets,
    check_freezes,
    check_registry,
    validate,
)
from engramfold.validation import (
    FAIL,
    NONVACUOUS_PREFIX,
    OPTIONAL,
    PASS,
    REQUIRED_NONEMPTY,
    Skip,
    build_result,
)

OTHER_MANIFEST_PATH = "datasets/manifests/other.json"
ARTIFACT_PATH = "datasets/records.jsonl"
RESEARCH_POLICIES = {
    **OPTIONAL_POLICIES,
    # A fixture that contains a dataset must not also declare the dataset population optional:
    # that combination is itself an error, and it would mask the attack being run.
    "datasets": "REQUIRED_NONEMPTY",
    "dataset_freezes": "REQUIRED_NONEMPTY",
}


def research_repo(tmp_path: Path) -> Path:
    """A fixture with one dataset: artifact bytes, a manifest, a freeze, and an index record."""
    root = registry_repo(tmp_path / "repo", policies=RESEARCH_POLICIES)
    manifest = make_dataset(root)
    manifest_file = root / MANIFEST_PATH
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    built = build_freeze(
        root,
        manifest,
        MANIFEST_PATH,
        created_at=CREATED_AT,
        repository_revision="0" * 40,
        repository_dirty=False,
        frozen_by_command="adversarial review",
    )
    assert built.errors == [], built.errors
    written = write_freeze(root, built)
    assert written.written, written
    (root / "registry/datasets.yaml").write_text(
        "schema_version: 1\ndatasets:\n  - id: example\n"
        f"    manifest_path: {MANIFEST_PATH}\n"
        f"    freeze_ids: [{built.freeze['freeze_id']}]\n",
        encoding="utf-8",
    )
    return root


def observed(message: str) -> None:
    """Record the outcome of an attack. Run with ``-s`` to capture the transcript."""
    print(f"    observed: {message}")


# ── 1. Remove all canonical manifests ──────────────────────────────────────


def test_attack_01_removing_every_canonical_manifest_is_detected(tmp_path: Path):
    """The population must not shrink quietly: zero discovered is an error, and named."""
    root = research_repo(tmp_path)
    before = check_datasets(root)
    assert before.status == PASS, before.render()
    assert before.discovered == 1

    removed = sorted(root.glob("datasets/manifests/*.json"))
    assert removed, "there is no manifest to remove, so the attack would be vacuous"
    for path in removed:
        path.unlink()

    datasets = check_datasets(root)
    registry = check_registry(root)
    freezes = check_freezes(root)
    observed(f"deleted {len(removed)} manifest(s)")
    observed(f"datasets: status={datasets.status} discovered={datasets.discovered}")
    observed(f"registry: status={registry.status} errors={len(registry.all_errors())}")
    observed(f"freezes:  status={freezes.status} errors={len(freezes.all_errors())}")
    for error in [*datasets.all_errors(), *registry.all_errors()][:3]:
        observed(error)
    assert datasets.status == FAIL, "an emptied population passed"
    assert any("registry and the filesystem disagree" in error for error in datasets.all_errors())
    assert any(MANIFEST_PATH in error for error in datasets.all_errors())
    assert datasets.discovered == before.discovered, (
        "the population shrank silently: the gate is describing fewer targets than the record "
        "declares, rather than reporting the disagreement"
    )
    assert registry.status == FAIL, "the record indexing the removed manifest was accepted"
    assert freezes.status == FAIL, "the freeze pinning the removed manifest was accepted"


# ── 2. Rename a manifest field ─────────────────────────────────────────────


def test_attack_02_renaming_a_manifest_field_is_not_read_as_absence(tmp_path: Path):
    """A record whose field was renamed must fail, not quietly index nothing.

    The errors of the two gates that own this question are reported, rather than every error the
    whole report contains: a synthetic fixture is missing the canonical documents, so a full
    report here would be mostly about that instead of about the attack.
    """
    root = research_repo(tmp_path)
    (root / "registry/datasets.yaml").write_text(
        "schema_version: 1\ndatasets:\n  - id: example\n"
        f"    manifest_lookup: {MANIFEST_PATH}\n"
        f"    freeze_ids: [{recorded_freeze_id(root)}]\n",
        encoding="utf-8",
    )
    registry = check_registry(root)
    datasets = check_datasets(root)
    observed(f"registry: status={registry.status} errors={len(registry.all_errors())}")
    for error in registry.all_errors()[:3]:
        observed(error)
    observed(f"datasets: status={datasets.status} discovered={datasets.discovered}")
    for error in datasets.all_errors()[:2]:
        observed(error)
    assert registry.status == FAIL, "a renamed field was treated as absent rather than as an error"
    assert any("manifest_path" in error for error in registry.all_errors())
    assert any(
        "not discovered" in error or "unreachable from the research record" in error
        for error in datasets.all_errors()
    ), datasets.all_errors()


def _freeze_document(root: Path) -> dict:
    from engramfold.documents import load_document

    loads = sorted(root.glob("datasets/freezes/*.json"))
    assert loads, "the fixture recorded no freeze"
    load = load_document(loads[0], kind="dataset_freeze")
    assert load.is_present, load.errors()
    return load.require()


def recorded_freeze_id(root: Path) -> str:
    """The freeze id the fixture recorded, read from the document rather than recomputed."""
    freeze_id = _freeze_document(root).get("freeze_id")
    assert isinstance(freeze_id, str) and freeze_id
    return freeze_id


# ── 3. Point a freeze at the wrong manifest ────────────────────────────────


def test_attack_03_a_freeze_pointing_at_another_manifest_is_detected(tmp_path: Path):
    """The freeze stays well-formed; only the manifest it names is the wrong one."""
    root = research_repo(tmp_path)
    other = make_dataset(root)
    other["dataset_id"] = "other"
    other_file = root / OTHER_MANIFEST_PATH
    other_file.parent.mkdir(parents=True, exist_ok=True)
    other_file.write_text(json.dumps(other, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    redirected = dict(_freeze_document(root))
    redirected["manifest_path"] = OTHER_MANIFEST_PATH
    redirected["freeze_id"] = freeze_identity(redirected)  # consistent, and still wrong

    verification = verify_freeze(root, redirected)
    observed(f"freeze re-pointed at {OTHER_MANIFEST_PATH}: ok={verification.ok}")
    for error in verification.errors:
        observed(error)
    assert not verification.ok, "a freeze pointing at the wrong manifest verified"


# ─ 4. Modify one byte of a frozen artifact ────────────────────────────────


def test_attack_04_one_byte_of_a_frozen_artifact_is_detected(tmp_path: Path):
    root = research_repo(tmp_path)
    freeze = _freeze_document(root)
    artifact = root / ARTIFACT_PATH
    before = content_hash(artifact)
    artifact.write_bytes(b'{"record": 2}\n')

    verification = verify_freeze(root, freeze)
    observed(
        f"one byte changed ({before[:12]} -> {content_hash(artifact)[:12]}); "
        f"changed={verification.artifacts_changed} missing={verification.artifacts_missing}"
    )
    for error in verification.errors:
        observed(error)
    assert not verification.ok
    assert verification.artifacts_changed == 1
    assert verification.artifacts_missing == 0


# ── 5. Change a tracked source file without committing ─────────────────────


def test_attack_05_an_uncommitted_change_is_reported_and_passes_with_a_warning(
    cloned_repo: Path,
):
    """Dirty is recorded as a warning, never as clean and never as a silent pass."""
    clean = capture_git_state(cloned_repo)
    assert clean.dirty is False, "the clone must start clean for the attack to mean anything"
    tracked = cloned_repo / "src/engramfold/hashing.py"
    original = tracked.read_text(encoding="utf-8")
    tracked.write_text(original + "\n# uncommitted edit\n", encoding="utf-8")

    dirty = capture_git_state(cloned_repo)
    report = audit(cloned_repo)
    provenance = next(result for result in report.results if result.name == "provenance")
    observed(
        f"dirty={dirty.dirty} files={dirty.dirty_file_list} "
        f"diff_hash recorded={bool(dirty.dirty_diff_hash)}"
    )
    observed(f"overall={report.overall} warnings={provenance.warnings}")
    assert dirty.dirty is True
    assert "src/engramfold/hashing.py" in dirty.dirty_file_list
    assert dirty.dirty_diff_hash
    assert report.overall == PASS, report.render()
    assert provenance.warnings, "a dirty tree passed without recording that it is dirty"


# ── 6. Invoke the validators through every supported entry point ───────────


def test_attack_06_every_entry_point_agrees(cloned_repo: Path):
    def run(*command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command), cwd=str(cloned_repo), capture_output=True, text=True, check=False
        )

    module_form = run(sys.executable, "-m", "engramfold.registry.validate", "--json")
    console_form = run("engramfold-validate", "--json")
    cli_form = run(
        sys.executable, "-m", "engramfold", "--repo", str(cloned_repo), "validate", "--json"
    )
    payloads = [json.loads(form.stdout) for form in (module_form, console_form, cli_form)]
    observed(
        f"exit codes: module={module_form.returncode} console={console_form.returncode} "
        f"cli={cli_form.returncode}"
    )
    observed(f"overall: {[payload['overall'] for payload in payloads]}")
    assert {form.returncode for form in (module_form, console_form, cli_form)} == {0}
    assert all(payload["overall"] == PASS for payload in payloads)
    assert module_form.stdout == console_form.stdout, (
        "the module form and the console script printed different reports for the same "
        "repository, so what is believed depends on how it was invoked"
    )


# ── 7. Create an empty registry ────────────────────────────────────────────


def test_attack_07_an_empty_registry_is_only_acceptable_when_the_policy_says_so(
    cloned_repo: Path, tmp_path: Path
):
    """Two fixtures, because this attack has two possible correct answers."""
    authorised = cloned_repo
    for name in ("datasets", "models", "experiments", "artifacts", "reports", "claims"):
        (authorised / f"registry/{name}.yaml").write_text(
            f"schema_version: 1\n{name}: []\n", encoding="utf-8"
        )
    report = audit(authorised)
    registry = next(result for result in report.results if result.name == "registry")
    observed(
        f"PHASE 0 policy: overall={report.overall} registry checks={registry.checks_executed} "
        f"discovered={registry.discovered}"
    )
    assert report.overall == PASS, report.render()
    assert registry.checks_executed > 0, "an empty registry was accepted without being examined"

    required = registry_repo(tmp_path / "required", policies=REQUIRED_POLICIES)
    errors = population_policy_errors(load_registry(required))
    registry_result = check_registry(required)
    observed(f"REQUIRED policy: policy errors={len(errors)} status={registry_result.status}")
    for error in errors[:3]:
        observed(error)
    assert errors, "an empty registry declared REQUIRED_NONEMPTY was accepted"
    assert registry_result.status == FAIL


# ─ 8. Create a malformed manifest ─────────────────────────────────────────


def test_attack_08_a_malformed_manifest_is_malformed_and_not_absent(cloned_repo: Path):
    broken = cloned_repo / "datasets/manifests/broken.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text('{"dataset_id": "broken",', encoding="utf-8")

    errors = validate(cloned_repo)
    observed(f"{len(errors)} error(s); the first three:")
    for error in errors[:3]:
        observed(error)
    assert errors, "a malformed manifest was accepted"
    assert any("broken.json" in error for error in errors)
    assert any(
        "malformed" in error.lower() or "could not" in error.lower() or "json" in error.lower()
        for error in errors
    ), "the malformed document was not identified as unparseable"


# ── 9. Make every validation target skip ───────────────────────────────────


def test_attack_09_a_gate_that_skipped_every_target_fails():
    """Both directions, because the rule depends on whether the population is required.

    A *required* population whose every target was skipped is a failure: the gate found the
    things it exists to check and evaluated none of them. An *optional* population whose
    targets are skipped with written reasons is allowed, and the skips stay inside
    ``discovered`` so the population cannot shrink to whatever happened to be evaluated.
    """
    required_result = build_result(
        "datasets",
        REQUIRED_NONEMPTY,
        ["a", "b"],
        [],
        checks_executed=2,
        skipped=[Skip("a", "synthetic"), Skip("b", "synthetic")],
    )
    observed(
        f"REQUIRED: discovered={required_result.discovered} checked={required_result.checked} "
        f"skipped={required_result.skipped_count} status={required_result.status}"
    )
    assert required_result.status == FAIL, "a required gate that evaluated nothing passed"
    assert any(NONVACUOUS_PREFIX in error for error in required_result.all_errors())
    assert required_result.accounting_errors() == [], "the counters must still add up"

    optional_result = build_result(
        "datasets",
        OPTIONAL,
        ["a", "b"],
        [],
        checks_executed=2,
        skipped=[Skip("a", "written reason"), Skip("b", "written reason")],
    )
    observed(
        f"OPTIONAL: discovered={optional_result.discovered} checked={optional_result.checked} "
        f"skipped={optional_result.skipped_count} status={optional_result.status}"
    )
    assert optional_result.discovered == 2, "skipping a target removed it from the population"
    assert optional_result.status == PASS

    empty = build_result(
        "datasets",
        OPTIONAL,
        [],
        [],
        checks_executed=0,
        allowed_empty_reason="the population is empty on purpose",
    )
    unauthorised = build_result("datasets", OPTIONAL, [], [], checks_executed=0)
    observed(
        f"0 executed checks: with a written authorisation={empty.status}, "
        f"without one={unauthorised.status}"
    )
    assert unauthorised.status == FAIL, "zero executed checks passed with no authorisation"
    assert any(NONVACUOUS_PREFIX in error for error in unauthorised.all_errors())
    assert empty.status == PASS, (
        "an OPTIONAL gate with zero executed checks and a written authorisation must pass: the "
        "authorisation is the explicit selection of an operation where zero targets is valid"
    )


# ─ 10. Change configuration ordering ──────────────────────────────────────


def test_attack_10_reordering_a_configuration_changes_nothing():
    first = {"scope": "infrastructure-only", "placeholder": True, "seed": 0}
    reordered = {"seed": 0, "placeholder": True, "scope": "infrastructure-only"}
    observed(
        f"configuration_hash equal="
        f"{configuration_identity(first) == configuration_identity(reordered)}"
    )
    assert configuration_identity(first) == configuration_identity(reordered)
    assert configuration_identity(first) != configuration_identity({**first, "seed": 1})

    manifest = draft_manifest()
    manifest["configuration"] = first
    manifest["configuration_hash"] = configuration_identity(first)
    reordered_manifest = {**manifest, "configuration": reordered}
    frozen = build_experiment_freeze(
        manifest, "experiments/manifests/example.json", frozen_at=CREATED_AT
    )
    again = build_experiment_freeze(
        reordered_manifest, "experiments/manifests/example.json", frozen_at=CREATED_AT
    )
    observed(
        f"definition_hash equal="
        f"{frozen.freeze['definition_hash'] == again.freeze['definition_hash']} "
        f"freeze_id equal={frozen.freeze['freeze_id'] == again.freeze['freeze_id']}"
    )
    assert experiment_identity(manifest) == experiment_identity(reordered_manifest)
    assert frozen.freeze["configuration_hash"] == again.freeze["configuration_hash"]
    assert frozen.freeze["freeze_id"] == again.freeze["freeze_id"]


# ── 11. Run freeze generation twice ────────────────────────────────────────


def test_attack_11_generating_a_freeze_twice_is_idempotent(tmp_path: Path):
    root = research_repo(tmp_path)
    freeze = _freeze_document(root)
    artifact_before = content_hash(root / ARTIFACT_PATH)
    again = build_freeze(
        root,
        json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8")),
        MANIFEST_PATH,
        created_at=freeze["created_at"],
        repository_revision=freeze["repository_revision"],
        repository_dirty=freeze["repository_dirty"],
        frozen_by_command=freeze["frozen_by_command"],
    )
    written = write_freeze(root, again)
    observed(
        f"freeze_id stable={again.freeze['freeze_id'] == freeze['freeze_id']} "
        f"written={written.written} unchanged={written.unchanged} "
        f"byte_identical={written.byte_identical}"
    )
    assert again.errors == []
    assert again.freeze["freeze_id"] == freeze["freeze_id"]
    assert written.written is False and written.unchanged is True
    assert written.byte_identical is True
    assert content_hash(root / ARTIFACT_PATH) == artifact_before


# ── 12. Run hashing twice ──────────────────────────────────────────────────


def test_attack_12_hashing_twice_agrees_and_a_change_is_visible(tmp_path: Path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "one.txt").write_text("one\n", encoding="utf-8")
    (root / "two.txt").write_text("two\n", encoding="utf-8")

    first = content_hash(root / "one.txt")
    tree = directory_hash(root)
    observed(f"content_hash stable={first == content_hash(root / 'one.txt')}")
    observed(
        f"directory_hash stable={tree.digest == directory_hash(root).digest} "
        f"entries={len(tree.entries)}"
    )
    assert first == content_hash(root / "one.txt")
    assert tree.digest == directory_hash(root).digest
    assert len(tree.entries) == 2

    (root / "one.txt").write_text("one!\n", encoding="utf-8")
    observed(
        f"after a one-byte change: content differs="
        f"{first != content_hash(root / 'one.txt')} "
        f"tree differs={tree.digest != directory_hash(root).digest}"
    )
    assert first != content_hash(root / "one.txt")
    assert tree.digest != directory_hash(root).digest
