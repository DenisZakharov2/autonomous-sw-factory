# Changelog

All notable changes are recorded here. The project follows a simple
`0.MINOR.PATCH` scheme until `1.0.0`.

## [0.1.2] — 2026-10-30

### Fixed

- **transactional core:** every mutation runs under one `run_lock`; the journal
  line is written before `state.json`, so a failed append can no longer leave a
  mutated state without an event (audit BUG-05);
- **artifact integrity:** `submit` and `task submit` require at least one
  artifact, hash every file, and `GATE_DECISION` / `TASK_DONE` record the
  approved hashes; `verify` re-hashes the disk and reports tampering (BUG-01,
  BUG-02, BUG-21);
- **root and directory artifacts:** `skills-lock.json` (`root: true`) and
  `product/src/`, `product/tests/`, `artifacts/screens/`, `artifacts/patches/`
  (`directory: true`) can be submitted; directories are hashed as a manifest
  and symlinks are refused (BUG-03, BUG-04);
- **stage machine:** `submit`, `gate approve/reject`, `task *` and `veto`
  require the current stage; gates close only from `review`; the flags
  `human_spec_approved` and `restore_drill_passed` can no longer be set by
  closing a future gate out of order (BUG-06);
- **work orders:** a rejected gate returns to `produce`, the verify command no
  longer carries a failing `--require-gate`, partial multi-role submits stay
  `in_progress`, `s4_build` dispatches `review` → `in_progress` → ready
  `pending` (deps honoured), and every task must be `accepted` before
  `gate_s4` (BUG-07, BUG-08, BUG-09, BUG-17);
- **escalation:** `factory gate accept-risk`, `factory stage reset`,
  `factory task reset` and `factory veto` close the human-in-the-loop deadlock
  and record `RISK_ACCEPTANCE`, `STAGE_RESET`, `TASK_RESET`, `VETO` (BUG-10,
  BUG-20);
- **validation:** raw `{{...}}` templates and empty sections fail; acceptance
  is parsed per table cell (ordinary words like "fail" no longer reject a
  report) and open Critical/High defects block the gate; `REQ-1` is no longer
  covered by `REQ-10`; traceability checks that tasks, test files and PASS rows
  exist (BUG-13, BUG-14, BUG-15);
- **quarantine:** `s15_quarantine` calls `verify_lock`, symlinks inside a skill
  are refused, a missing `--name` falls back to the scanner report, and
  `REJECT_AND_PURGE` removes the lock entry and the quarantined directory
  (BUG-18);
- **metrics:** `verification_coverage` excludes the inapplicable `s_in` in
  greenfield and is `0.0` after init; gate rejections count as rework;
  `cycle_time` is measured gate-to-gate instead of submit-to-approve (BUG-19);
- **roles:** `09-ai-engineer` is allowed on `s5`/`s6`, `12` is no longer
  assigned to nano `s_in`, `doctor` validates `pipeline.yaml` against
  `active_stages` in both directions, events use the model tier and effort
  declared by the role card, `task accept` writes the `verification.md` report
  required by the verifier card, and unused event types are gone (BUG-20);
- **packaging:** `FACTORY_GLOSSARY.md` and `orchestrator/*.md` ship in the
  wheel; `MANIFEST.in` includes `tests/conftest.py`, `VERSION`, `NOTICE`,
  `examples/` and `docs/`; role files are `0644`; CI installs and tests the
  built sdist (BUG-11);
- **doctor:** repository checks (`VERSION`, `LICENSE`) only run inside the
  factory checkout, so `factory doctor` works in a client project (BUG-12);
- **docs:** version references are updated across `DESIGN.md`, `docs/` and
  `SELF_IMPROVEMENT.md` (BUG-22).

### Added

- optional HMAC approval for human gates: when `FACTORY_APPROVAL_KEY` is set,
  a `human:<id>` decision requires `--approval-token`; without the key the
  decision is journaled as `approval: unauthenticated`;
- 28 regression tests covering every fixed invariant;
- the reference run submits real artifacts and records hashes for all 13 gates.

### Changed

- `factory submit` and `factory task submit` reject an empty `--artifact` list;
- `factory next` proposes `gate` directly when a submitted stage validates, and
  `verify` otherwise;
- the reference run computes metrics before `gate_s9` and never rewrites
  `metrics.json` after its hash is recorded.

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
