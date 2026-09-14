"""EngramFold: research integrity infrastructure.

EngramFold is an experimental research project investigating structural
consolidation of pretrained Mixture-of-Experts models and mechanisms for
preserving behavior after consolidation.

This package is the *integrity substrate*, not the research. It exists to make the
repository unable to casually lie to its own authors: canonical hashing, dataset
and experiment manifests, freezes, git and environment provenance, a registry whose
references must resolve, and validation that fails when it checks nothing.

Nothing in this package asserts anything about mixture-of-experts consolidation.
Every research question is open and unclaimed. See ``docs/METHODOLOGY.md``.
"""

from __future__ import annotations

# The status string is load-bearing, not decorative. ``registry/populations.yaml``
# declares a phase, and validation requires it to equal this value. That coupling is
# what stops the population policy from being quietly relaxed: this repository cannot
# declare that zero datasets is legitimate while still advertising itself as being
# past the pre-experiment phase.
STATUS = "PRE-EXPERIMENT / INFRASTRUCTURE ONLY"

# Narrow on purpose. The project claims to be investigating something, not to have
# established anything. Do not extend this string with a finding.
IDENTITY = (
    "EngramFold is an experimental research project investigating structural "
    "consolidation of pretrained Mixture-of-Experts models and mechanisms for "
    "preserving behavior after consolidation."
)

__version__ = "0.0.1"

__all__ = ["IDENTITY", "STATUS", "__version__"]
