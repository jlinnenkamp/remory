# Security

## Reporting a vulnerability

If you find a security issue in Remory, please email remory@mail.de with the details. Please do not open a public GitHub issue for security reports.

A response should arrive within five business days. There is no bug bounty programme; the project is unfunded.

## Threat model

Remory is a single-user, local-first CLI. It is designed to be run by the same person who owns the machine it runs on, against a `claude` binary that person has installed and logged into themselves. The threat model reflects that scope:

- **In scope:** bugs in Remory's own file handling that could corrupt a user's `state.md`, prompt-injection content in a user's own raw entries causing the sleep pipeline to misbehave in ways that lose data, mishandling of the `claude` subprocess that leaves stray processes or partial files on disk.
- **Out of scope:** the security posture of the `claude` binary itself (Remory trusts it the way a shell trusts `ls`), the security of Anthropic's API (anything you send to `claude` is governed by Anthropic's policies), multi-user attacks (Remory is single-user by design), and protection of data at rest (files are markdown and not encrypted — that is a feature; if your filesystem is hostile, Remory cannot help).

Remory does not collect, transmit, or store any data outside the user's own data directory and the `claude` subprocess it invokes. There is no telemetry, no crash reporting, and no analytics, opt-in or otherwise.

## Dependencies

Dependencies are pinned in `pyproject.toml` and reviewed at PR time. We do not run automated dependency-bump bots in v0.1; if a CVE lands in a dependency, the project owner will cut a point release.
