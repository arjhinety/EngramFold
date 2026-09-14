"""The gate contract: which gates must execute for a validation run to mean anything.

A validation suite is only as complete as the list of gates it is supposed to
contain. If that list lives only in the aggregator's body, then deleting a gate
deletes the requirement along with it, and the suite shrinks toward nothing while
still reporting PASS.

So the list is declared here, separately from the code that runs the gates, and
``VerificationReport`` fails when a declared gate did not execute. Removing a gate is
therefore not a way to make validation pass; it is a way to make it fail, which is the
correct direction.

Contract versioning
-------------------

``VERIFIER_CONTRACT`` is bumped when the set of gates, their discovery rules, or their
non-vacuity requirements change such that a PASS would mean something different. A
PASS recorded under an earlier contract number does not mean what a PASS under the
current one means, and no historical record is rewritten to imply otherwise.

v1
    Initial EngramFold contract. Eight gates. Every gate declares a population policy
    and a non-zero ``checks_executed`` count, or names a written reason why executing
    nothing was the legitimate outcome.
"""

from __future__ import annotations

VERIFIER_CONTRACT = 1

# The canonical gate names, in the order they are reported.
#
# Each is a distinct question about the repository:
#
#   substrate   Is the integrity infrastructure itself self-consistent? (schemas
#               registered, one hashing implementation, generated-file markers,
#               population policy legal.)
#   documents   Do the canonical documents exist where the repository says they do,
#               and do they parse?
#   registry    Are the registries well-formed, are their references resolvable, and
#               is every declared population consistent with what is actually there?
#   datasets    Do the dataset manifests satisfy their schema and their immutability
#               requirements?
#   freezes     Do the dataset freezes still describe the bytes they pinned, and does
#               the manifest each points at agree?
#   experiments Are the experiment manifests well-formed, do their references resolve,
#               and has any definition changed since it was frozen?
#   artifacts   Do the artifact manifests name inputs that resolve, and do the
#               recorded artifact hashes still match?
#   provenance  Are the recorded source revisions and environments internally
#               consistent, and does the current checkout still match them?
GATE_CONTRACT: tuple[str, ...] = (
    "substrate",
    "documents",
    "registry",
    "datasets",
    "freezes",
    "experiments",
    "artifacts",
    "provenance",
)

# Gates that may legitimately discover zero targets at this phase, together with the
# written reason. The reason is repeated here rather than referenced so that reading
# this file alone tells a reviewer which gates are currently allowed to be empty, and
# why. `tests/test_validation_contract.py` asserts this mapping and the code agree.
#
# These are authorisations to *discover nothing*, never authorisations to *check
# nothing*: each of these gates still executes its own structural checks.
PHASE_0_EMPTY_POPULATIONS: dict[str, str] = {
    "datasets": (
        "Phase 0 is infrastructure only and no dataset has been frozen yet; the gate "
        "still validates the manifest schema, the discovery mechanics, and the "
        "population policy that authorises the empty population."
    ),
    "freezes": (
        "No dataset freeze exists yet because no dataset exists yet; the gate still "
        "validates freeze-format handling and that no orphaned freeze is present."
    ),
    "experiments": (
        "No experiment definition has been frozen yet; the gate still validates the "
        "experiment-manifest schema and reference resolution."
    ),
    "artifacts": (
        "No canonical artifact has been produced yet; the gate still validates the "
        "artifact-manifest schema and the hash-verification path."
    ),
    "provenance": (
        "No artifact has been produced, so no provenance record is required; the gate "
        "still captures and validates the current checkout's provenance."
    ),
}


__all__ = ["GATE_CONTRACT", "PHASE_0_EMPTY_POPULATIONS", "VERIFIER_CONTRACT"]
