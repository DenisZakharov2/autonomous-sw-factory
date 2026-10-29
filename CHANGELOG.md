# Changelog

All notable changes are recorded here. The project follows a simple
`0.MINOR.PATCH` scheme until `1.0.0`.

## [0.1.1] — 2026-10-29

### Fixed

- gate reject now enforces the declared approver, so a foreign role cannot
  reject a human gate (audit L-01);
- task submit validates the task id, the task owner and every artifact against
  `allowed_files`; stage submit checks the artifact owner (L-02);
- gate reject executes `rework_to`: the target stage and every intermediate
  stage are reopened and `current_stage` is recomputed (L-03);
- `next` returns `action: blocked` for an escalated or blocked stage/task and
  the CLI exits 1 (L-04);
- acceptance requires an explicit `PASS` row per `REQ` and rejects `PENDING`,
  `[ТРЕБУЕТ ПРОВЕРКИ]`, `FAIL` and `BLOCKED`; required sections must be whole
  heading lines (L-06);
- `budget.yaml` is loaded: attempt limits and the veto cap are enforced by the
  engine instead of a hardcoded 3 (M-01);
- `task` and `gate` schemas are synced with `state.$defs`, doctor fails on
  drift, work orders are validated against their schema (M-02);
- every directory artifact must contain a file, including `screens/` and
  `patches/` (M-03);
- the reference run recomputes metrics after `gate_s9`; verification coverage
  is 1.0 instead of 0.923 (M-04);
- one `approved_by` default (`auto:skillspector`), and `quarantine scan
  --run-id` writes `SKILL_INSTALL` to the journal (M-05).

### Added

- `factory budget` prints attempt limits, mode budgets and attempts used;
- regression tests for foreign approver, foreign owner, out-of-scope file,
  rework, escalation, blocked work orders, schema drift and `SKILL_INSTALL`;
- the docs test now walks every Markdown file and checks that documented
  `factory` subcommands exist in the CLI.

### Changed

- `SECURITY.md` states explicitly that role and approver strings are protocol
  discipline, not authentication.

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
