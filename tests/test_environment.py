"""Environment capture: deterministic, and secrets withheld by construction.

Two properties are load-bearing. Capture must be stable enough to hash (so two captures of an
unchanged machine agree), and it must not be possible to leak a credential through it. The
second is tested adversarially: a secret-shaped *name*, a credential-shaped *value* under an
innocent name, and an unlisted variable must all be handled.
"""

from __future__ import annotations

import json

from engramfold.provenance.environment import (
    CORE_PACKAGES,
    ENVIRONMENT_MANIFEST_VERSION,
    INFORMATIONAL_ENVIRONMENT_FIELDS,
    MATERIAL_ENVIRONMENT_FIELDS,
    REDACTED,
    capture_environment,
    capture_environment_variables,
    classify_hostname,
    compare_environments,
    environment_identity,
    is_secret_shaped_name,
    looks_like_secret_value,
    probe_container,
    probe_cpu,
    probe_cuda,
    probe_gpus,
    probe_packages,
    probe_ram_bytes,
    probe_rocm,
    quick_environment_digest,
    redact_environment_variables,
)

# ── Redaction: by name ───────────────────────────────────────────────────────


def test_secret_shaped_names_are_recognised():
    for name in (
        "HF_TOKEN",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "OPENAI_API_KEY",
        "DB_PASSWORD",
        "MY_CREDENTIAL",
        "AUTH_HEADER",
        "SESSION_ID",
        "CSRF_COOKIE",
        "PRIVATE_KEY_PATH",
        "SIGNATURE",
        "BEARER",
        "LICENSE_KEY",
    ):
        assert is_secret_shaped_name(name), name


def test_ordinary_names_are_not_flagged():
    for name in ("CUDA_VISIBLE_DEVICES", "OMP_NUM_THREADS", "PYTHONHASHSEED", "LANG", "TZ"):
        assert not is_secret_shaped_name(name), name


def test_a_secret_named_variable_is_withheld_entirely():
    recorded, withheld = capture_environment_variables(
        {"HF_TOKEN": "hf_live_value", "LANG": "en_US.UTF-8"}
    )
    assert "HF_TOKEN" not in recorded
    assert "HF_TOKEN" in withheld
    assert recorded["LANG"] == "en_US.UTF-8"


def test_withheld_names_are_recorded_so_the_withholding_is_visible():
    """The record must show *that* something was withheld, without showing what."""
    recorded, withheld = capture_environment_variables({"AWS_SECRET_ACCESS_KEY": "s3cr3t"})
    assert withheld == ["AWS_SECRET_ACCESS_KEY"]
    assert "s3cr3t" not in json.dumps(recorded)
    assert "s3cr3t" not in json.dumps(withheld)


def test_an_unlisted_variable_is_not_recorded_at_all(tmp_path):
    """The default for an unrecognised variable is silence, not inclusion."""
    recorded, _withheld = capture_environment_variables({"SOME_RANDOM_THING": "value", "LANG": "C"})
    assert "SOME_RANDOM_THING" not in recorded
    assert "LANG" in recorded


