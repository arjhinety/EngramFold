"""The registry: canonical entities, their references, and the population policy.

The registry is the index of the research record. It maps stable ids to the documents
that describe them, and it is where cross-document references are resolved -- an
experiment naming a dataset, an artifact naming the experiment that made it, a report
naming the artifacts it interprets.

Two mechanisms here exist specifically to prevent a validation suite from shrinking to
nothing while still reporting success.

**References must resolve.** A reference to an id that does not exist is an error, not a
skip. Without this, deleting a record silently orphans everything that pointed at it, and
the gates that would have checked those targets simply have fewer targets to check.

**The population policy is data, and it is validated against reality.** A registry with no
records would make every record-level check vacuous, so a population that is allowed to be
empty must say so in writing (:mod:`registry/populations.yaml`), and two consistency rules
keep that declaration honest:

* declaring a population ``OPTIONAL`` while it actually contains records is a
  contradiction -- so ``OPTIONAL`` can never be used to hide real targets;
* ``OPTIONAL`` is only legal while the repository's declared phase matches
  ``engramfold.STATUS``, and that phase must also appear in ``README.md`` -- so the
  policy cannot be quietly relaxed without also changing what the repository says it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engramfold import STATUS
from engramfold.documents import Discovery, DocumentLoad, discover, load_document
from engramfold.validation.accounting import OPTIONAL, POLICIES
from engramfold.validation.fields import is_identifier, is_sha256

#: population -> registry file.
REGISTRY_FILES: dict[str, str] = {
    "datasets": "registry/datasets.yaml",
    "models": "registry/models.yaml",
    "experiments": "registry/experiments.yaml",
    "artifacts": "registry/artifacts.yaml",
    "reports": "registry/reports.yaml",
    "claims": "registry/claims.yaml",
}

#: population -> the key its records are listed under.
RECORD_KEY: dict[str, str] = {
    "datasets": "datasets",
    "models": "models",
    "experiments": "experiments",
    "artifacts": "artifacts",
    "reports": "reports",
    "claims": "claims",
}

#: Fields a record of each population must carry. `id` is included for every population
#: because every reference in the registry is by id.
REQUIRED_RECORD_FIELDS: dict[str, tuple[str, ...]] = {
    "datasets": ("id", "manifest_path"),
    "models": ("id", "license"),
    "experiments": ("id", "manifest_path"),
    "artifacts": ("id", "manifest_path"),
    "reports": ("id",),
    "claims": ("id", "statement", "status", "evidence"),
}

#: The lifecycle of a canonical claim. ``SUPPORTED`` is the only status that asserts the
#: claim holds, and it is the only one that requires resolving evidence.
CLAIM_STATUSES: tuple[str, ...] = (
    "UNSUPPORTED",  # recorded but not established; may have no evidence yet
    "SUPPORTED",  # asserted, and therefore must cite machine-readable evidence
    "WITHDRAWN",  # previously asserted, now retracted; kept as part of the record
    "CORRECTED",  # superseded by an erratum; the correction is named in evidence
)

#: Kinds of evidence a claim may cite. Prose is deliberately absent: generated prose is
#: not evidence, so a claim cannot satisfy the requirement by pointing at a sentence.
EVIDENCE_KINDS: tuple[str, ...] = (
    "artifact",
    "experiment",
    "dataset_freeze",
    "metric_file",
    "report",
)

#: Which evidence kinds are machine-readable enough to be checked automatically.
RESOLVABLE_EVIDENCE_KINDS: dict[str, str] = {
    "artifact": "artifacts",
    "experiment": "experiments",
}

#: population -> {field: target population}. A reference field names ids that must exist
#: in the target population's registry file.
REFERENCE_FIELDS: dict[str, dict[str, str]] = {
    "datasets": {},
    "models": {},
    "experiments": {
        "dataset_ids": "datasets",
        "artifact_ids": "artifacts",
    },
    "artifacts": {
        "experiment_id": "experiments",
    },
    "reports": {
        "artifact_ids": "artifacts",
        "experiment_ids": "experiments",
    },
    "claims": {},
}

#: population -> fields holding freeze ids, which must resolve to freeze documents on disk.
FREEZE_REFERENCE_FIELDS: dict[str, tuple[str, ...]] = {
    "datasets": ("freeze_ids",),
    "experiments": ("dataset_freezes",),
    "artifacts": ("dataset_freezes",),
}

#: The registry's own document.
POPULATIONS_FILE = "registry/populations.yaml"
POPULATIONS_KIND = "populations"

#: Populations that must be declared, including ones with no registry file of their own.
#: ``dataset_freezes`` and ``provenance_records`` are populated on disk rather than in a
#: registry index, but they still need a declared policy: without one there is no basis for
#: deciding whether discovering zero of them is legitimate.
DECLARED_POPULATIONS: tuple[str, ...] = (
    *REGISTRY_FILES,
    "dataset_freezes",
    "provenance_records",
)

#: gate name -> the population whose policy governs whether that gate may discover nothing.
#:
#: Gate names and population names are deliberately separate vocabularies: a gate is a
#: question, a population is a set of things. The ``freezes`` gate, for instance, is governed
#: by the ``dataset_freezes`` population -- not by a population called ``freezes``, which does
#: not exist. Spelling the mapping out means a gate cannot inherit the wrong policy by
#: accident, which is how the freeze gate in the repository this design learned from ended up
#: reporting a non-vacuity failure for an authorised-empty population.
GATE_POPULATION: dict[str, str] = {
    "datasets": "datasets",
    "freezes": "dataset_freezes",
    "experiments": "experiments",
    "artifacts": "artifacts",
    "provenance": "provenance_records",
}

#: ``OPTIONAL`` is only legal while the repository declares itself pre-experiment. The
#: point is that relaxing a population policy requires changing the project's advertised
#: status too, which is a visible act rather than a quiet edit to one YAML file.
OPTIONAL_POLICY_ALLOWED_PHASES: tuple[str, ...] = (STATUS,)

#: Directories whose contents are canonical and must therefore be tracked by git. An
#: empty canonical directory is a directory whose contents cannot be reproduced from a
#: clone, which is why the substrate gate reports it.
CANONICAL_DIRECTORIES: tuple[str, ...] = (
    "registry",
    "datasets/manifests",
    "datasets/freezes",
    "experiments/manifests",
    "experiments/freezes",
    "artifacts/manifests",
    "provenance/records",
    "reports",
    "docs",
    "tests",
    "src/engramfold",
)


@dataclass
class PopulationPolicy:
    """A written statement about whether a population may legitimately be empty."""

    name: str
    policy: str
    zero_allowed_reason: str | None = None

    def errors(self, *, origin: str) -> list[str]:
        problems: list[str] = []
        if self.policy not in POLICIES:
            problems.append(
                f"{origin}: population {self.name!r} has policy={self.policy!r}, which must be "
                f"one of {list(POLICIES)}"
            )
            return problems
        if self.policy == OPTIONAL:
            if not (self.zero_allowed_reason or "").strip():
                problems.append(
                    f"{origin}: population {self.name!r} is OPTIONAL but states no "
                    f"zero_allowed_reason; a population authorised to be empty must say why"
                )
        elif self.zero_allowed_reason is not None:
            problems.append(
                f"{origin}: population {self.name!r} is {self.policy} but also carries a "
                f"zero_allowed_reason; remove it, because a required population cannot be "
                f"authorised to be empty"
            )
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "zero_allowed_reason": self.zero_allowed_reason,
        }


@dataclass
class Populations:
    """The repository's declared phase and per-population policy."""

    phase: str
    policies: dict[str, PopulationPolicy] = field(default_factory=dict)

    def policy_for(self, population: str) -> str | None:
        entry = self.policies.get(population)
        return entry.policy if entry else None

    def empty_allowed_reason(self, population: str) -> str | None:
        """The written reason this population may be empty, or ``None`` if it may not."""
        entry = self.policies.get(population)
        if entry is None or entry.policy != OPTIONAL:
            return None
        return entry.zero_allowed_reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "policies": {name: entry.to_dict() for name, entry in sorted(self.policies.items())},
        }


