# Changelog

All notable changes to Remory will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Three built-in topic types — `job-profile`, `workout`, and `coaching` — each
  with a defined set of state sections, a default tone and strictness, and
  wizard questions for first-run setup.
- File formats for per-topic state: `state.md` (YAML frontmatter plus
  schema-defined section headings), `meta.yaml` (consolidation counters and
  per-topic knobs), and raw entry files under `raw/<year>/`. User-authored
  topic schemas in YAML are loaded from the user config directory and shadow
  built-ins of the same name.
