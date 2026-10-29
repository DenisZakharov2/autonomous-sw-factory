# Changelog

All notable changes are recorded here. The project follows a simple
`0.MINOR.PATCH` scheme until `1.0.0`.

## [0.1.0] — 2026-10-29

### Added

- Executable Python engine: `init`, `next`, `status`, `submit`, `verify`,
  `gate`, `task`, `quarantine`, `trace`, `metrics`, `doctor`, `artifacts`,
  `models`, `log`.
- 13-stage pipeline with human and verifier gates; four modes (`nano`,
  `standard`, `enterprise`, `brownfield`).
- 22 role cards with `active_stages`, `write_scopes`, explicit veto type and
  eight required sections; uniqueness and contract checks.
- 38 artifact templates and 9 JSON schemas; structural Markdown validation.
- SHA-256 hash-chained journal with `fcntl.flock`, state anchor and semantic
  `PASS` checks; truncated tails and forged verifier events are rejected.
- SkillSpector 2.12.0 adapter with fail-closed decision table, directory hash
  lock and substitution detection.
- `REQ-###` traceability matrix, task lifecycle with three attempts and
  escalation, eight health metrics.
- Reference run `examples/nano-todo/` (33 events, 2 tasks, 3 REQ) and CI.
- Full documentation set: README (RU/EN), DESIGN, PIPELINE, ROSTER, AGENTS,
  four protocols, glossary, threat model, metrics, runtime adapters.

### Changed from the audited 1.0.0 kit

- Documentation is no longer a set of one-line stubs.
- `pipeline.yaml` is a real graph and passes its own schema.
- `nano` and `standard` have an explicit owner for spec, spec-human and plan.
- `verify` is no longer a no-op without a flag; empty and truncated journals
  fail.
- `--run-id` is validated; traversal, absolute paths and destructive re-init
  are rejected.
- `advisor` vetoes no longer claim to block; role scopes match duties.
- Remediation IDs from the old audit were replaced with current contracts.
- `LICENSE` contains the full MIT text; `NOTICE` is separate.
- `runs/` is gitignored.

### Known limitations

- The journal is not signed; a determined writer can recompute the whole chain.
- No embedded model or runtime; a host runtime executes roles.
- Cost metrics are `null` until the runtime records tokens and price.
- Brownfield supports git repositories only.
- `s4_build` imports tasks from a Markdown table, not arbitrary plans.