@dataclass
class RegistryDocument:
    """One registry file, with its records and the failures met while loading it."""

    population: str
    load: DocumentLoad
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return self.load.is_present

    @property
    def record_count(self) -> int:
        return len(self.records)


@dataclass
class Registry:
    """Every registry file, plus the population declarations."""

    root: str
    documents: dict[str, RegistryDocument] = field(default_factory=dict)
    populations: Populations | None = None
    populations_errors: list[str] = field(default_factory=list)

    def records(self, population: str) -> list[dict[str, Any]]:
        document = self.documents.get(population)
        return document.records if document else []

    def ids(self, population: str) -> set[str]:
        return {str(record.get("id")) for record in self.records(population)}

    def document_errors(self) -> list[str]:
        errors: list[str] = []
        for document in self.documents.values():
            errors.extend(document.load.errors())
        return errors

    def total_records(self) -> int:
        return sum(document.record_count for document in self.documents.values())


def load_populations(root: Path | str) -> tuple[Populations | None, list[str]]:
    """Load ``registry/populations.yaml``.

    Returns ``(None, errors)`` when it is missing, malformed or version-incompatible. A
    missing populations file is an error rather than "no populations declared": without it
    there is no basis for deciding whether an empty population is legitimate, and defaulting
    to permissive is exactly the wrong default.
    """
    base = Path(root)
    load = load_document(base / POPULATIONS_FILE, kind=POPULATIONS_KIND)
    if load.is_absent:
        return None, [
            f"{POPULATIONS_FILE}: missing; without it there is no declared basis for whether "
            f"an empty population is legitimate"
        ]
    if load.is_error:
        return None, load.errors()
    document = load.require()

    errors: list[str] = []
    phase = document.get("phase")
    if not isinstance(phase, str) or not phase.strip():
        errors.append(f"{POPULATIONS_FILE}: phase must be a non-blank string, found {phase!r}")
    elif phase != STATUS:
        errors.append(
            f"{POPULATIONS_FILE}: phase={phase!r} does not match the repository's declared "
            f"status {STATUS!r}; the population policy and the advertised project status must "
            f"agree, so that relaxing one is visible as changing the other"
        )

    raw = document.get("populations")
    if not isinstance(raw, dict):
        errors.append(
            f"{POPULATIONS_FILE}: populations must be a mapping, found {type(raw).__name__}"
        )
        return None, errors

    policies: dict[str, PopulationPolicy] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            errors.append(
                f"{POPULATIONS_FILE}: populations[{name!r}] must be a mapping, found "
                f"{type(entry).__name__}"
            )
            continue
        unknown = sorted(set(entry) - {"policy", "zero_allowed_reason"})
        if unknown:
            errors.append(
                f"{POPULATIONS_FILE}: populations[{name!r}] has unknown key(s) {unknown}; "
                f"known keys are ['policy', 'zero_allowed_reason']"
            )
        policy_entry = PopulationPolicy(
            name=str(name),
            policy=str(entry.get("policy")),
            zero_allowed_reason=entry.get("zero_allowed_reason"),
        )
        policies[str(name)] = policy_entry
        errors.extend(policy_entry.errors(origin=POPULATIONS_FILE))

    for name in DECLARED_POPULATIONS:
        if name not in policies:
            errors.append(
                f"{POPULATIONS_FILE}: population {name!r} is not declared; every population the "
                f"registry can hold must state whether it may be empty, because an undeclared "
                f"population has no defined empty-semantics"
            )
    for name in sorted(set(policies) - set(DECLARED_POPULATIONS)):
        errors.append(
            f"{POPULATIONS_FILE}: population {name!r} is declared but is not a population this "
            f"build knows about; expected {list(DECLARED_POPULATIONS)}"
        )

    if phase != STATUS and any(entry.policy == OPTIONAL for entry in policies.values()):
        optional_names = sorted(n for n, e in policies.items() if e.policy == OPTIONAL)
        errors.append(
            f"{POPULATIONS_FILE}: phase={phase!r} with OPTIONAL populations {optional_names}; "
            f"OPTIONAL is only permitted while phase is one of "
            f"{list(OPTIONAL_POLICY_ALLOWED_PHASES)}. Once the repository leaves the "
            f"pre-experiment phase, an empty population must be a failure rather than an "
            f"authorised outcome"
        )

    populations = Populations(phase=str(phase), policies=policies)
    return populations, errors


