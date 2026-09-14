"""Validation: execution accounting, the gate contract, and census construction.

Import from here rather than from the submodules, so that a caller cannot accidentally
depend on an internal detail of how a gate is assembled.
"""

from __future__ import annotations

from engramfold.validation.accounting import (
    ACCOUNTING_PREFIX,
    BLOCKED_DEPENDENCY,
    BLOCKED_NETWORK,
    BLOCKED_STATUSES,
    BLOCKED_TARGETS,
    CONDITIONALLY_REQUIRED,
    CONTRACT_PREFIX,
    COVERAGE_PREFIX,
    FAIL,
    NONVACUOUS_PREFIX,
    OPTIONAL,
    PASS,
    POLICIES,
    REQUIRED_NONEMPTY,
    Skip,
    ValidationResult,
    VerificationReport,
    require_policy,
    skipped_all,
)
from engramfold.validation.census import build_result
from engramfold.validation.contract import (
    GATE_CONTRACT,
    PHASE_0_EMPTY_POPULATIONS,
    VERIFIER_CONTRACT,
)

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
    "GATE_CONTRACT",
    "NONVACUOUS_PREFIX",
    "OPTIONAL",
    "PASS",
    "PHASE_0_EMPTY_POPULATIONS",
    "POLICIES",
    "REQUIRED_NONEMPTY",
    "VERIFIER_CONTRACT",
    "Skip",
    "ValidationResult",
    "VerificationReport",
    "build_result",
    "require_policy",
    "skipped_all",
]
