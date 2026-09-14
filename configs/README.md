# `configs/` — ownership and purpose

**Owner:** whoever runs a tool.
**Status:** source, hand-authored.

## What belongs here

Hand-authored configuration for tooling: adapter settings, benchmark suites, hardware
profiles. Configuration that a *manifest* records is not duplicated here — an experiment's
configuration lives in its manifest, and its identity is that manifest's
`configuration_hash`.

## The rules

1. **Configuration that affects a result belongs in the manifest, not here.** A config file
   that a run read but no manifest records is a config that cannot be checked, and a
   configuration whose identity is not recorded cannot be shown to have been the one used.
2. **No research parameters yet.** Nothing in this directory may encode a research decision —
   no target model, no expert count, no consolidation method, no benchmark selection. Those
   are undecided; see [`docs/METHODOLOGY.md`](../docs/METHODOLOGY.md).
3. **No secrets.** Credentials never belong in a repository file. Environment capture redacts
   secret-shaped variables, but a committed token is already leaked.
4. **Key order is not meaning.** Configuration identity is computed over a canonicalised
   value, so reordering a mapping is not a change. See
   [`docs/REPRODUCIBILITY.md`](../docs/REPRODUCIBILITY.md).

## Currently empty

No configuration is needed yet, because nothing runs. The directory exists with its ownership
declared so that the first configuration file has a documented home.