def load_registry(root: Path | str) -> Registry:
    """Load every registry file, keeping per-file load state."""
    base = Path(root)
    registry = Registry(root=base.as_posix())
    for population, relative in REGISTRY_FILES.items():
        load = load_document(base / relative, kind="registry")
        records: list[dict[str, Any]] = []
        if load.is_present:
            value = load.value or {}
            raw = value.get(RECORD_KEY[population])
            if isinstance(raw, list):
                records = [record for record in raw if isinstance(record, dict)]
            elif raw is not None:
                records = []
        registry.documents[population] = RegistryDocument(
            population=population, load=load, records=records
        )
    populations, population_errors = load_populations(base)
    registry.populations = populations
    registry.populations_errors = population_errors
    return registry


def registry_file_errors(registry: Registry) -> list[str]:
    """Errors about the registry files themselves: load state and record shape.

    A file that is missing, malformed, or version-incompatible is an error here. The
    distinction is preserved in the message, so "the registry is not there" and "the
    registry is there and unusable" read differently.
    """
    errors: list[str] = []
    for population, document in registry.documents.items():
        relative = REGISTRY_FILES[population]
        if document.load.is_absent:
            errors.append(
                f"{population}: registry file {relative} is missing, so this population cannot "
                f"be enumerated at all"
            )
            continue
        if document.load.is_error:
            errors.extend(document.load.errors(label=population))
            continue

        value = document.load.value or {}
        raw = value.get(RECORD_KEY[population])
        if not isinstance(raw, list):
            errors.append(
                f"{population}: {relative} must list records under {RECORD_KEY[population]!r}, "
                f"found {type(raw).__name__}"
            )
            continue
        for index, record in enumerate(raw):
            if not isinstance(record, dict):
                errors.append(
                    f"{population}: {relative} record {index} is not a mapping, found "
                    f"{type(record).__name__}"
                )
    return errors


