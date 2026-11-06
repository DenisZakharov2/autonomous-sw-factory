# Technical Documentation

This file contains the technical details extracted from the main README for developers and integrators.

---

## Architecture Overview

### Core Components

```
factory/                 # Engine and configs
  cli.py                 # Commands
  journal.py             # Hash chain, locking, state anchor
  engine.py              # init/next/submit/gate/task
  artifacts.py           # Registry, Markdown/JSON checks
  pipeline.py            # Stages, modes, owners
  quarantine.py          # SkillSpector adapter
  ui/                    # Read-only run dashboard
    data.py              # Panel payload from core read functions, cached by mtime
    server.py            # ThreadingHTTPServer: GET only, static page, 304
    static/index.html    # 12-column grid, panels, drag/resize, presets
  runtime/               # Executable loop
    base.py              # Executor protocol
    loop.py              # Order → executor → verify → submit → gate
    prompt.py            # Prompt assembly
    submit.py            # Which paths may be submitted
    guard.py             # Tripwire on state.json and run directory
    usage.py             # usage.jsonl and mode budget
    evidence.py          # runtime/evidence/*.json
    fake.py              # Deterministic executor
    opencode.py          # OpenCode adapter
    hermes.py            # Hermes adapter
    sandbox.py           # bwrap: read-only root and control plane
    fixtures/nano/       # Reference artifacts for fake
  roles/                 # 22 role cards
  schemas/               # 13 JSON schemas
  templates/             # 38 templates
  pipeline.yaml          # Stage graph and modes
  artifacts.yaml         # Artifact registry
  models.yaml            # Tier → runtime/env
  budget.yaml            # Limits and behavior
tests/                   # Invariants, CLI, concurrency, schemas, runtime
examples/nano-todo/      # Reference run
docs/                    # quickstart, runtime, checkpoints, eval, eval-plan, threat model, metrics, adapters
```

---

## Work Order Contract

`factory next --run-id <id>` returns JSON:

```json
{
  "action": "produce",
  "stage": "s1_spec",
  "roles": ["05-business-analyst", "06-system-analyst", "02-independent-verifier"],
  "artifacts": [
    {"id": "spec", "path": "artifacts/spec.md", "template": "templates/spec.tmpl", "owner": "05-business-analyst"}
  ],
  "pending_artifacts": [
    {"id": "spec", "path": "artifacts/spec.md", "owner": "05-business-analyst", "errors": ["missing artifact: ..."]}
  ],
  "next_role": "05-business-analyst",
  "gate": {"id": "gate_s1", "approver": "human", "blocking": true},
  "next_command": "factory submit --run-id ... --stage s1_spec --role ... --artifact ..."
}
```

The runtime must:
1. Read the role card and template
2. Create only declared artifacts
3. Write `deliverable.md` with REQ, verification, and diff
4. Call `factory submit`
5. Not write `PASS` and not write to other roles' files

Fresh context per role is a recommendation, not a kernel requirement. If the runtime doesn't support subagents, it passes `handoff` manually: decisions, open questions, rejected alternatives, paths, and hashes.

Since `0.2.0` this contract is executed by `factory run`: it receives the order, calls the adapter (`fake`, `hermes`, `opencode`), submits only declared and existing artifacts, writes `runtime/usage.jsonl` and `runtime/evidence/`, and stops at a human gate with exit code `2`. Details in [`docs/runtime.md`](docs/runtime.md). `pending_artifacts` and `next_role` were added so multi-role stages don't require guessing who hasn't submitted yet.

---

## Stages

13 stages: `s_in`, `s00_intake`, `s0_vision`, `s1_spec`, `s15_quarantine`, `s2_architecture`, `s3_plan`, `s4_build`, `s5_acceptance`, `s6_security`, `s7_release`, `s8_docs`, `s9_learn`. Full table in [`PIPELINE.md`](PIPELINE.md).

---

## Gates

- **Human gates:** `gate_s_in` (restore), `gate_s00` (interview), `gate_s0` (vision, advisory), `gate_s1` (spec-human), `gate_s7` (release), `gate_s9` (self-improvement).
- **Verifier gates:** `gate_s15`, `gate_s2`, `gate_s3`, `gate_s4`, `gate_s5`, `gate_s6`, `gate_s8`.

A gate closes only after `factory verify`, which checks schemas, journal, state, artifacts, REQ parity, and plan coverage.

---

## Tasks

`plan.md` contains a task table. `factory gate approve --gate gate_s3` imports it into `state.json`. Then:

