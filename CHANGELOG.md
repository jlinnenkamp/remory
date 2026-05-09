# Changelog

All notable changes to Remory will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Phase 0** — Project skeleton: `src/` layout; `pyproject.toml` with ruff/pyright/pytest
  configs (pyright strict on `src/`, basic on `tests/`); Apache 2.0 LICENSE; pre-commit
  pinned to ruff `v0.15.12` (matches dev-dep ruff); GitHub Actions CI matrix
  (Ubuntu+macOS × Python 3.12+3.13) plus pre-commit backstop job; dev-time subagent
  stubs (`architect`, `implementer`, `reviewer`).
