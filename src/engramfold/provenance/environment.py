"""Deterministic environment capture, with secrets redacted by construction.

An artifact that cannot say what produced it is not reproducible evidence. This module
records the software and hardware environment -- operating system, interpreter, package
versions, accelerators, container identity -- in a form that is stable enough to hash
and complete enough to diagnose a divergence.

Three rules shape it.

**Capture by allowlist, never by dumping the environment.** ``os.environ`` routinely
contains API tokens, cloud credentials and session cookies. Recording it wholesale and
then trying to scrub it is a losing game, because the scrub has to anticipate every
secret. Instead only variables matching a stated relevance pattern are considered, and
each candidate is passed through redaction. Names of withheld variables are recorded,
so the record shows that something was withheld without showing what.

**Hardware differences are recorded, not treated as failures.** EngramFold will
eventually run on heterogeneous hardware. A different GPU model is a fact about the
run, not evidence that the run is wrong. :func:`compare_environments` therefore
*classifies* differences instead of rejecting them, and only the caller decides whether
a given class of difference matters for the comparison at hand.

**No timestamps or hostnames inside the identity.** ``captured_at`` and ``hostname``
are recorded for diagnostics; ``manifest_hash`` strips them, so two captures of an
unchanged machine produce the same ``environment_manifest_hash``.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from engramfold.hashing import canonical_json_hash, manifest_hash

#: Bumped when the set of captured fields or their meaning changes.
ENVIRONMENT_MANIFEST_VERSION = 1

# ── Redaction ────────────────────────────────────────────────────────────────

# Substrings that mark a variable as secret-shaped. Matched case-insensitively against
# the variable *name*. Kept broad on purpose: withholding a harmless value costs a
# diagnostic detail, while recording a live token costs a credential.
SECRET_NAME_MARKERS: tuple[str, ...] = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE_KEY",
    "APIKEY",
    "API_KEY",
    "ACCESS_KEY",
    "AUTH",
    "SESSION",
    "COOKIE",
    "SIGNATURE",
    "BEARER",
    "LICENSE_KEY",
)

# Variables worth recording when present. A relevance allowlist rather than a
# denylist: the default for an unlisted variable is "do not record it".
RELEVANT_ENV_PATTERNS: tuple[str, ...] = (
    r"^CUDA_",
    r"^CUBLAS_",
    r"^CUDNN_",
    r"^NCCL_",
    r"^NVIDIA_",
    r"^ROCR_",
    r"^HIP_",
    r"^AMD_",
    r"^TORCH_",
    r"^PYTORCH_",
    r"^OMP_",
    r"^MKL_",
    r"^OPENBLAS_",
    r"^PYTHONHASHSEED$",
    r"^PYTHONPATH$",
    r"^PYTHONDONTWRITEBYTECODE$",
    r"^VIRTUAL_ENV$",
    r"^CONDA_",
    r"^HF_",
    r"^HUGGINGFACE_",
    r"^TRANSFORMERS_",
    r"^DATASETS_",
    r"^SLURM_",
    r"^ENGRAMFOLD_",
    r"^SOURCE_DATE_EPOCH$",
    r"^TZ$",
    r"^LANG$",
    r"^LC_ALL$",
)

REDACTED = "<redacted>"

# A URL carrying inline credentials, e.g. https://user:token@host/path.
_CREDENTIAL_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
# A long opaque token. The alphabet includes ``.`` so that JWT-shaped credentials -- three
# base64url segments joined by dots -- are caught; without it the most common structured token
# format would pass straight through. The cost is that a long dotted identifier (a module path,
# say) is also withheld. That is the right direction to err: withholding a harmless diagnostic
# loses one detail, while recording a live credential cannot be undone.
_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-+/=.]{32,}$")


def is_secret_shaped_name(name: str) -> bool:
    """Whether a variable *name* marks its value as secret."""
    upper = name.upper()
    return any(marker in upper for marker in SECRET_NAME_MARKERS)


def looks_like_secret_value(value: str) -> bool:
    """Whether a value itself looks like a credential.

    Catches the case the name-based rule cannot: a credential stored under an innocent
    variable name. Applies to values that were already selected by the relevance
    allowlist, so the cost of a false positive is one missing diagnostic detail.
    """
    stripped = value.strip()
    if not stripped:
        return False
    if _CREDENTIAL_URL_RE.match(stripped):
        return True
    # A long opaque string is withheld. An absolute path is exempt: it is a legitimate
    # diagnostic value rather than a token, and the leading slash distinguishes the two.
    return bool(_OPAQUE_TOKEN_RE.match(stripped)) and not stripped.startswith("/")


def capture_environment_variables(
    environ: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """The relevant, non-secret environment variables, plus the withheld names.

    Returns ``(recorded, withheld_names)``. The withheld list is sorted and holds names
    only -- enough to show that something was withheld and to notice it is missing on a
    re-run, without disclosing the value.
    """
    source = dict(os.environ if environ is None else environ)
    patterns = [re.compile(pattern) for pattern in RELEVANT_ENV_PATTERNS]
    recorded: dict[str, str] = {}
    withheld: list[str] = []

    for name in sorted(source):
        if is_secret_shaped_name(name):
            withheld.append(name)
            continue
        if not any(pattern.match(name) for pattern in patterns):
            continue
        value = source[name]
        if looks_like_secret_value(value):
            withheld.append(name)
            recorded[name] = REDACTED
            continue
        recorded[name] = value
    return recorded, sorted(set(withheld))


def redact_environment_variables(
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """A fully redacted view of an environment mapping.

    Provided for callers that hold an environment mapping and must never emit it
    verbatim. Every secret-shaped name maps to ``<redacted>``, so the result is safe to
    print; use :func:`capture_environment_variables` when a relevance filter is also
    wanted.
    """
    source = dict(os.environ if environ is None else environ)
    return {
        name: (REDACTED if is_secret_shaped_name(name) or looks_like_secret_value(value) else value)
        for name, value in sorted(source.items())
    }


# ── Probes ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Probe:
    """A best-effort observation that records *why* it is unknown when it is.

    Every probe returns one of these, so "not available on this machine" is a recorded
    fact with a reason rather than a ``None`` a reader has to interpret.
    """

    available: bool
    value: Any = None
    reason: str | None = None

    def to_dict(self, key: str = "value") -> dict[str, Any]:
        payload: dict[str, Any] = {"available": self.available}
        if self.available:
            payload[key] = self.value
        else:
            payload["reason"] = self.reason or "unavailable"
        return payload


def _subprocess_probe(command: list[str]) -> Probe:
    """Run a probe command, treating absence of the tool as unavailability."""
    if shutil.which(command[0]) is None:
        return Probe(available=False, reason=f"{command[0]} is not installed")
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return Probe(available=False, reason=f"{command[0]} could not be run: {exc}")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        return Probe(available=False, reason=f"{command[0]} exited {result.returncode}: {detail}")
    return Probe(available=True, value=result.stdout.decode("utf-8", errors="replace").strip())


def probe_cuda() -> Probe:
    """CUDA driver and toolkit version, via ``nvidia-smi``.

    Read from the driver rather than by importing a framework: this must work on a
    machine with a GPU and no PyTorch installed, and must not import a 2 GB package to
    answer a version question.
    """
    return _subprocess_probe(
        [
            "nvidia-smi",
            "--query-gpu=driver_version",
            "--format=csv,noheader",
        ]
    )


def probe_rocm() -> Probe:
    """ROCm version, via ``rocminfo`` or the version file."""
    tool = _subprocess_probe(["rocminfo"])
    if tool.available:
        match = re.search(r"ROCm\s+version[:\s]+([0-9][0-9A-Za-z.\-]*)", str(tool.value))
        if match:
            return Probe(available=True, value=match.group(1))
        return Probe(available=True, value="present (version string not recognised)")
    version_file = Path("/opt/rocm/.info/version")
    if version_file.is_file():
        try:
            return Probe(available=True, value=version_file.read_text(encoding="utf-8").strip())
        except OSError as exc:
            return Probe(available=False, reason=f"ROCm version file unreadable: {exc}")
    return Probe(available=False, reason=tool.reason or "ROCm not detected")


def probe_gpus() -> Probe:
    """Per-GPU model, memory and driver, via ``nvidia-smi``.

    Returns a list of records so a heterogeneous or multi-GPU host is described
    accurately rather than collapsed into one sum.
    """
    result = _subprocess_probe(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader",
        ]
    )
    if not result.available:
        return Probe(available=False, reason=result.reason)
    devices: list[dict[str, Any]] = []
    for line in str(result.value).splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        while len(parts) < 4:
            parts.append("")
        devices.append(
            {
                "model": parts[0],
                "memory_total": parts[1],
                "driver_version": parts[2],
                "compute_capability": parts[3],
            }
        )
    if not devices:
        return Probe(available=False, reason="nvidia-smi reported no devices")
    return Probe(available=True, value=devices)


def probe_cpu() -> Probe:
    """A human-readable CPU description, best effort per platform."""
    if sys.platform.startswith("linux"):
        try:
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return Probe(available=False, reason=f"/proc/cpuinfo unreadable: {exc}")
        for line in text.splitlines():
            if line.lower().startswith("model name"):
                return Probe(available=True, value=line.split(":", 1)[1].strip())
        return Probe(available=False, reason="no 'model name' line in /proc/cpuinfo")
    processor = platform.processor()
    if processor:
        return Probe(available=True, value=processor)
    return Probe(available=False, reason="platform.processor() is empty on this platform")


def probe_ram_bytes() -> Probe:
    """Total physical memory in bytes, best effort per platform."""
    if sys.platform.startswith("linux"):
        try:
            text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return Probe(available=False, reason=f"/proc/meminfo unreadable: {exc}")
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.MULTILINE)
        if match:
            return Probe(available=True, value=int(match.group(1)) * 1024)
        return Probe(available=False, reason="MemTotal not found in /proc/meminfo")
    if sys.platform == "win32":  # pragma: no cover - exercised on Windows hosts only
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            # `getattr` rather than `ctypes.windll`: `windll` exists only on Windows, and a
            # direct attribute reference is a type error on any other platform -- including
            # the Linux CI that runs this repository's own checks.
            windll = getattr(ctypes, "windll", None)
            if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return Probe(available=True, value=int(status.ullTotalPhys))
        except (OSError, AttributeError, ValueError) as exc:
            return Probe(available=False, reason=f"GlobalMemoryStatusEx failed: {exc}")
    return Probe(available=False, reason="no RAM probe for this platform")


def probe_packages() -> Probe:
    """Every installed distribution and version, sorted by name.

    Read from installed metadata rather than by importing anything, so capture stays fast
    and cannot be affected by a package's import side effects.

    A distribution whose metadata is malformed is *recorded* under
    ``value["unreadable_distributions"]`` rather than skipped. Silently dropping it would
    make the package list quietly incomplete, which is the kind of omission nobody notices
    until it matters. The ``substrate`` gate forbids an exception handler that discards a
    failure without recording it, so this is enforced rather than merely intended.
    """
    versions: dict[str, str] = {}
    unreadable: list[str] = []
    try:
        distributions = list(metadata.distributions())
    except (OSError, ValueError, TypeError) as exc:
        return Probe(
            available=False,
            reason=f"installed distributions could not be enumerated: {exc}",
        )

    for distribution in distributions:
        try:
            name = distribution.metadata["Name"]
            version = distribution.version
        except (KeyError, TypeError, ValueError) as exc:
            unreadable.append(f"{type(distribution).__name__}: {type(exc).__name__}: {exc}")
            continue
        if not name:
            unreadable.append(f"{type(distribution).__name__}: metadata carries no Name")
            continue
        # First occurrence wins; duplicate names in site-packages are common and the ordering
        # of `distributions()` is not guaranteed.
        versions.setdefault(str(name), str(version))

    return Probe(
        available=True,
        value={
            "versions": dict(sorted(versions.items())),
            "unreadable_distributions": sorted(unreadable),
        },
    )


#: Core packages whose versions are worth surfacing without searching the full list.
CORE_PACKAGES: tuple[str, ...] = (
    "torch",
    "transformers",
    "datasets",
    "accelerate",
    "peft",
    "trl",
    "safetensors",
    "numpy",
    "pyyaml",
    "pytest",
    "ruff",
    "mypy",
)


def probe_container() -> Probe:
    """Container image identity, when the process is inside one.

    Records the image reference and, when the environment exposes it, the immutable
    digest. The digest is what pins an image; a tag alone does not, and the difference
    is recorded rather than blurred.
    """
    image = os.environ.get("ENGRAMFOLD_CONTAINER_IMAGE") or os.environ.get("CONTAINER_IMAGE")
    digest = os.environ.get("ENGRAMFOLD_CONTAINER_DIGEST") or os.environ.get("CONTAINER_DIGEST")
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    if not image and not in_container:
        return Probe(available=False, reason="not detected inside a container")
    return Probe(
        available=True,
        value={
            "image": image or None,
            "digest": digest or None,
            # A tag is mutable and a digest is not; a reader must be able to tell which
            # of the two is all that was recorded.
            "pinned_by_digest": bool(digest),
            "detected_via": "environment variable" if image else "container marker file",
        },
    )


def classify_hostname(hostname: str | None) -> str:
    """A coarse class for the host, so a record is comparable without naming it."""
    if not hostname:
        return "unknown"
    lowered = hostname.lower()
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return "container"
    if any(marker in lowered for marker in ("runner", "ci-", "-ci", "buildkite", "github")):
        return "ci"
    if re.match(r"^[0-9a-f]{12}$", lowered) or lowered.startswith("ip-"):
        return "cloud-instance"
    return "workstation"


# ── Capture ──────────────────────────────────────────────────────────────────


@dataclass
class EnvironmentManifest:
    """A deterministic description of the environment a result was produced in."""

    captured_at: str | None = None
    hostname: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def manifest_hash(self) -> str:
        """Identity of the environment, with volatile and host-specific fields removed.

        Computed over :meth:`identity_payload`, which includes the manifest format version but
        excludes ``captured_at``, ``hostname`` and ``hostname_class``.
        """
        return manifest_hash(self.identity_payload())

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment_manifest_version": ENVIRONMENT_MANIFEST_VERSION,
            "captured_at": self.captured_at,
            "hostname": self.hostname,
            "hostname_class": classify_hostname(self.hostname),
            "environment_manifest_hash": self.manifest_hash,
            **self.manifest,
        }

    def identity_payload(self) -> dict[str, Any]:
        """Exactly what ``manifest_hash`` is computed over.

        Exposed so a reader -- and the tests -- can see which fields participate in the
        environment's identity rather than having to infer it from the digest. The format
        version is inside it, because a different capture format is a different description of
        the environment.
        """
        return {"environment_manifest_version": ENVIRONMENT_MANIFEST_VERSION, **self.manifest}


def capture_environment(
    *,
    captured_at: str | None = None,
    environ: dict[str, str] | None = None,
    probe_hardware: bool = True,
) -> EnvironmentManifest:
    """Capture the current environment.

    ``probe_hardware=False`` skips the subprocess probes (``nvidia-smi``, ``rocminfo``,
    ``/proc`` reads) and is what the deterministic test suite uses: the probes describe
    the machine the tests happen to run on and would otherwise make the suite's
    expected values depend on the host.
    """
    recorded_env, withheld = capture_environment_variables(environ)
    package_probe = probe_packages()
    packages: dict[str, str] = (
        dict(package_probe.value["versions"]) if package_probe.available else {}
    )
    unreadable_packages: list[str] = (
        list(package_probe.value["unreadable_distributions"]) if package_probe.available else []
    )

    manifest: dict[str, Any] = {
        "os": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "kernel": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "architecture": platform.architecture()[0],
        },
        "python": {
            "version": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "executable_name": Path(sys.executable).name,
        },
        "packages": packages,
        "packages_unreadable": unreadable_packages,
        "packages_core": {name: packages.get(name, "not installed") for name in CORE_PACKAGES},
        "environment_variables": recorded_env,
        "redacted_variable_names": withheld,
    }

    if probe_hardware:
        manifest["cpu"] = probe_cpu().to_dict()
        manifest["ram_bytes"] = probe_ram_bytes().to_dict()
        manifest["gpu"] = probe_gpus().to_dict("devices")
        manifest["cuda"] = probe_cuda().to_dict()
        manifest["rocm"] = probe_rocm().to_dict()
        manifest["container"] = probe_container().to_dict()
    else:
        reason = "hardware probing disabled for this capture"
        manifest["cpu"] = {"available": False, "reason": reason}
        manifest["ram_bytes"] = {"available": False, "reason": reason}
        manifest["gpu"] = {"available": False, "reason": reason}
        manifest["cuda"] = {"available": False, "reason": reason}
        manifest["rocm"] = {"available": False, "reason": reason}
        manifest["container"] = {"available": False, "reason": reason}

    return EnvironmentManifest(
        captured_at=captured_at,
        hostname=platform.node() or None,
        manifest=manifest,
    )


# ── Comparison ──────────────────────────────────────────────────────────────

#: Fields whose difference changes what a result means. A divergence here means the
#: two runs were not the same software environment.
MATERIAL_ENVIRONMENT_FIELDS: tuple[str, ...] = (
    "python.version",
    "packages",
    "packages_core",
)

#: Fields whose difference is a fact about the machine rather than a defect. Recorded
#: and reported, never treated as an automatic reproducibility failure -- EngramFold
#: will run on heterogeneous hardware.
INFORMATIONAL_ENVIRONMENT_FIELDS: tuple[str, ...] = (
    "cpu",
    "gpu",
    "cuda",
    "rocm",
    "ram_bytes",
    "container",
    "os.kernel",
)


@dataclass
class EnvironmentComparison:
    """How two environments differ, split by whether the difference is material."""

    material: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    informational: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    @property
    def same_software(self) -> bool:
        return not self.material

    def to_dict(self) -> dict[str, Any]:
        return {
            "same_software": self.same_software,
            "material_differences": {
                key: {"baseline": a, "candidate": b}
                for key, (a, b) in sorted(self.material.items())
            },
            "informational_differences": {
                key: {"baseline": a, "candidate": b}
                for key, (a, b) in sorted(self.informational.items())
            },
            "note": (
                "Hardware differences are recorded, not treated as failures. A material "
                "difference means the software environment differed and the two results "
                "are not directly comparable without saying so."
            ),
        }


def _dig(data: dict[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def compare_environments(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> EnvironmentComparison:
    """Classify the differences between two environment manifests.

    Deliberately does not return a pass/fail. Whether a difference invalidates a
    comparison is a research judgment; this reports what differs and how it is
    classified, and leaves the decision visible.
    """
    comparison = EnvironmentComparison()
    for field_name in MATERIAL_ENVIRONMENT_FIELDS:
        left, right = _dig(baseline, field_name), _dig(candidate, field_name)
        if left != right:
            comparison.material[field_name] = (left, right)
    for field_name in INFORMATIONAL_ENVIRONMENT_FIELDS:
        left, right = _dig(baseline, field_name), _dig(candidate, field_name)
        if left != right:
            comparison.informational[field_name] = (left, right)
    return comparison


def environment_identity(manifest: dict[str, Any]) -> str:
    """Identity hash of an environment manifest, timestamps and host removed."""
    return manifest_hash(manifest)


def quick_environment_digest() -> str:
    """A short digest of the interpreter and core package versions.

    Used in diagnostics where a full manifest would be noise. Not an identity: it is a
    convenience summary, and no freeze or manifest records it as one.
    """
    packages_probe = probe_packages()
    packages: dict[str, str] = (
        dict(packages_probe.value["versions"]) if packages_probe.available else {}
    )
    payload = {
        "python": sys.version.split()[0],
        "core": {name: packages.get(name, "not installed") for name in CORE_PACKAGES},
    }
    # Routed through the canonical implementation rather than calling a hash library
    # here: a second place in the tree that reaches for sha256 directly is the first
    # step toward two subtly different notions of identity.
    return canonical_json_hash(payload)


__all__ = [
    "CORE_PACKAGES",
    "ENVIRONMENT_MANIFEST_VERSION",
    "INFORMATIONAL_ENVIRONMENT_FIELDS",
    "MATERIAL_ENVIRONMENT_FIELDS",
    "REDACTED",
    "RELEVANT_ENV_PATTERNS",
    "SECRET_NAME_MARKERS",
    "EnvironmentComparison",
    "EnvironmentManifest",
    "Probe",
    "capture_environment",
    "capture_environment_variables",
    "classify_hostname",
    "compare_environments",
    "environment_identity",
    "is_secret_shaped_name",
    "looks_like_secret_value",
    "probe_container",
    "probe_cpu",
    "probe_cuda",
    "probe_gpus",
    "probe_packages",
    "probe_ram_bytes",
    "probe_rocm",
    "quick_environment_digest",
    "redact_environment_variables",
]
