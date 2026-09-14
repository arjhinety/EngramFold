# `scripts/` — ownership and purpose

**Class:** Substrate. **Owner:** the maintainers.

## What belongs here

Operational scripts that are **not** part of the installed package: one-off audits, migration
helpers, and CI shims. Anything that the package or the CLI should own belongs in
`src/engramfold/` instead, where it is imported, typed and tested.

## What does not belong here

- Research code. Nothing in this repository trains, merges experts, prunes, clusters or
  downloads weights.
- A second implementation of anything the package already does. Two implementations of
  identity, discovery or validation is the failure mode this repository is built around
  avoiding.
- A second quality gate. [`engramfold-preflight`](../src/engramfold/preflight.py) is the one
  canonical entry point; a script here may call it, never replace it.

## Currently empty

The preflight gate is implemented in the package rather than as a shell script, so that it is
testable and so that there is exactly one definition of repository health. This directory is
kept with its ownership declared for operational scripts that genuinely do not belong in the
package.