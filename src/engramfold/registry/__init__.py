"""Registry: the index of the canonical research record, and its validation.

The registry maps stable ids to the documents describing them, resolves references between
those documents, and declares -- in writing, per population -- whether an empty population
is legitimate.

Validation lives in :mod:`engramfold.registry.validate`, which is also an executable
module so that ``python -m engramfold.registry.validate`` performs exactly the same work as
the ``engramfold-validate`` console script.
"""

from __future__ import annotations

from engramfold.registry.store import (
    CANONICAL_DIRECTORIES,
    CLAIM_STATUSES,
    DECLARED_POPULATIONS,
    EVIDENCE_KINDS,
    POPULATIONS_FILE,
    REGISTRY_FILES,
    PopulationPolicy,
    Populations,
    Registry,
    claim_errors,
    declared_phase,
    load_populations,
    load_registry,
    population_policy_errors,
    record_errors,
    reference_errors,
    registry_file_errors,
)

__all__ = [
    "CANONICAL_DIRECTORIES",
    "CLAIM_STATUSES",
    "DECLARED_POPULATIONS",
    "EVIDENCE_KINDS",
    "POPULATIONS_FILE",
    "REGISTRY_FILES",
    "PopulationPolicy",
    "Populations",
    "Registry",
    "claim_errors",
    "declared_phase",
    "load_populations",
    "load_registry",
    "population_policy_errors",
    "record_errors",
    "reference_errors",
    "registry_file_errors",
]
