"""Execution accounting for validation gates.

A gate that returns success without doing anything is indistinguishable, from the
outside, from a gate that did the work and found nothing wrong. That is the single
most dangerous property a research repository can have, because it converts "I
verified this" into a claim nothing supports.

So a gate here must demonstrate two separate things, and is forbidden from
conflating them:

    A validation gate must prove both that its assertions passed **and** that the
    expected assertions actually executed.

Every result therefore carries an execution census alongside its errors, the census
is checked for internal consistency *before* the status is computed, and a result
whose counters disagree is itself a failure -- counters that disagree are a symptom
of the same class of bug that produces vacuous passes.

Two counters that are easy to confuse, and are kept distinct
-----------------------------------------------------------

``discovered`` / ``checked``
    *Targets*: the things this gate was pointed at (manifests, freezes, records).
``checks_executed``
    *Assertions*: the individual verifications actually performed. A gate can
    discover zero targets and still execute checks (a schema is loadable, a policy
    file is self-consistent, a directory exists); it can also discover ten targets
    and execute zero checks if it is broken.

Requiring ``checks_executed > 0`` is what makes "the gate ran" a fact rather than an
inference from an exit code. Zero executed checks is a **failure** unless the gate
explicitly declares, with a non-empty written reason, that executing none was the
legitimate outcome -- and only an ``OPTIONAL`` gate is permitted to make that
declaration at all.

Population policy
-----------------

Not every gate has a mandatory input. Each gate states which it is; the value is a
required constructor argument with no default, so a new gate cannot acquire
"optional" semantics by omission.

``REQUIRED_NONEMPTY``
    The repository contains these inputs by construction. Discovering none means
    discovery is broken, which is a hard failure.
``CONDITIONALLY_REQUIRED``
    Required while a stated precondition holds. If the precondition holds and nothing
    is discovered, that is a failure; if it does not hold, the gate is skipped with a
    reason rather than passed in silence.
``OPTIONAL``
    May legitimately have no inputs. Discovering none is recorded as a skip with a
    reason.

Statuses
--------

``PASS`` is the only success. ``BLOCKED_*`` is explicitly not success: a gate that
could not reach what it was checking reports that it could not, and the report's
overall verdict becomes blocked rather than passing. ``FAIL`` covers both content
failures and self-inconsistency.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from engramfold.errors import ValidationContractError

PASS = "PASS"
FAIL = "FAIL"

# Blocked is never a pass. A gate that could not evaluate something must not be
# readable as having evaluated it.
BLOCKED_TARGETS = "BLOCKED_TARGETS"
BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
BLOCKED_NETWORK = "BLOCKED_NETWORK"

BLOCKED_STATUSES: tuple[str, ...] = (BLOCKED_TARGETS, BLOCKED_DEPENDENCY, BLOCKED_NETWORK)

REQUIRED_NONEMPTY = "REQUIRED_NONEMPTY"
CONDITIONALLY_REQUIRED = "CONDITIONALLY_REQUIRED"
OPTIONAL = "OPTIONAL"

POLICIES: tuple[str, ...] = (REQUIRED_NONEMPTY, CONDITIONALLY_REQUIRED, OPTIONAL)

# Greppable prefixes. A reader scanning a log must be able to tell a vacuity failure
# from an accounting contradiction from a content failure without reading prose.
NONVACUOUS_PREFIX = "FAIL_NONVACUOUS"
ACCOUNTING_PREFIX = "FAIL_ACCOUNTING"
CONTRACT_PREFIX = "FAIL_CONTRACT"
COVERAGE_PREFIX = "FAIL_COVERAGE"


@dataclass
class Skip:
    """A discovered candidate deliberately not evaluated, and why."""

    id: str
    reason: str

    def render(self) -> str:
        return f"{self.id} ({self.reason})"


@dataclass
class ValidationResult:
    """What a gate discovered, what it evaluated, what it executed, and its verdict.

    ``policy`` has no default on purpose: making a gate declare its population policy
    is what stops "this gate has no inputs" from being the silent consequence of
    forgetting to think about it.
    """

    name: str
    policy: str

    discovered: int = 0
    checked: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    skipped: list[Skip] = field(default_factory=list)

    # The number of individual assertions this gate performed. Independent of how
    # many targets it found; zero is a failure unless explicitly authorised below.
    checks_executed: int = 0

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Why candidates could not be evaluated. Kept apart from ``errors`` because a
    # blocked candidate is not a failed candidate, and a gate reporting FAIL merely
    # because it could not reach the network would be lying in the other direction.
    blocked_reasons: list[str] = field(default_factory=list)

    # Gate-specific breakdown that does not fit the census.
    detail: dict[str, int] = field(default_factory=dict)

    blocked_status: str | None = None
    precondition: str | None = None

    # Set only by a gate that is legitimately allowed to execute no checks, and only
    # legal together with policy == OPTIONAL. Must be a written reason: an empty
    # string is a contract error, not an authorisation.
    allowed_empty_reason: str | None = None

    # ─ Census consistency ──────────────────────────────────────────────────

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def attempted(self) -> int:
        return self.checked + self.blocked

    def accounting_errors(self) -> list[str]:
        """Contradictions in the gate's own counters.

        These are defects in the gate, not in the data. They are failures because a
        gate whose counters do not add up cannot be trusted about anything else it
        reports, least of all that it ran.
        """
        problems: list[str] = []
        if self.discovered != self.attempted + self.skipped_count:
            problems.append(
                f"{ACCOUNTING_PREFIX}: {self.name}: discovered {self.discovered} != "
                f"checked {self.checked} + blocked {self.blocked} + skipped {self.skipped_count}"
            )
        if self.checked != self.passed + self.failed:
            problems.append(
                f"{ACCOUNTING_PREFIX}: {self.name}: checked {self.checked} != "
                f"passed {self.passed} + failed {self.failed}"
            )
        if any(not skip.reason.strip() for skip in self.skipped):
            problems.append(f"{ACCOUNTING_PREFIX}: {self.name}: a skip carries no reason")
        if self.discovered < 0 or self.checked < 0 or self.checks_executed < 0:
            problems.append(f"{ACCOUNTING_PREFIX}: {self.name}: a counter is negative")
        return problems

    def contract_errors(self) -> list[str]:
        """Misuse of the result structure itself."""
        problems: list[str] = []
        if self.policy not in POLICIES:
            problems.append(
                f"{CONTRACT_PREFIX}: {self.name}: unknown population policy "
                f"{self.policy!r}; expected one of {list(POLICIES)}"
            )
        if self.blocked_status is not None and self.blocked_status not in BLOCKED_STATUSES:
            problems.append(
                f"{CONTRACT_PREFIX}: {self.name}: unknown blocked status "
                f"{self.blocked_status!r}; expected one of {list(BLOCKED_STATUSES)}"
            )
        if self.blocked_status is not None and not any(r.strip() for r in self.blocked_reasons):
            problems.append(
                f"{CONTRACT_PREFIX}: {self.name}: reports {self.blocked_status} without "
                f"stating a blocked reason"
            )
        if self.allowed_empty_reason is not None:
            if not self.allowed_empty_reason.strip():
                problems.append(
                    f"{CONTRACT_PREFIX}: {self.name}: allowed_empty_reason is blank; an "
                    f"authorisation to execute nothing must state why"
                )
            if self.policy != OPTIONAL:
                problems.append(
                    f"{CONTRACT_PREFIX}: {self.name}: a {self.policy} gate may not be "
                    f"authorised to execute zero checks"
                )
        if (
            self.policy == CONDITIONALLY_REQUIRED
            and self.precondition is not None
            and not self.precondition.strip()
        ):
            problems.append(
                f"{CONTRACT_PREFIX}: {self.name}: precondition is blank, so the "
                f"conditional requirement cannot be evaluated"
            )
        return problems

    def vacuity_errors(self) -> list[str]:
        """Evidence that this gate proved nothing, however green it looks."""
        problems: list[str] = []

        # The primary rule: executing no checks is never a pass by default.
        authorised_empty = (
            self.allowed_empty_reason is not None
            and bool(self.allowed_empty_reason.strip())
            and self.policy == OPTIONAL
            and self.blocked_status is None
        )
        if self.checks_executed == 0 and not authorised_empty:
            if self.blocked_status is not None:
                pass  # reported as blocked, which is not a pass
            else:
                problems.append(
                    f"{NONVACUOUS_PREFIX}: {self.name}: executed 0 checks, so nothing "
                    f"was verified; if running no checks is legitimate for this gate, "
                    f"say so with allowed_empty_reason"
                )

        # A required population that discovered nothing proves nothing.
        required = self.policy == REQUIRED_NONEMPTY or (
            self.policy == CONDITIONALLY_REQUIRED and self.precondition is not None
        )
        if required and self.discovered == 0:
            trigger = f" ({self.precondition})" if self.precondition else ""
            problems.append(
                f"{NONVACUOUS_PREFIX}: {self.name}: expected targets, discovered 0{trigger}"
            )
        elif required and self.checked == 0 and self.blocked == 0:
            problems.append(
                f"{NONVACUOUS_PREFIX}: {self.name}: discovered {self.discovered} targets "
                f"and evaluated none of them"
            )
        return problems

    def all_errors(self) -> list[str]:
        """Every reason this result is not a pass, in a stable order."""
        return [
            *self.contract_errors(),
            *self.accounting_errors(),
            *self.vacuity_errors(),
            *self.errors,
        ]

    # ── Status ──────────────────────────────────────────────────────────────

    @property
    def status(self) -> str:
        if self.failed > 0 or self.all_errors():
            return FAIL
        if self.blocked_status is not None:
            return self.blocked_status
        if self.blocked > 0:
            return BLOCKED_TARGETS
        return PASS

    @property
    def ok(self) -> bool:
        return self.status == PASS

    def counts(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "checked": self.checked,
            "checks_executed": self.checks_executed,
            "passed": self.passed,
            "failed": self.failed,
            "blocked": self.blocked,
            "skipped": self.skipped_count,
            "warnings": len(self.warnings),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "policy": self.policy,
            **self.counts(),
            "precondition": self.precondition,
            "allowed_empty_reason": self.allowed_empty_reason,
            "detail": dict(sorted(self.detail.items())),
            "skipped": [{"id": skip.id, "reason": skip.reason} for skip in self.skipped],
            "blocked_reasons": list(self.blocked_reasons),
            "warnings": list(self.warnings),
            "errors": self.all_errors(),
        }

    def render(self) -> str:
        lines = [f"[{self.status}] {self.name}  {self.counts()}"]
        for key, value in sorted(self.detail.items()):
            lines.append(f"    {key}: {value}")
        lines.extend(f"    warning: {message}" for message in self.warnings)
        lines.extend(f"    skipped: {skip.render()}" for skip in self.skipped)
        lines.extend(f"    blocked: {reason}" for reason in self.blocked_reasons)
        lines.extend(f"    {error}" for error in self.all_errors())
        return "\n".join(lines)


@dataclass
class VerificationReport:
    """An ordered set of gate results, checked against the gate contract.

    The contract check is what makes "the suite ran" a claim rather than an
    assumption. A report that silently contains six of seven gates is not a report
    about the repository; it is a report about six gates. So a gate named by the
    contract and absent from the results is a failure, and a gate present but not
    named by the contract is equally an inconsistency.
    """

    contract: int
    expected_gates: Sequence[str]
    results: list[ValidationResult] = field(default_factory=list)

    def add(self, result: ValidationResult) -> ValidationResult:
        self.results.append(result)
        return result

    @property
    def ran_gates(self) -> tuple[str, ...]:
        return tuple(result.name for result in self.results)

    def missing_gates(self) -> list[str]:
        return [name for name in self.expected_gates if name not in self.ran_gates]

    def unexpected_gates(self) -> list[str]:
        return [name for name in self.ran_gates if name not in self.expected_gates]

    def duplicate_gates(self) -> list[str]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for name in self.ran_gates:
            if name in seen and name not in duplicates:
                duplicates.append(name)
            seen.add(name)
        return duplicates

    def contract_errors(self) -> list[str]:
        problems: list[str] = []
        missing = self.missing_gates()
        if missing:
            problems.append(
                f"{COVERAGE_PREFIX}: gate contract v{self.contract} names "
                f"{len(self.expected_gates)} gates; {len(missing)} did not execute: "
                f"{sorted(missing)}"
            )
        unexpected = self.unexpected_gates()
        if unexpected:
            problems.append(
                f"{CONTRACT_PREFIX}: gates executed that the contract does not name, so their "
                f"coverage is not specified: {sorted(unexpected)}"
            )
        duplicates = self.duplicate_gates()
        if duplicates:
            problems.append(
                f"{CONTRACT_PREFIX}: gates executed more than once, so the totals double-count: "
                f"{sorted(duplicates)}"
            )
        if not self.results:
            problems.append(
                f"{COVERAGE_PREFIX}: no gate executed at all, so this report is not evidence "
                f"about the repository"
            )
        return problems

    def totals(self) -> dict[str, int]:
        totals = dict.fromkeys(
            (
                "discovered",
                "checked",
                "checks_executed",
                "passed",
                "failed",
                "blocked",
                "skipped",
                "warnings",
            ),
            0,
        )
        for result in self.results:
            for key, value in result.counts().items():
                totals[key] += value
        return totals

    @property
    def overall(self) -> str:
        problems = self.contract_errors()
        if problems:
            return FAIL
        statuses = {result.status for result in self.results}
        if FAIL in statuses:
            return FAIL
        for blocked in BLOCKED_STATUSES:
            if blocked in statuses:
                return blocked
        # The report itself must have executed something. A suite of gates that each
        # legitimately executed nothing is still a suite that verified nothing.
        if self.totals()["checks_executed"] == 0:
            return FAIL
        return PASS

    def all_errors(self) -> list[str]:
        return [*self.contract_errors(), *(e for r in self.results for e in r.all_errors())]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_contract": self.contract,
            "overall": self.overall,
            "expected_gates": list(self.expected_gates),
            "ran_gates": list(self.ran_gates),
            "missing_gates": self.missing_gates(),
            "unexpected_gates": self.unexpected_gates(),
            "contract_errors": self.contract_errors(),
            "totals": self.totals(),
            "gates": [result.to_dict() for result in self.results],
        }

    def render(self) -> str:
        width = max((len(result.name) for result in self.results), default=10)
        width = max(width, len("Gate"))
        header = (
            f"{'Gate':<{width}}  {'Disc':>5} {'Chk':>5} {'Exec':>5} "
            f"{'Pass':>5} {'Fail':>5} {'Blk':>5} {'Skip':>5} {'Warn':>5}  Status"
        )
        rows = [header, "-" * len(header)]
        for result in self.results:
            counts = result.counts()
            rows.append(
                f"{result.name:<{width}}  {counts['discovered']:>5} {counts['checked']:>5} "
                f"{counts['checks_executed']:>5} {counts['passed']:>5} {counts['failed']:>5} "
                f"{counts['blocked']:>5} {counts['skipped']:>5} {counts['warnings']:>5}  "
                f"{result.status}"
            )
        body = "\n\n".join(result.render() for result in self.results)
        totals = self.totals()
        footer = (
            f"EXECUTION COVERAGE (verifier contract v{self.contract})\n"
            f"{chr(10).join(rows)}\n\n"
            f"gates expected {len(self.expected_gates)}, executed {len(self.results)}, "
            f"missing {len(self.missing_gates())}\n"
            f"totals: {totals}\n\n"
            f"overall: {self.overall}"
        )
        return f"{body}\n\n{footer}" if body else footer


def require_policy(policy: str) -> str:
    """Validate a policy name, raising on misuse.

    Raising rather than returning a status: an unknown policy is a defect in the
    repository's own code, and a gate that quietly degraded to OPTIONAL would be
    exactly the silent weakening this module exists to prevent.
    """
    if policy not in POLICIES:
        raise ValidationContractError(
            f"unknown population policy {policy!r}; expected one of {list(POLICIES)}"
        )
    return policy


def skipped_all(ids: Iterable[str], reason: str) -> list[Skip]:
    """A skip for every id, with the same written reason."""
    if not reason.strip():
        raise ValidationContractError("a skip reason must be written, not blank")
    return [Skip(id=str(item), reason=reason) for item in ids]


__all__ = [
    "ACCOUNTING_PREFIX",
    "BLOCKED_DEPENDENCY",
    "BLOCKED_NETWORK",
    "BLOCKED_STATUSES",
    "BLOCKED_TARGETS",
    "CONDITIONALLY_REQUIRED",
    "CONTRACT_PREFIX",
    "COVERAGE_PREFIX",
    "FAIL",
    "NONVACUOUS_PREFIX",
    "OPTIONAL",
    "PASS",
    "POLICIES",
    "REQUIRED_NONEMPTY",
    "Skip",
    "ValidationResult",
    "VerificationReport",
    "require_policy",
    "skipped_all",
]