def record_errors(registry: Registry) -> list[str]:
    """Per-record shape errors: ids, duplicates, required fields."""
    errors: list[str] = []
    for population, document in registry.documents.items():
        if not document.present:
            continue
        seen: dict[str, int] = {}
        for record in document.records:
            record_id = record.get("id")
            if not is_identifier(record_id):
                errors.append(
                    f"{population}:{record_id!r}: id must match [a-z0-9][a-z0-9._-]*, found "
                    f"{record_id!r}"
                )
                continue
            key = str(record_id)
            if key in seen:
                errors.append(
                    f"{population}:{key}: duplicate id in {REGISTRY_FILES[population]} "
                    f"(also at record index {seen[key]})"
                )
            seen.setdefault(key, len(seen))
            for field_name in REQUIRED_RECORD_FIELDS[population]:
                if field_name not in record or record[field_name] in (None, "", []):
                    errors.append(
                        f"{population}:{key}: required field {field_name!r} is missing or empty"
                    )
    return errors


def reference_errors(registry: Registry, root: Path | str) -> list[str]:
    """Every reference that does not resolve.

    Deliberately includes the *reverse* direction for freeze ids and manifest paths: a
    reference pointing at nothing is an error, and so is a declared document that no
    record references, because the latter means a canonical file is unreachable from the
    index.
    """
    base = Path(root)
    errors: list[str] = []
    population_ids = {name: registry.ids(name) for name in REGISTRY_FILES}

    for population, fields in REFERENCE_FIELDS.items():
        for record in registry.records(population):
            record_id = str(record.get("id"))
            for field_name, target in fields.items():
                if field_name not in record:
                    continue
                value = record[field_name]
                targets = value if isinstance(value, list) else [value]
                for item in targets:
                    if item in (None, ""):
                        errors.append(
                            f"{population}:{record_id}: {field_name} contains an empty reference"
                        )
                        continue
                    if str(item) not in population_ids.get(target, set()):
                        errors.append(
                            f"{population}:{record_id}: {field_name} references {item!r}, which "
                            f"does not exist in the {target} registry; a dangling reference means "
                            f"whatever it pointed at is no longer checked by anything"
                        )

    for population, freeze_field_names in FREEZE_REFERENCE_FIELDS.items():
        for record in registry.records(population):
            record_id = str(record.get("id"))
            for field_name in freeze_field_names:
                value = record.get(field_name)
                if value is None:
                    continue
                if not isinstance(value, list):
                    errors.append(
                        f"{population}:{record_id}: {field_name} must be a list of freeze ids, "
                        f"found {type(value).__name__}"
                    )
                    continue
                for item in value:
                    if not is_sha256(item):
                        errors.append(
                            f"{population}:{record_id}: {field_name} entry {item!r} is not a "
                            f"sha256 freeze id"
                        )
                        continue
                    freeze_path = base / "datasets/freezes" / f"{item}.json"
                    if not freeze_path.is_file():
                        errors.append(
                            f"{population}:{record_id}: {field_name} references freeze {item!r}, "
                            f"but datasets/freezes/{item}.json does not exist"
                        )

    for population in ("datasets", "experiments", "artifacts"):
        for record in registry.records(population):
            record_id = str(record.get("id"))
            manifest_path = record.get("manifest_path")
            if not isinstance(manifest_path, str) or not manifest_path.strip():
                continue
            if not (base / manifest_path).is_file():
                errors.append(
                    f"{population}:{record_id}: manifest_path={manifest_path!r} does not resolve "
                    f"to a file; the record indexes a document that is not there"
                )
    return errors


