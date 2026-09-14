"""The EngramFold command line.

A small, deterministic CLI over the integrity substrate. There are deliberately **no**
research commands here: nothing trains, nothing merges experts, nothing downloads weights.
Every command either reads the repository's canonical state, records provenance, or writes
a freeze.

Design rules, each of which is a way CLIs usually lose their value:

* **Exit codes are meaningful.** ``0`` pass, ``1`` failure, ``2`` usage error, ``3``
  blocked (a gate could not evaluate something). Blocked is never 0.
* **Failures are never swallowed.** Only :class:`engramfold.errors.EngramFoldError` is
  caught, and it is printed to stderr and reported as a failure. Anything else propagates
  with its traceback, because an unexpected exception is a defect and hiding it would be
  the exact behaviour this repository exists to prevent.
* **Machine-readable output where useful.** ``--json`` emits canonical JSON: key-sorted,
  compact, stable, so its own output can be diffed and hashed.
* **A command that did nothing says so.** ``verify-freeze`` with no freezes reports that it
  found none and exits non-zero, rather than printing nothing and returning 0.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engramfold import IDENTITY, STATUS, __version__
from engramfold.datasets.freeze import (
    build_freeze,
    verify_freeze,
    write_freeze,
)
from engramfold.documents import discover, load_document
from engramfold.errors import EngramFoldError
from engramfold.experiments.freeze import (
    build_experiment_freeze,
    write_experiment_freeze,
)
from engramfold.hashing import (
    canonical_json_hash,
    content_hash,
    directory_hash,
    pretty_json_text,
    strip_identity_excluded,
)
from engramfold.paths import ROOT_MARKERS
from engramfold.paths import find_repo_root as _find_repo_root
from engramfold.provenance.environment import capture_environment
from engramfold.provenance.git_state import provenance_record
from engramfold.registry.validate import audit

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3


def find_repo_root(start: Path | None = None) -> Path:
    """The repository root, resolved by the shared helper.

    Re-exported here so the CLI and the validator cannot disagree about which repository they
    are describing -- a disagreement that once made the validator pass on the wrong tree.
    """
    return _find_repo_root(start)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _emit_json(payload: object) -> None:
    print(pretty_json_text(payload))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engramfold",
        description=(
            "EngramFold integrity substrate. Reads canonical state, records provenance and "
            "writes freezes. There are no research commands: no training, no consolidation, "
            "no downloads."
        ),
    )
    parser.add_argument("--version", action="version", version=f"engramfold {__version__}")
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="repository root (default: nearest ancestor containing pyproject.toml or .git)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="run every validation gate")
    validate.add_argument("--json", action="store_true", help="emit the report as JSON")

    provenance = subparsers.add_parser(
        "provenance", help="capture the current checkout's provenance"
    )
    provenance.add_argument("--json", action="store_true")
    provenance.add_argument(
        "--write",
        type=Path,
        default=None,
        help="also write the record to this path (relative to the repository root)",
    )
    provenance.add_argument(
        "--git-only",
        action="store_true",
        help="skip environment capture (faster; no subprocess hardware probes)",
    )

    hash_parser = subparsers.add_parser("hash", help="hash a file or a directory tree")
    hash_parser.add_argument("path", type=Path)
    hash_parser.add_argument("--json", action="store_true")
    hash_parser.add_argument(
        "--tree", action="store_true", help="treat the path as a directory and hash the tree"
    )

    freeze = subparsers.add_parser("freeze", help="build a freeze document")
    freeze_sub = freeze.add_subparsers(dest="freeze_kind", required=True)
    dataset = freeze_sub.add_parser("dataset", help="freeze a dataset's bytes")
    dataset.add_argument("manifest", help="path to the dataset manifest, relative to the root")
    dataset.add_argument("--created-at", default=None, help="freeze timestamp (default: now)")
    dataset.add_argument("--write", action="store_true", help="write the freeze document")
    dataset.add_argument("--json", action="store_true")

    experiment = freeze_sub.add_parser("experiment", help="freeze an experiment definition")
    experiment.add_argument(
        "manifest", help="path to the experiment manifest, relative to the root"
    )
    experiment.add_argument("--frozen-at", default=None, help="freeze timestamp (default: now)")
    experiment.add_argument("--write", action="store_true")
    experiment.add_argument("--json", action="store_true")

    verify = subparsers.add_parser("verify-freeze", help="verify dataset freezes")
    verify.add_argument(
        "freeze_id", nargs="?", default=None, help="a specific freeze id (default: all)"
    )
    verify.add_argument("--json", action="store_true")

    registry = subparsers.add_parser("registry", help="registry operations")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    registry_validate = registry_sub.add_parser("validate", help="validate the registry")
    registry_validate.add_argument("--json", action="store_true")

    hashing = subparsers.add_parser("hashing", help="hashing operations")
    hashing_sub = hashing.add_subparsers(dest="hashing_command", required=True)
    selfcheck = hashing_sub.add_parser(
        "selfcheck", help="assert the canonical hashing properties hold"
    )
    selfcheck.add_argument("--json", action="store_true")

    identity = subparsers.add_parser("identity", help="print what this project claims to be")
    identity.add_argument("--json", action="store_true")

    return parser


def _cmd_validate(root: Path, args: argparse.Namespace) -> int:
    report = audit(root)
    if args.json:
        _emit_json(report.to_dict())
    else:
        print(report.render())
    verdict = report.overall
    if verdict == "PASS":
        return EXIT_OK
    return EXIT_BLOCKED if verdict.startswith("BLOCKED") else EXIT_FAIL


def _cmd_registry_validate(root: Path, args: argparse.Namespace) -> int:
    # Deliberately the same call as `validate`: the registry gates are part of the same
    # contract, and a second, narrower implementation would eventually disagree with it.
    report = audit(root)
    registry_result = next((result for result in report.results if result.name == "registry"), None)
    if args.json:
        _emit_json(
            {
                "registry": registry_result.to_dict() if registry_result else None,
                "overall": report.overall,
            }
        )
    else:
        print(registry_result.render() if registry_result else "registry gate did not execute")
        print(f"\noverall: {report.overall}")
    if registry_result is None:
        return EXIT_FAIL
    if registry_result.status == "PASS":
        return EXIT_OK
    return EXIT_BLOCKED if registry_result.status.startswith("BLOCKED") else EXIT_FAIL


def _cmd_provenance(root: Path, args: argparse.Namespace) -> int:
    relevant = [
        "src/engramfold",
        "registry",
        "pyproject.toml",
    ]
    record = provenance_record(root, relevant_paths=relevant, captured_at=_now())
    if not args.git_only:
        manifest = capture_environment(captured_at=record["captured_at"])
        record["environment"] = manifest.to_dict()

    if args.write is not None:
        destination = root / args.write
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(pretty_json_text(record), encoding="utf-8")
        if not args.json:
            print(f"wrote {args.write}")

    if args.json:
        _emit_json(record)
    else:
        print(f"commit:          {record['git_commit']}")
        print(f"branch:          {record['git_branch']}")
        print(f"dirty:           {record['git_dirty']}")
        print(f"dirty files:     {len(record['dirty_file_list'])}")
        print(f"dirty diff hash: {record['dirty_diff_hash']}")
        print(
            f"last commit touching relevant paths: {record['last_commit_affecting_relevant_file']}"
        )
        if record["error"]:
            print(f"error:           {record['error']}")
    # An unknowable provenance state is not a success: it means nothing about this checkout
    # can be attributed, and silently exiting 0 would present unknown as fine.
    if not record["git_available"] or record["git_dirty"] is None:
        return EXIT_BLOCKED
    return EXIT_OK


def _cmd_hash(root: Path, args: argparse.Namespace) -> int:
    target = args.path if args.path.is_absolute() else (root / args.path)
    if not target.exists():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return EXIT_FAIL
    if args.tree:
        tree = directory_hash(target)
        if args.json:
            _emit_json(tree.to_dict())
        else:
            print(f"directory_hash {tree.digest}")
            print(f"entries        {len(tree.entries)}")
            print(f"excluded       {len(tree.excluded)}")
        return EXIT_OK
    if not target.is_file():
        print(f"error: {args.path} is not a regular file (use --tree)", file=sys.stderr)
        return EXIT_FAIL
    file_digest = content_hash(target)
    if args.json:
        _emit_json(
            {
                "path": args.path.as_posix(),
                "size_bytes": target.stat().st_size,
                "content_hash": file_digest,
            }
        )
    else:
        print(file_digest)
    return EXIT_OK


def _cmd_freeze_dataset(root: Path, args: argparse.Namespace) -> int:
    load = load_document(root / args.manifest, kind="dataset_manifest")
    if load.is_absent:
        print(f"error: dataset manifest {args.manifest} does not exist", file=sys.stderr)
        return EXIT_FAIL
    if load.is_error:
        for error in load.errors():
            print(f"error: {error}", file=sys.stderr)
        return EXIT_FAIL
    manifest = load.require()
    state = provenance_record(root, relevant_paths=[args.manifest])
    result = build_freeze(
        root,
        manifest,
        args.manifest,
        created_at=args.created_at or _now(),
        repository_revision=state["git_commit"],
        repository_dirty=state["git_dirty"],
        frozen_by_command="engramfold freeze dataset",
    )
    if result.errors:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        return EXIT_FAIL
    if args.write:
        result = write_freeze(root, result)
    if args.json:
        _emit_json(
            {
                "freeze": result.freeze,
                "output_path": result.output_path,
                "written": result.written,
                "unchanged": result.unchanged,
                "byte_identical": result.byte_identical,
                "artifacts_verified": result.artifacts_verified,
            }
        )
    else:
        print(f"freeze_id       {result.freeze['freeze_id']}")
        print(f"dataset_id      {result.freeze['dataset_id']}")
        print(f"artifacts       {result.artifacts_verified}")
        print(f"output path     {result.output_path}")
        print(f"written         {result.written}")
        print(f"unchanged       {result.unchanged}")
        if not args.write:
            print("(dry run: pass --write to record the freeze)")
    return EXIT_OK


def code_revision_at_freeze(state: dict[str, Any]) -> dict[str, Any]:
    """A provenance record with its volatile fields removed, for embedding in a freeze.

    A definition freeze records which code was checked out when the definition was fixed. The
    instant of the *capture* is already recorded in ``frozen_at``; leaving a second timestamp
    inside the embedded record made the document impossible to regenerate byte-for-byte, so
    ``--frozen-at`` was not, as ``docs/REPRODUCIBILITY.md`` claims, the only volatile input to
    freeze generation.

    The volatile fields are dropped through the canonical helper rather than by naming
    ``captured_at`` here, so that a field added to the volatile list later cannot quietly
    reintroduce the same non-determinism.
    """
    stripped = strip_identity_excluded(state)
    return stripped if isinstance(stripped, dict) else {}


def _cmd_freeze_experiment(root: Path, args: argparse.Namespace) -> int:
    load = load_document(root / args.manifest, kind="experiment_manifest")
    if load.is_absent:
        print(f"error: experiment manifest {args.manifest} does not exist", file=sys.stderr)
        return EXIT_FAIL
    if load.is_error:
        for error in load.errors():
            print(f"error: {error}", file=sys.stderr)
        return EXIT_FAIL
    manifest = load.require()
    state = provenance_record(root, relevant_paths=[args.manifest])
    result = build_experiment_freeze(
        manifest,
        args.manifest,
        frozen_at=args.frozen_at or _now(),
        repository_revision=state["git_commit"],
        repository_dirty=state["git_dirty"],
        code_revision_at_freeze=code_revision_at_freeze(state),
        frozen_by_command="engramfold freeze experiment",
    )
    if result.errors:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        return EXIT_FAIL
    if args.write:
        result = write_experiment_freeze(root, result)
    if args.json:
        _emit_json(
            {
                "freeze": result.freeze,
                "output_path": result.output_path,
                "written": result.written,
                "unchanged": result.unchanged,
                "byte_identical": result.byte_identical,
            }
        )
    else:
        print(f"freeze_id         {result.freeze['freeze_id']}")
        print(f"experiment_id     {result.freeze['experiment_id']}")
        print(f"definition_hash   {result.freeze['definition_hash']}")
        print(f"output path       {result.output_path}")
        print(f"written           {result.written}")
        if not args.write:
            print("(dry run: pass --write to record the freeze)")
    return EXIT_OK


def _cmd_verify_freeze(root: Path, args: argparse.Namespace) -> int:
    discovery = discover(root, "datasets/freezes/*.json", kind="dataset_freeze")
    selected = [
        load
        for load in discovery.present
        if args.freeze_id is None or (load.value or {}).get("freeze_id") == args.freeze_id
    ]
    payloads: list[dict[str, Any]] = []
    failures = 0
    for load in selected:
        verification = verify_freeze(root, load.require(), path=load.path)
        payloads.append(verification.to_dict())
        if not verification.ok:
            failures += 1

    errors = list(discovery.errors)
    if args.freeze_id is not None and not selected:
        # Asking for a specific freeze and finding none is a failure, not an empty success.
        errors.append(
            f"no dataset freeze with freeze_id {args.freeze_id!r} exists under datasets/freezes/"
        )

    if args.json:
        _emit_json(
            {
                "discovered": discovery.discovered_count,
                "selected": len(selected),
                "failed": failures,
                "rejected": discovery.rejected_count,
                "errors": errors,
                "verifications": payloads,
            }
        )
    else:
        print(f"freezes discovered: {discovery.discovered_count}")
        print(f"freezes verified:   {len(selected)}")
        print(f"freezes failed:     {failures}")
        for payload in payloads:
            status = "OK" if payload["ok"] else "FAIL"
            print(
                f"  [{status}] {str(payload['freeze_id'])[:12]}… artifacts checked "
                f"{payload['artifacts_checked']}, missing {payload['artifacts_missing']}, "
                f"changed {payload['artifacts_changed']}"
            )
            for error in payload["errors"]:
                print(f"      {error}")
        for error in errors:
            print(f"  error: {error}")

    if errors or failures:
        return EXIT_FAIL
    if not selected:
        # Zero targets when freezes were expected. Reporting success here is the vacuous
        # pass this whole repository is built to refuse.
        print(
            "error: no dataset freezes exist to verify; a verification command that checked "
            "nothing is a failure unless the caller explicitly asked for a possibly-empty set",
            file=sys.stderr,
        )
        return EXIT_FAIL
    return EXIT_OK


def _cmd_hashing_selfcheck(root: Path, args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool]] = []

    payload = {"b": 2, "a": 1}
    checks.append(("repeatable", canonical_json_hash(payload) == canonical_json_hash(payload)))
    checks.append(
        ("order-independent", canonical_json_hash(payload) == canonical_json_hash({"a": 1, "b": 2}))
    )
    checks.append(
        (
            "distinguishes-content",
            canonical_json_hash({"a": 1}) != canonical_json_hash({"a": 2}),
        )
    )
    tree = directory_hash(root / "src")
    checks.append(("directory-hash-stable", directory_hash(root / "src").digest == tree.digest))
    checks.append(("directory-hash-covers-sources", len(tree.entries) > 0))

    failed = [name for name, ok in checks if not ok]
    if args.json:
        _emit_json(
            {
                "checks_executed": len(checks),
                "passed": len(checks) - len(failed),
                "failed": len(failed),
                "failed_checks": failed,
                "source_files_covered": len(tree.entries),
                "source_tree_digest": tree.digest,
            }
        )
    else:
        for name, ok in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        print(f"\nchecks executed {len(checks)}, failed {len(failed)}")
        print(f"src/ tree digest {tree.digest} over {len(tree.entries)} files")
    return EXIT_FAIL if failed else EXIT_OK


def _cmd_identity(args: argparse.Namespace) -> int:
    payload = {
        "name": "EngramFold",
        "version": __version__,
        "status": STATUS,
        "identity": IDENTITY,
        "claims_results": False,
    }
    if args.json:
        _emit_json(payload)
    else:
        print(IDENTITY)
        print(f"\nSTATUS: {STATUS}")
        print(f"version: {__version__}")
        print("This project claims no results.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = find_repo_root(args.repo)

    try:
        if args.command == "validate":
            return _cmd_validate(root, args)
        if args.command == "registry" and args.registry_command == "validate":
            return _cmd_registry_validate(root, args)
        if args.command == "provenance":
            return _cmd_provenance(root, args)
        if args.command == "hash":
            return _cmd_hash(root, args)
        if args.command == "freeze" and args.freeze_kind == "dataset":
            return _cmd_freeze_dataset(root, args)
        if args.command == "freeze" and args.freeze_kind == "experiment":
            return _cmd_freeze_experiment(root, args)
        if args.command == "verify-freeze":
            return _cmd_verify_freeze(root, args)
        if args.command == "hashing" and args.hashing_command == "selfcheck":
            return _cmd_hashing_selfcheck(root, args)
        if args.command == "identity":
            return _cmd_identity(args)
    except EngramFoldError as exc:
        # A deliberate failure of this package's own contract. Printed, not swallowed, and
        # reported as a failure so a caller cannot mistake it for success.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL

    parser.print_usage(sys.stderr)
    print(f"error: unhandled command {args.command!r}", file=sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_FAIL",
    "EXIT_OK",
    "EXIT_USAGE",
    "ROOT_MARKERS",
    "find_repo_root",
    "main",
]