def test_relevant_variables_are_recorded():
    recorded, _ = capture_environment_variables(
        {
            "CUDA_VISIBLE_DEVICES": "0,1",
            "OMP_NUM_THREADS": "8",
            "PYTHONHASHSEED": "0",
            "ENGRAMFOLD_SOMETHING": "x",
            "SLURM_JOB_ID": "123",
            "NCCL_DEBUG": "INFO",
            "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
        }
    )
    assert recorded["CUDA_VISIBLE_DEVICES"] == "0,1"
    assert recorded["OMP_NUM_THREADS"] == "8"
    assert recorded["PYTHONHASHSEED"] == "0"
    assert recorded["ENGRAMFOLD_SOMETHING"] == "x"
    assert recorded["SLURM_JOB_ID"] == "123"
    assert recorded["NCCL_DEBUG"] == "INFO"
    assert recorded["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"


# ── Redaction: by value ──────────────────────────────────────────────────────


def test_a_url_with_inline_credentials_is_withheld():
    assert looks_like_secret_value("https://user:token@example.invalid/path")
    assert looks_like_secret_value("postgres://admin:hunter2@db:5432/x")


def test_a_long_opaque_token_is_withheld():
    assert looks_like_secret_value("a" * 40)
    assert looks_like_secret_value("AKIAIOSFODNN7EXAMPLE1234567890abcdefgh")
    assert looks_like_secret_value("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop")


def test_ordinary_values_are_not_withheld():
    for value in (
        "0,1",
        "8",
        "en_US.UTF-8",
        ":4096:8",
        "/usr/local/lib",
        "INFO",
        "3.12.0",
        "C:\\Users\\someone\\data",
        "https://huggingface.co/owner/name",
    ):
        assert not looks_like_secret_value(value), value


def test_a_credential_shaped_value_under_an_innocent_name_is_withheld():
    """The case a name-based rule cannot catch."""
    recorded, withheld = capture_environment_variables(
        {"CUDA_VISIBLE_DEVICES": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"}
    )
    assert withheld
    assert recorded["CUDA_VISIBLE_DEVICES"] == REDACTED


def test_the_full_redaction_view_withholds_every_secret_shaped_name():
    view = redact_environment_variables(
        {"HF_TOKEN": "x", "MY_SECRET": "y", "LANG": "C", "SOMETHING": "z"}
    )
    assert view["HF_TOKEN"] == REDACTED
    assert view["MY_SECRET"] == REDACTED
    assert view["LANG"] == "C"
    assert view["SOMETHING"] == "z"


def test_the_redaction_view_is_sorted_and_complete():
    view = redact_environment_variables({"B": "2", "A": "1"})
    assert list(view) == ["A", "B"]


# ── Determinism ─────────────────────────────────────────────────────────────


def test_two_captures_of_an_unchanged_environment_hash_identically():
    first = capture_environment(captured_at="2026-01-01T00:00:00Z", probe_hardware=False)
    second = capture_environment(captured_at="2030-01-01T00:00:00Z", probe_hardware=False)
    assert first.manifest_hash == second.manifest_hash


def test_the_timestamp_is_recorded_but_excluded_from_identity():
    first = capture_environment(captured_at="2026-01-01T00:00:00Z", probe_hardware=False)
    assert first.captured_at == "2026-01-01T00:00:00Z"
    assert first.to_dict()["captured_at"] == "2026-01-01T00:00:00Z"
    # The recorded hash and the recomputable identity over the payload must agree.
    assert first.to_dict()["environment_manifest_hash"] == environment_identity(
        first.identity_payload()
    )
    assert first.to_dict()["environment_manifest_hash"] == first.manifest_hash


def test_the_format_version_participates_in_the_identity():
    """A different capture format is a different description of the environment."""
    manifest = capture_environment(probe_hardware=False)
    payload = manifest.identity_payload()
    assert payload["environment_manifest_version"] == ENVIRONMENT_MANIFEST_VERSION
    without_version = {k: v for k, v in payload.items() if k != "environment_manifest_version"}
    assert environment_identity(without_version) != manifest.manifest_hash


def test_the_hostname_is_recorded_but_excluded_from_identity():
    first = capture_environment(probe_hardware=False)
    other = capture_environment(probe_hardware=False)
    other.hostname = "a-completely-different-host"
    assert other.manifest_hash == first.manifest_hash
    assert other.to_dict()["hostname"] == "a-completely-different-host"


def test_a_different_package_set_changes_the_identity():
    first = capture_environment(probe_hardware=False)
    mutated = dict(first.manifest)
    mutated["packages"] = {**mutated["packages"], "invented-package": "1.0"}
    assert environment_identity(mutated) != first.manifest_hash


def test_the_quick_digest_is_stable():
    assert quick_environment_digest() == quick_environment_digest()


# ── Manifest contents ───────────────────────────────────────────────────────


def test_the_manifest_carries_its_version():
    manifest = capture_environment(probe_hardware=False).to_dict()
    assert manifest["environment_manifest_version"] == ENVIRONMENT_MANIFEST_VERSION


def test_the_manifest_records_os_interpreter_and_packages():
    manifest = capture_environment(probe_hardware=False).to_dict()
    for key in ("os", "python", "packages", "packages_core", "environment_variables"):
        assert key in manifest, key
    assert manifest["os"]["system"]
    assert manifest["python"]["version"]


def test_every_core_package_is_either_versioned_or_explicitly_absent():
    core = capture_environment(probe_hardware=False).to_dict()["packages_core"]
    assert set(core) == set(CORE_PACKAGES)
    for name, version in core.items():
        assert version == "not installed" or version, name


def test_package_versions_are_sorted():
    packages = capture_environment(probe_hardware=False).to_dict()["packages"]
    assert list(packages) == sorted(packages)


def test_unreadable_distributions_are_recorded_not_dropped():
    """A silently short package list is an invisible incompleteness."""
    probe = probe_packages()
    assert probe.available
    assert "unreadable_distributions" in probe.value


def test_the_manifest_is_json_serialisable():
    json.dumps(capture_environment(probe_hardware=False).to_dict())


def test_hardware_probing_can_be_disabled_and_says_so():
    manifest = capture_environment(probe_hardware=False).to_dict()
    for key in ("cpu", "ram_bytes", "gpu", "cuda", "rocm", "container"):
        assert manifest[key]["available"] is False
        assert "reason" in manifest[key]


# ── Probes: unavailable is a fact with a reason ──────────────────────────────


def test_probes_are_callable_and_report_a_reason_when_unavailable():
    for probe in (
        probe_cuda(),
        probe_rocm(),
        probe_gpus(),
        probe_cpu(),
        probe_ram_bytes(),
        probe_container(),
    ):
        payload = probe.to_dict()
        assert "available" in payload
        if not probe.available:
            assert payload["reason"]
        else:
            assert "value" in payload


def test_probe_dicts_are_json_serialisable():
    for probe in (probe_cuda(), probe_rocm(), probe_gpus(), probe_cpu(), probe_ram_bytes()):
        json.dumps(probe.to_dict())


# ── Comparison: hardware differences are information, not failure ────────────


def test_a_material_software_difference_is_classified_as_material():
    baseline = capture_environment(probe_hardware=False).to_dict()
    candidate = json.loads(json.dumps(baseline))
    candidate["packages"]["torch"] = "0.0.0-invented"
    comparison = compare_environments(baseline, candidate)
    assert not comparison.same_software
    assert "packages" in comparison.material


def test_a_hardware_difference_is_classified_as_informational():
    """EngramFold will run on heterogeneous hardware; a different GPU is not a defect."""
    baseline = capture_environment(probe_hardware=False).to_dict()
    candidate = json.loads(json.dumps(baseline))
    candidate["gpu"] = {"available": True, "devices": [{"model": "a different accelerator"}]}
    candidate["cpu"] = {"available": True, "value": "a different cpu"}
    comparison = compare_environments(baseline, candidate)
    assert comparison.same_software, "hardware must not count as a software difference"
    assert "gpu" in comparison.informational
    assert "cpu" in comparison.informational


def test_identical_environments_report_no_differences():
    baseline = capture_environment(probe_hardware=False).to_dict()
    comparison = compare_environments(baseline, json.loads(json.dumps(baseline)))
    assert comparison.same_software
    assert comparison.material == {}
    assert comparison.informational == {}


def test_comparison_explains_the_policy_in_its_payload():
    baseline = capture_environment(probe_hardware=False).to_dict()
    comparison = compare_environments(baseline, baseline)
    assert "not treated as failures" in comparison.to_dict()["note"]


def test_the_field_classification_is_declared():
    assert "packages" in MATERIAL_ENVIRONMENT_FIELDS
    assert "python.version" in MATERIAL_ENVIRONMENT_FIELDS
    for field in ("gpu", "cpu", "cuda", "rocm", "ram_bytes", "container"):
        assert field in INFORMATIONAL_ENVIRONMENT_FIELDS


def test_comparison_of_a_missing_field_is_reported_rather_than_crashing():
    comparison = compare_environments({}, {"packages": {"x": "1"}})
    assert "packages" in comparison.material


# ─ Hostname class ──────────────────────────────────────────────────────────


def test_hostname_class_is_coarse_and_never_the_raw_name():
    assert classify_hostname(None) == "unknown"
    assert classify_hostname("github-runner-3") in {"ci", "container", "workstation"}
    assert classify_hostname("a1b2c3d4e5f6") == "cloud-instance"
    assert classify_hostname("ip-10-0-0-5") == "cloud-instance"
    assert classify_hostname("my-laptop") == "workstation"