def claim_errors(registry: Registry, root: Path | str) -> list[str]:
    """Validate canonical claims and the evidence they cite.

    This is the claim/evidence discipline in executable form. The rule it enforces is the
    repository's stated one -- generated prose is not evidence, canonical machine-readable
    artifacts are -- so a claim asserting that something holds must cite evidence that is
    itself an artifact, an experiment, a freeze, a metrics file or a report, and the
    reference must resolve.

    ``UNSUPPORTED`` is a legitimate status: recording a claim that is not yet established is
    honest and useful. What is not legitimate is a ``SUPPORTED`` claim with no resolvable
    evidence, which is the mechanism by which an assertion becomes a finding by repetition.
    """
    base = Path(root)
    errors: list[str] = []
    for record in registry.records("claims"):
        record_id = str(record.get("id"))
        status = record.get("status")
        if status not in CLAIM_STATUSES:
            errors.append(
                f"claims:{record_id}: status={status!r} must be one of {list(CLAIM_STATUSES)}"
            )
            continue
        evidence = record.get("evidence")
        if not isinstance(evidence, list):
            errors.append(
                f"claims:{record_id}: evidence must be a list of evidence references, found "
                f"{type(evidence).__name__}"
            )
            continue
        if status == "SUPPORTED" and not evidence:
            errors.append(
                f"claims:{record_id}: status=SUPPORTED with no evidence; a supported claim must "
                f"cite machine-readable evidence, because generated prose is not evidence"
            )
        for index, entry in enumerate(evidence):
            if not isinstance(entry, dict):
                errors.append(
                    f"claims:{record_id}: evidence[{index}] must be a mapping with 'kind' and 'ref'"
                )
                continue
            kind = entry.get("kind")
            reference = entry.get("ref")
            if kind not in EVIDENCE_KINDS:
                errors.append(
                    f"claims:{record_id}: evidence[{index}].kind={kind!r} must be one of "
                    f"{list(EVIDENCE_KINDS)}; prose is not an evidence kind"
                )
                continue
            if not isinstance(reference, str) or not reference.strip():
                errors.append(
                    f"claims:{record_id}: evidence[{index}].ref must be a non-blank reference"
                )
                continue
            target_population = RESOLVABLE_EVIDENCE_KINDS.get(str(kind))
            if target_population and reference not in registry.ids(target_population):
                errors.append(
                    f"claims:{record_id}: evidence[{index}] references {reference!r}, which does "
                    f"not exist in the {target_population} registry"
                )
            if kind == "dataset_freeze" and not is_sha256(reference):
                errors.append(
                    f"claims:{record_id}: evidence[{index}].ref must be a freeze id, found "
                    f"{reference!r}"
                )
            if kind == "metric_file" and not (base / reference).is_file():
                errors.append(
                    f"claims:{record_id}: evidence[{index}].ref={reference!r} does not resolve to "
                    f"a file; a claim cannot rest on a metrics file that is not there"
                )
    return errors