```
pending -> in_progress -> review -> accepted
                         \-> escalated (3 rejections)
```

Each task has `owner`, `reqs`, `allowed_files`, `deps`, `attempts`, `lease`, `artifacts`. `task accept` writes `TASK_DONE PASS` only from `02-independent-verifier` and only after a `VERIFY PASS` event on a valid `tasks/<task_id>/verification/verification.md` report.

---

## Roles and Vetoes

22 role cards, eight sections. Key frontmatter fields:

- `role_id`, `title`, `default_model_tier`, `reasoning_effort`
- `active_stages` — where the role may work
- `write_scopes` — `code`, `own_artifacts`, `factory_state`
- `veto_type` — `blocking` or `advisory`
- `veto_scope` — why and where veto applies
- `blackbox_logging: required`

Veto types:

| Type | Meaning | How applied |
|---|---|---|
| `blocking` | Stop on safety invariant or contract | Reason code + evidence; 3 returns → escalate to human |
| `advisory` | Recommendation | Written to report, doesn't stop the stage |

A human can lift a blocking veto only with a `RISK_ACCEPTANCE` event containing risk text, expiry, and accountable person. Role registry and `pipeline` are checked by `factory doctor`.

---

## Journal and Trust

`blackbox.jsonl` — append-only JSONL. Hash:

```
hash = sha256(prev_hash || canonical_json(event_without_hash))
```

Properties:
- `fcntl.flock` serializes writes
- `state.json` stores `last_event_hash` and `event_count`, so tail truncation is visible
- `verify_chain` checks schema, seq, event_id, run_id, prev_hash, hash, and `PASS` semantics
- `AUDIT_TRAIL.md` — derived mirror with escaping
- `ARTIFACT_RERECORD` — only legal way to change an accepted artifact's hash: event with reason and `old_sha256`, only by owner, only `DRAFT`; `verify` takes the latest record for a path, so edit after re-record is again `tampered`
- Chain is not signed; for protection against full history recomputation an external checkpoint (HMAC/Ed25519) is needed

---

## Brownfield Mode

1. `git bundle create` + `tar` worktree + `manifest.sha256`
2. Temporary clone; restore; compare `git status --porcelain` and hashes
3. `restore-drill.md` with actual output and `PASS` only after verification
4. `Untouchable Paths` — glob list; writes to them are blocked
5. Only after `gate_s_in` are changes allowed

Read-only scan is limited by extensions, file count, and exclusions (`.env`, `*.pem`, `id_*`, `.git`, `node_modules`). Foreign repository content is treated as data, not instructions.

---

## Skill Quarantine

`factory/quarantine.py` calls `skillspector scan --format json --fail-on-incomplete --min-coverage 95`. Decision:

- `SAFE` + `LOW` + complete + coverage ≥ 95 + exit 0 → `AUTO_ALLOW`
- `CAUTION` → `ESCALATE_HUMAN`
- `DO_NOT_INSTALL` / `HIGH` / `CRITICAL` → `REJECT_AND_PURGE`
- incomplete / failed analyzer / low coverage → `BLOCK_INCOMPLETE`
- exit ≠ 0 → `BLOCK_RETRY` (no install)

Lock stores directory hash, `pack`, `commit`, `license`, scanner version, verdict. `verify-lock` detects substitution.

---

## Self-Improvement (S9)

S9 computes eight metrics: `first_pass_rate`, `rework_rate`, `escaped_defect_rate`, `cycle_time_p50/p90`, `cost_per_artifact`, `top_reject_reasons`, `verification_coverage`, `traceability_coverage`.

Patches land in `artifacts/patches/` and apply only after human review. Meta-agent cannot change `factory/pipeline.yaml`, `roles/`, or invariants without a separate human commit. This is intentional: self-improvement without eval is prompt corruption with acceleration.

---

## Threat Model (Summary)

| Asset | Threat | Countermeasure |
|---|---|---|
| Customer code | Prompt injection in README/comments | Data not instructions; scan boundaries; allowlist |
| Secrets | Leak into journal/artifacts | `.gitignore`, scan exclusions, log redaction |
| Journal | Tampering/truncation | Hash chain, state anchor, flock |
| Skills | Supply chain | SkillSpector, fail-closed, hash lock |
| Permissions | Write outside scope | `write_scopes`, `Allowed Files`, pre-commit check |
| Release | Rollback unverified | `rollback drill` + human gate |

Full version — [`docs/threat-model.md`](docs/threat-model.md).

---

## Non-Goals

