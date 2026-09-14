"""Turning a discovered population and a flat error list into a consistent census.

Gates in this repository are written in the natural style: discover a population, then
run checks that produce error *strings* naming the target they are about. This module
converts that into the census ``ValidationResult`` requires, without letting the
conversion itself become a place where errors are lost.

Three properties matter, and each corresponds to a way a census can lie:

**No error may vanish.** An error naming a target that was not in the discovered
population is not discarded and not treated as a gate-level note. It is attributed to a
synthetic sentinel target that counts as failed, so an unattributable error *increases*
the failed count rather than silently ceasing to exist. Losing an error is the
mechanism by which a failing check becomes a passing gate.

**Counters must be derivable.** ``passed`` is computed, not supplied, so a caller
cannot assert five passes over three checked targets. ``failed`` is the remainder. The
result is self-consistent by construction, and ``accounting_errors`` then only fires
for genuinely contradictory caller input -- which is exactly when it should.

**Skips and blocks are populated, not removed.** A skipped or blocked target stays in
``discovered``. Dropping it would make the population shrink to whatever happened to be
evaluated, which is how "everything was skipped" reads as "everything passed".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from engramfold.validation.accounting import (
    BLOCKED_STATUSES,
    OPTIONAL,
    Skip,
    ValidationResult,
    require_policy,
)


def _sentinel(name: str) -> str:
    """The synthetic target that absorbs gate-level errors."""
    return f"<gate:{name}>"


def attribute_errors(
    target_ids: Sequence[str], errors: Iterable[str], *, gate: str
) -> tuple[dict[str, list[str]], list[str]]:
    """Group error strings by the target they name.

    Attribution is an exact prefix match against ids that were genuinely discovered,
    not open-ended parsing. Anything unmatched is returned separately so the caller
    can account for it explicitly instead of dropping it.
    """
    known = list(dict.fromkeys(target_ids))
    attributed: dict[str, list[str]] = {target: [] for target in known}
    unattributed: list[str] = []
    # Longest ids first: with ids `a` and `a.b` both discovered, the error
    # "a.b: something" must attribute to `a.b`, not to `a`.
    ordered = sorted(known, key=len, reverse=True)
    for message in errors:
        for target in ordered:
            if message.startswith(f"{target}:"):
                attributed[target].append(message)
                break
        else:
            unattributed.append(message)
    return attributed, unattributed


def build_result(
    name: str,
    policy: str,
    target_ids: Sequence[str],
    errors: Iterable[str],
    *,
    checks_executed: int,
    skipped: Sequence[Skip] = (),
    blocked_ids: Sequence[str] = (),
    blocked_reasons: Sequence[str] = (),
    blocked_status: str | None = None,
    detail: dict[str, int] | None = None,
    precondition: str | None = None,
    allowed_empty_reason: str | None = None,
    warnings: Sequence[str] = (),
    gate_level_errors: Sequence[str] = (),
) -> ValidationResult:
    """Build a census for one gate.

    ``checks_executed`` is required and keyword-only: a gate has to state how many
    assertions it performed, because that number -- not the exit code -- is what makes
    "this gate ran" checkable. Passing ``0`` is legal and is then either a failure or a
    declared authorisation, decided by ``policy`` and ``allowed_empty_reason``.

    ``gate_level_errors`` are errors that are about the gate's own preconditions rather
    than about a discovered target (a registry file that would not parse, say). They are
    recorded as errors and, because they are not attributable, they also produce a
    failed sentinel target -- so a gate cannot report ``failed == 0`` while carrying
    gate-level errors.
    """
    require_policy(policy)
    if blocked_status is not None and blocked_status not in BLOCKED_STATUSES:
        raise ValueError(f"unknown blocked status {blocked_status!r}")

    skipped_list = list(skipped)
    blocked_list = list(dict.fromkeys(blocked_ids))
    skipped_ids = [item.id for item in skipped_list]

    # Duplicate target ids would make the census ambiguous: two entries for one id
    # cannot be counted as two targets without overstating coverage.
    duplicates = sorted({i for i in target_ids if list(target_ids).count(i) > 1})
    duplicate_errors = (
        [
            f"{_sentinel(name)}: duplicate target ids in the discovered population, so "
            f"the census cannot be computed: {duplicates}"
        ]
        if duplicates
        else []
    )

    # A skipped or blocked target is still a discovered target. Folding any that the
    # caller listed separately back into the population keeps the census honest rather
    # than letting a bookkeeping slip become an accounting failure.
    population: list[str] = list(dict.fromkeys(target_ids))
    for extra in [*skipped_ids, *blocked_list]:
        if extra not in population:
            population.append(extra)

    skipped_set = set(skipped_ids)
    blocked_set = set(blocked_list)
    checked_ids = [i for i in population if i not in skipped_set and i not in blocked_set]

    all_errors = [*duplicate_errors, *gate_level_errors, *errors]
    attributed, unattributed = attribute_errors(checked_ids, all_errors, gate=name)

    # Derived, never asserted: a target that carries no attributed error passed.
    failed_ids = [target for target in checked_ids if attributed.get(target)]
    if unattributed:
        sentinel = _sentinel(name)
        if sentinel not in population:
            population.append(sentinel)
        checked_ids = [*checked_ids, sentinel]
        failed_ids.append(sentinel)
        attributed[sentinel] = unattributed

    passed = sum(1 for target in checked_ids if target not in failed_ids)
    failed = len(checked_ids) - passed

    return ValidationResult(
        name=name,
        policy=policy,
        discovered=len(population),
        checked=len(checked_ids),
        passed=passed,
        failed=failed,
        blocked=len(blocked_list),
        skipped=skipped_list,
        checks_executed=checks_executed,
        errors=all_errors,
        warnings=list(warnings),
        blocked_reasons=list(blocked_reasons),
        detail=dict(detail or {}),
        blocked_status=blocked_status,
        precondition=precondition,
        allowed_empty_reason=allowed_empty_reason,
    )


def empty_population_result(
    name: str,
    *,
    checks_executed: int,
    reason: str,
    detail: dict[str, int] | None = None,
    warnings: Sequence[str] = (),
) -> ValidationResult:
    """A gate whose population is legitimately empty, with the reason written down.

    ``policy`` is forced to ``OPTIONAL`` and the reason must be non-blank, so this is a
    narrow, self-describing constructor rather than an escape hatch: any caller using it
    also had to write a sentence explaining why finding nothing was correct.
    """
    if not reason.strip():
        raise ValueError(
            f"{name}: an empty population must state a reason; "
            f"authorising zero checks without one is not permitted"
        )
    return ValidationResult(
        name=name,
        policy=OPTIONAL,
        discovered=0,
        checked=0,
        checks_executed=checks_executed,
        detail=dict(detail or {}),
        warnings=list(warnings),
        allowed_empty_reason=reason,
    )


def summarise(results: Iterable[ValidationResult]) -> dict[str, Any]:
    """A compact machine-readable summary of a set of gate results."""
    materialised = list(results)
    return {
        "gates": len(materialised),
        "failed_gates": [r.name for r in materialised if r.status != "PASS"],
        "totals": {
            key: sum(r.counts()[key] for r in materialised)
            for key in (
                "discovered",
                "checked",
                "checks_executed",
                "passed",
                "failed",
                "blocked",
                "skipped",
                "warnings",
            )
        },
    }


__all__ = [
    "attribute_errors",
    "build_result",
    "empty_population_result",
    "summarise",
]