def population_policy_errors(registry: Registry) -> list[str]:
    """Reconcile the declared population policy against what the registry contains.

    The two consistency rules that stop ``OPTIONAL`` from hiding real targets:

    * ``OPTIONAL`` while records exist -> contradiction;
    * ``REQUIRED_NONEMPTY`` while no records exist -> the required population is empty.

    Both directions matter. The first stops a policy from being used to excuse skipping
    work that exists; the second stops a policy from being aspirational while nothing is
    actually being enforced.
    """
    errors: list[str] = list(registry.populations_errors)
    populations = registry.populations
    if populations is None:
        return errors

    for population in DECLARED_POPULATIONS:
        entry = populations.policies.get(population)
        if entry is None or entry.policy not in POLICIES:
            continue
        count = _population_size(registry, population)
        if entry.policy == OPTIONAL and count > 0:
            errors.append(
                f"populations.yaml: population {population!r} is declared OPTIONAL but the "
                f"registry contains {count} record(s); an OPTIONAL policy must not be used to "
                f"authorise an empty validation run over a populated registry"
            )
        if entry.policy != OPTIONAL and count == 0:
            errors.append(
                f"populations.yaml: population {population!r} is declared {entry.policy} but "
                f"contains no records; a required population that is empty means either the "
                f"registry lost its contents or the policy is wrong"
            )
    return errors


def _population_size(registry: Registry, population: str) -> int:
    """How many records a population actually holds, including ones on disk."""
    if population == "provenance_records":
        return len(
            _found(discover(registry.root, "provenance/records/*.json", kind="provenance_record"))
        )
    if population == "dataset_freezes":
        return len(
            _found(discover(registry.root, "datasets/freezes/*.json", kind="dataset_freeze"))
        )
    document = registry.documents.get(population)
    return document.record_count if document else 0


def _found(discovery: Discovery) -> list[DocumentLoad]:
    return discovery.present


def declared_phase(root: Path | str) -> str | None:
    """The ``STATUS:`` line advertised by ``README.md``.

    Markdown decoration is stripped before matching, so ``**STATUS: X**``, ``## STATUS: X``
    and a plain ``STATUS: X`` all resolve to ``X``. Being strict about the decoration would
    make the convention about formatting rather than about the statement, and the statement
    is what couples the population policy to the advertised phase.
    """
    readme = Path(root) / "README.md"
    if not readme.is_file():
        return None
    try:
        text = readme.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        stripped = line.strip().lstrip("#>*-_ ").strip()
        if stripped.upper().startswith("STATUS:"):
            return stripped.split(":", 1)[1].strip().strip("*_` ").strip()
    return None


def phase_errors(root: Path | str, populations: Populations | None) -> list[str]:
    """The advertised phase must match the declared one, and be visible in the README."""
    errors: list[str] = []
    advertised = declared_phase(root)
    if advertised is None:
        errors.append(
            "README.md: no 'STATUS:' line found; the repository must advertise its phase, because "
            "the population policy is only permitted to authorise empty populations while that "
            "phase is pre-experiment"
        )
        return errors
    if populations is None:
        return errors
    if advertised != populations.phase:
        errors.append(
            f"README.md: advertises STATUS: {advertised}, but registry/populations.yaml declares "
            f"phase {populations.phase!r}; the two must agree"
        )
    return errors


def reference_counts(registry: Registry) -> dict[str, int]:
    """How many reference edges were examined, per population, for the census."""
    counts: dict[str, int] = {}
    for population, fields in REFERENCE_FIELDS.items():
        total = 0
        for record in registry.records(population):
            for field_name in fields:
                value = record.get(field_name)
                if isinstance(value, list):
                    total += len(value)
                elif value not in (None, ""):
                    total += 1
        counts[population] = total
    return counts


__all__ = [
    "CANONICAL_DIRECTORIES",
    "CLAIM_STATUSES",
    "DECLARED_POPULATIONS",
    "EVIDENCE_KINDS",
    "FREEZE_REFERENCE_FIELDS",
    "GATE_POPULATION",
    "OPTIONAL_POLICY_ALLOWED_PHASES",
    "POPULATIONS_FILE",
    "POPULATIONS_KIND",
    "RECORD_KEY",
    "REFERENCE_FIELDS",
    "REGISTRY_FILES",
    "REQUIRED_RECORD_FIELDS",
    "RESOLVABLE_EVIDENCE_KINDS",
    "PopulationPolicy",
    "Populations",
    "Registry",
    "RegistryDocument",
    "claim_errors",
    "declared_phase",
    "load_populations",
    "load_registry",
    "phase_errors",
    "population_policy_errors",
    "record_errors",
    "reference_counts",
    "reference_errors",
    "registry_file_errors",
]