- Replace Jira/Linear
- Built-in LLM provider
- Automatic merge to main without human
- "22 agents in one context"
- Promise that the model doesn't make mistakes

---

## Roadmap

- `0.1.2` — Transactional core, artifact integrity, working stage automaton
- `0.2.0` — Executable runtime: `pending_artifacts`/`next_role`, fake/Hermes/OpenCode adapters, `usage.jsonl`, evidence, tripwire
- `0.2.1` — CI fix: sandbox tests on runners
- `0.2.2` — Trust boundary: `bwrap` by default for real executors, read-only control plane; measured step cost (`--executor-tools auto`, durable `step`); parallel `s4_build` tasks in git worktree
- `0.3.0` — Signed checkpoints (`factory sign`, HMAC anchor outside `runs/`), external eval set (`factory eval`, two cases, baseline in CI), run comparison (`factory compare`) and green `nano-todo` on real Hermes (`eval/baseline-hermes.json`, analysis — `docs/audit/handoff-1.0.9.md`)
- `1.0.0` — Full `nano`, `standard`, `brownfield` on real product (`nano-product-mini`, `product-mini`, `brownfield-proxy-mini`), five records in `eval/baseline-hermes.json`, mode ceilings from measurements and green CI on ubuntu/macOS/Windows (run `41215608643`, 14/14)
- `1.0.1` — `traceability` checks test name, not just file; legal `re-record` of accepted artifact (`ARTIFACT_RERECORD`, §5.7 `docs/runtime.md`); new human-readable factory description in `README.md`/`README.en.md`; CI run `41221412801` (14/14)
- `1.0.2` — Independent verifier executes before every role-gate and every `TASK_DONE` (`VERIFY` in journal, report with three checks); deterministic check in stage report, literal `PASS` in acceptance matrix and single word in verdict checked by code; evidence `report_invalid`; budgets `nano` 7.8M, `standard` 9.5M, `brownfield` 9.1M / 60 min from real measurements. CI run `41282195622` (14/14)
- `1.0.3` — Read-only run dashboard `factory ui` (stdlib, only `GET`, seven panels, free 12-column grid, presets, 2s polling and `304`) and export M1: CSV of full journal, tasks, spend, traceability, `.xlsx` book of six sheets via extra `xlsx`, `runs.csv` for all runs; verifier rejection reaches producer, stage reworking itself reopens its tasks
- `1.0.4` — Metrics panel with nine `compute_metrics` values (`null` = "not measured", calc error = warnbox), run list scrolls inside table not page, `factory ui --runs-dir` actually reads specified dir, old layout doesn't auto-open new panel; both READMEs — "See a run" block and honest metrics description
- `1.0.5` — Journal filters (verdict, stage, event_type, limit) with URL state and honest counter; client-side search/sort of run list (?q=&sort=&dir=); detail drawer 420px for stage/task/event with deep link ?event=<seq>; new endpoint GET /api/runs/<id>/events/<seq>; journal.csv export with same filters; ETag includes filter hash
- `1.0.6` — Time-series panel (P9): four canvas charts (tokens, seconds, USD, API calls) without external libs, metric tabs, buckets stage/event/minute, URL state (?ts=&bucket=); endpoint GET /api/runs/<id>/timeseries with ETag by journal stamp and bucket; CHANGELOG.md created
- `1.0.7` — Endpoint `/api/compare/artifact` and panel "Artifacts (A vs B)" with three-tab selector and line-by-line diff, drawer close fix. Current.

---

## Limits

- `models.yaml` intentionally has no vendor IDs. Tier resolved by runtime or `FACTORY_MODEL_TIER_1/2/3`; journal records actual `model_id` passed by runtime.
- Cost metrics stay `null`: `cost_per_artifact_usd` computed from journal, but journal doesn't store prices. Runtime writes tokens and cost separately in `runtime/usage.jsonl`.
- Brownfield supports git repos only. For non-git folder use `standard` and backup manually.
- `s4_build` imports tasks from `plan.md` Markdown table; arbitrary plan formats not parsed.
- `examples/nano-todo` is a deterministic reference run, not proof of model autonomy.
- `factory run` executes one step per executor call and stops at human gate.
- Hash chain not signed; `factory sign` is separate command, no auto-sign in `factory run`.

---

## Development

```bash
pip install -e ".[dev]"
pytest -q
factory doctor
python examples/build_nano_todo.py
```

CI: Python 3.10–3.13, `doctor`, `pytest`, example check, package build.

---

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).