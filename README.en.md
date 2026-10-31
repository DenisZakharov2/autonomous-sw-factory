# Autonomous Software Factory

[![CI](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml) · [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Русский README](README.md) · [License](LICENSE) · [Reference run](examples/nano-todo/) · [Audit](AUDIT.md) · [Quick start](docs/quickstart.md)

---

**Version 1.0.1.**

**Autonomous Software Factory makes building applications and writing code convenient, safe and transparent.** It is a set of tools and rules that move work through stages: interview, requirements, architecture, build, acceptance, release, documentation and lessons. Every stage has a document to submit, a check to pass, and an explicit decision — proceed or send it back.

**Convenient** — because the process leads the person, not the other way round. The factory asks the questions and adapts its language to the person's level: plain words for a newcomer, options, constraints and a budget for a manager, files, tests and commands for a developer. It takes a product from scratch end to end — from interview and requirements to a working, verified result — or helps a team finish a project that has run out of energy, time or resources.

**Safe** — because the author does not accept their own work: only an independent verifier can record `PASS`. A real executor runs in a sandbox where the control files are read-only, and before any change to an existing project a backup is taken and a restore is verified.

**Transparent and verifiable** — because every stage leaves a document, every requirement is linked to a task and a test, and the journal keeps fingerprints of steps and files. The result can be checked, compared against a reference, and shown to a customer.

## Why it is convenient

1. **It asks the questions.** You do not start with an empty form but with an adaptive interview: 1–3 questions per step, each with a recommended option and a reason. The interviewer works out the person's profile and does not dump a long questionnaire on them.
2. **It adapts to the level.** Three profiles — `USER_NON_TECH`, `TECH_MANAGER`, `DEVELOPER` — are fixed in `state.json`. For a newcomer, the role card forbids jargon; a manager sees options, constraints and the mode budget; a developer sees files, templates and commands. One process, a different language.
3. **From scratch, end to end.** The `nano`, `standard` and `enterprise` modes carry a product from interview and requirements through architecture, build, acceptance and release to documentation and lessons. `factory next` returns the work order, the templates and the exact command that closes the stage. “End to end” here means a working, verified product in the run directory — not hosting, deployment or support.
4. **It helps finish a stuck project.** The `brownfield` mode for an existing git repository starts with `pre-factory.bundle`, a worktree archive, `manifest.sha256` and a verified restore drill, then moves to a read-only audit and targeted tasks. Nothing has to be rewritten from scratch, and you do not have to start over.
5. **It does not lock you into a tool and does not demand prompt engineering.** Pick an executor: Hermes, OpenCode, `fake` without a model, or a human. The core never calls a vendor model: `factory next` returns a JSON work order, and the roles, stages and templates are already described. You do not have to design the process yourself.

Two different readers, two different promises: “end to end” is for someone with an idea and no process; “finish the project” is for someone with code and no resources left to carry it.

## Why it is safe

1. **The author does not accept their own work.** Only `02-independent-verifier` can record `PASS`; a producer cannot write it at submission or at verification. Before every role gate and every `TASK_DONE` that role actually runs: it writes `verification.md` with three adversarial checks (a command and its real output) and one verdict, and the gate reads that verdict — without `VERIFY PASS` in the journal, `factory verify` will not let the gate close. A human can accept risk with a separate `accept-risk` record — with a reason, an expiry date and an accountable person.
2. **Execution is isolated.** A real executor runs in `bwrap`: the filesystem root is read-only, the control plane (`state.json`, the journal, `usage.jsonl`, evidence) is mounted read-only, and writes are open only in directories declared by the work order. A tripwire hashes the state and stops the run on substitution. This is a file boundary, not a separate Unix user and not network isolation — the limitation is stated plainly.
3. **Backup and restore drill before brownfield.** Before the first edit to existing code, the factory creates `pre-factory.bundle`, a worktree archive and `manifest.sha256`, then verifies the restore in a separate clone: a `diff` of the status and `sha256sum -c`. Without `PASS` in `restore-drill.md`, the `s_in` gate does not close.
4. **Journal and tamper protection.** `blackbox.jsonl` is a SHA-256 hash chain with file locking and an anchor in `state.json`; `factory verify` re-hashes files and reports `artifact tampered`. `factory sign` stores an HMAC checkpoint outside `runs/` and tells `stale` from `rolled_back` and `diverged`. A legitimate edit to an accepted artifact goes only through `factory artifact re-record`, with a reason and evidence.
5. **Limits, stops and quarantine.** Each mode has token, minute and USD budgets, each stage has an attempt limit, and a veto has a retry cap (`max_veto_retries: 3`). When a limit is hit, the run stops and escalates to a human instead of looping forever. External skills pass a SkillSpector quarantine with hashes pinned in `skills-lock.json`.

## Why it is transparent and verifiable

1. **Every stage is a document and a gate.** Not “a chat where something was written” but an artifact submitted from a template, a check and an explicit gate decision. `factory next` says up front what to submit and which command closes the stage.
2. **Requirements are traced.** `factory trace` shows the “requirement → task → test → acceptance” chain and names the gaps. A link to a test that does not exist is a gap too; since 1.0.1 the function name after `::` is checked as well.
3. **A report for the customer.** `AUDIT_TRAIL.md` is rendered from the journal: who, when, what was submitted, what the verdict was and how the record links to the previous one. The summary is `factory status`; the check is `factory verify`.
4. **Repeatable and comparable.** `factory eval` drives reference cases on `fake` or a real executor and compares steps, events and tokens with the baseline. `factory compare` shows the delta between two runs, including artifact hashes.
5. **Spend is visible.** `factory usage` reports `steps`, `api_calls`, `prompt_chars`, `tokens`, `tokens_billable`, `cache_read`, `tokens_out` and `seconds` by stage, task or role. `tokens` is the full context including cache; `tokens_billable` is input plus output only. USD at `0.0` or `null` is not a price but “not measured”.

All of this lives as files under `runs/<id>/`: a run can be archived, handed over and checked later — not only viewed in a chat or a cloud.

## What the factory does not promise

- **Not a “fully autonomous factory”.** A human makes the key decisions — requirements, release, lessons. That is deliberate.
- **No built-in model.** The code does not “think”; a connected executor or a human performs the role.
- **No “22 agents in a minute” promise.** One run requires an interview, written requirements, architecture and review.
- **No issue tracker, no cloud and no deployment.** Tasks and the product live in the run's files; hosting and support are not part of the job.
- **No absolute safety.** The sandbox is a file boundary; the same uid and the network remain. Naming that limitation is better than hiding it.
- **USD is not measured.** `0.0` or `null` in `usage.jsonl` is not “free” but “the provider did not return a price”. Compare `tokens_billable`.

## Who it is for

- a founder or a small team with an idea but no time or energy to assemble a development process;
- a team that already has a project but no resources to finish it;
- a customer who needs a report of what was checked and by whom, not a screenshot of a chat;
- a team lead or an auditor who needs the trail behind a decision;
- a newcomer who needs questions, plain-language explanations and ready-made templates.

**Not for you** if you want a chat that writes code without a human; if this is a throwaway prototype nobody will accept; or if you are not willing to write requirements and an architecture.

## What it looks like in practice

```bash
# 1. Install and check the environment
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
factory doctor

# 2. Create a run and get the first work order
factory init --run-id demo-01 --mode nano --idea "dependency-free CLI todo for one developer"
factory next --run-id demo-01

# 3. Take the templates, fill in the declared documents and submit them
cp factory/templates/interview-brief.tmpl runs/demo-01/artifacts/interview-brief.md
cp factory/templates/GLOSSARY.tmpl runs/demo-01/artifacts/GLOSSARY.md
factory submit --run-id demo-01 --stage s00_intake \
  --role 01-smart-interviewer \
  --artifact artifacts/interview-brief.md \
  --artifact artifacts/GLOSSARY.md
factory verify --run-id demo-01 --stage s00_intake

# 4. Close the human gate
factory gate approve --run-id demo-01 --gate gate_s00 \
  --approver human:your-name --evidence "brief accepted"

# 5. Walk the remaining stages by hand or with an executor
factory run --run-id demo-01 --executor fake --max-steps 60
```

`factory next` prints the work order: the stage, the role, which documents to submit, who reviews them and which command closes the stage. A human or an executor fills in the templates, submits them and the verifier returns a verdict. `factory run` does the same in a loop and stops at a human gate with exit code `2`, printing the exact command for the human.

Check the reference run without a model:

```bash
FACTORY_ROOT="$PWD/examples/nano-todo" \
FACTORY_RUNS_ROOT="$PWD/examples" \
factory verify --run-id nano-todo --stage s9_learn --require-gate
```

Expected: `VERIFY OK`. The reference run has 33 events and 3/3 requirements covered by tests; the product runs.

## What's inside

- **22 role cards** with eight sections: purpose, inputs, procedure, outputs, definition of done, veto, prohibitions, escalation.
- **13 stages** from `s_in`/`s00_intake` to `s9_learn`, each with an explicit gate.
- **A Python engine**: `init`, `next`, `submit`, `gate`, `verify`, `task`, `quarantine`, `trace`, `metrics`, `usage`, `doctor`.
- **The `factory run` runtime**: order → executor → verify → submit → gate; `pending_artifacts` and `next_role` in every order, `usage.jsonl` with token breakdown, evidence and stop-guards (state substitution, writes outside the run, budget). Adapters: `fake` (no model), `hermes`, `opencode`; real CLIs run in `bwrap` by default with a read-only control plane. `--parallel-tasks N` builds independent `s4_build` tasks in separate git worktrees — see [`docs/runtime.md`](docs/runtime.md).
- **Signed checkpoints**: `factory sign` stores an HMAC-SHA256 anchor of the journal head outside `runs/`; `factory verify` tells `stale` from `rolled_back` and `diverged` — see [`docs/checkpoints.md`](docs/checkpoints.md).
- **An external eval set and run comparison**: `factory eval` drives fixed cases (`nano-todo`, `nano-product-mini`, `standard-mini`, `brownfield-proxy-mini`, `product-mini`) on `fake` or a real executor, checks invariants and numeric baselines, and fails on extra steps, fewer events or more than 10 % token growth; `factory compare` shows the stage, cost and artifact-hash delta of two runs — see [`docs/eval.md`](docs/eval.md).
- **A SHA-256 hash-chained journal** with file locking, a state anchor and semantic `PASS` checks.
- **Producer != Verifier**: only `02-independent-verifier` can record `PASS`; a human can accept risk with a separate event.
- **SkillSpector 2.12.0 quarantine adapter** with a fail-closed decision table and a hash lock.
- **38 templates and 13 JSON schemas**; templates never pre-fill `PASS`.
- **A reference run** in `examples/nano-todo/`: 33 events, 2 tasks, 3 requirements, all gates closed, the product runs.

The numbers: 4 modes (3 of them exercised on a real project), 5 reference examples, 282 tests, CI on Ubuntu, macOS and Windows (Python 3.10–3.13).

## Modes

| Mode | Roles | Use |
| :--- | ---: | :--- |
| `nano` | 4 | Interviewer/spec author, fullstack builder, verifier, orchestrator. Smallest product. |
| `standard` | 12 | Product, analysis, architecture, fullstack build, QA, security, release. |
| `enterprise` | 22 | All roles, including mobile, DBA, data, docs and SRE. |
| `brownfield` | 12 | Standard plus a mandatory backup/restore drill before the first write. |

In `nano`, role `01-smart-interviewer` also writes `spec.md`, `spec-human.md`, `traceability.json` and `plan.md`, because roles `05` and `04` are not active. This is encoded in the role card and checked by `factory doctor`.

## Stages

| Stage | Output | Gate |
| :--- | :--- | :--- |
| `s_in` | Backup, restore drill, brownfield audit | human |
| `s00_intake` | Interview brief, glossary | human |
| `s0_vision` | Vision, backlog, competitors, metrics | human (advisory) |
| `s1_spec` | spec, spec-human, traceability, domain model, access matrix | human, blocking |
| `s15_quarantine` | Skill security review, skills lock | verifier |
| `s2_architecture` | Architecture, API contract, data model, threat model, ADR, UX, AI design | verifier |
| `s3_plan` | Plan, technical debt | verifier |
| `s4_build` | Deliverables, code, tests | verifier |
| `s5_acceptance` | Test report, acceptance matrix | verifier |
| `s6_security` | App security, DB report, data pipeline | verifier |
| `s7_release` | Deploy runbook, ops runbook, release notes | human |
| `s8_docs` | Product README, user guide | verifier |
| `s9_learn` | Metrics, lessons, improvement patches | human |

## Commands

| Command | Purpose |
| :--- | :--- |
| `factory doctor` | Check config, roles, schemas, templates, version and license |
| `factory init --run-id ID --mode MODE` | Create an isolated run |
| `factory status --run-id ID` | Show the stage, gates, tasks and journal |
| `factory next --run-id ID` | Print the next work order as JSON |
| `factory run --run-id ID --executor fake\|hermes\|opencode` | Run the loop: order → executor → verify → submit → gate. Exit codes: `0` done, `2` a human is needed, `3` escalation |
| `factory submit --run-id ID --stage S --role R --artifact P` | Submit producer artifacts |
| `factory verify --run-id ID [--stage S] [--require-gate]` | Deterministic verification |
| `factory sign --run-id ID --key-file KEY` | Sign the journal head with an HMAC checkpoint outside `runs/` |
| `factory gate approve\|reject\|accept-risk --gate G --approver human:ID` | Gate decision; `accept-risk` closes an escalation with a recorded risk |
| `factory stage reset --run-id ID --stage S --approver human:ID --reason T` | Reset an escalated stage |
| `factory veto --run-id ID --stage S --role R --code C --evidence E` | Record a blocking role veto |
| `factory task import\|list\|claim\|submit\|accept\|reject\|reset` | `s4_build` tasks |
| `factory artifact re-record --run-id ID --path P --role R --reason T --evidence E` | Explicitly re-record the hash of an accepted artifact: `ARTIFACT_RERECORD` with `old_sha256`; the gate is not reopened |
| `factory quarantine scan PATH --lock skills-lock.json` | Run SkillSpector and write the lock |
| `factory quarantine verify-lock --lock skills-lock.json` | Detect a substituted skill |
| `factory trace --run-id ID` | `REQ → task → test → acceptance` matrix |
| `factory metrics --run-id ID [--write]` | Eight health metrics |
| `factory eval --cases eval/cases [--executor fake] [--json]` | Run eval cases, check invariants and baseline; exit `1` on regression |
| `factory eval --cases eval/cases --update-baseline eval/baseline.json` | Rewrite the baseline (green cases only) |
| `factory compare --left ID --right ID [--json]` | Delta of two runs: stages, steps, tokens, cost, artifact hashes |
| `factory usage --run-id ID [--by stage\|task\|role] [--json]` | Spend from `runtime/usage.jsonl` by stage, task or role; `null` is not turned into zero |
| `factory budget [--run-id ID]` | Attempt limits, mode budget, used attempts |
| `factory artifacts` | Artifact registry |

## Journal and trust

`blackbox.jsonl` is the single source of truth. Each event carries `event_id`, `seq`, `timestamp_utc`, stage, task, role, model, `why`, `for_what`, `action`, artifact hashes, verdict, `prev_hash` and `hash`.

- Writes are serialized with `fcntl.flock`, so parallel roles cannot corrupt the chain.
- `state.json` stores the last hash and event count; deleting the journal tail is detected.
- `submit`, `gate approve`, `task accept` and `artifact re-record` bind SHA-256 hashes, and `factory verify` re-hashes the files on disk: a change after submission or acceptance is reported as `artifact tampered`.
- A producer cannot record `PASS`; the rule is enforced at append time and at verification time.
- `AUDIT_TRAIL.md` is rendered from JSONL with `|` and newlines escaped.
- The chain itself is **not signed**: it detects accidental edits and naive tampering, not a determined attacker who recomputes every hash. For that threat model there is `factory sign`, an HMAC checkpoint outside `runs/`.

## Quarantine

```bash
factory quarantine scan ./skills/my-skill \
  --name my-skill --pack local --commit local --license MIT \
  --lock skills-lock.json --no-llm
```

| Recommendation | Complete | Exit | Action |
| :--- | :--- | :--- | :--- |
| `SAFE` | true, coverage ≥ 95 | 0 | auto-allow + lock |
| `CAUTION` | true | 0 | human |
| `DO_NOT_INSTALL` | any | 0 | purge |
| any | false / failed analyzer | any | block |
| any | true | ≠ 0 | block + retry |

The lock stores `pack`, `commit`, `path`, the directory `sha256`, `license`, scanner version, `severity`, `recommendation`, `is_complete`, `coverage_percent`, `scanned_at` and `approved_by`. `factory quarantine verify-lock` recomputes the hash and detects a substituted file.

## Structure

```text
factory/                 # engine and configs
  cli.py                 # commands
  journal.py             # hash chain, locking, state anchor
  engine.py              # init/next/submit/gate/task
  artifacts.py           # registry, Markdown/JSON checks
  pipeline.py            # stages, modes, owners
  quarantine.py          # SkillSpector adapter
  runtime/               # the executable loop
    base.py              # Executor protocol
    loop.py              # order → executor → verify → submit → gate
    prompt.py            # prompt assembly
    submit.py            # which paths may be submitted
    guard.py             # tripwire on state.json and the run directory
    usage.py             # usage.jsonl and mode budget
    evidence.py          # runtime/evidence/*.json
    fake.py              # deterministic executor
    opencode.py          # OpenCode adapter
    hermes.py            # Hermes adapter
    sandbox.py           # bwrap: read-only root and control plane
    fixtures/nano/       # reference artifacts for fake
  roles/                 # 22 cards
  schemas/               # 13 JSON schemas
  templates/             # 38 templates
  pipeline.yaml          # stage graph and modes
  artifacts.yaml         # artifact registry
  models.yaml            # tier → runtime/env
  budget.yaml            # limits and behavior
tests/                   # invariants, CLI, concurrency, schemas, runtime
examples/nano-todo/      # reference run
docs/                    # quickstart, runtime, checkpoints, eval, eval-plan, threat model, metrics, adapters
```

## Limitations

- `models.yaml` contains no vendor model IDs. Tiers resolve through the runtime or `FACTORY_MODEL_TIER_1/2/3`; the journal records the `model_id` the runtime actually passed.
- Cost metrics stay `null` until the runtime records tokens and cost. We do not invent numbers.
- Brownfield supports git repositories only.
- `s4_build` imports tasks from the Markdown table in `plan.md`; arbitrary plan formats are not parsed.
- `examples/nano-todo` is a deterministic reference run, not proof of model autonomy.
- `factory run` executes one step per executor call and stops at a human gate.
- The hash chain is not signed; `factory sign` is a separate command and `factory run` does not sign automatically.

## Development

```bash
python -m pip install -e ".[dev]"
pytest -q
factory doctor
python examples/build_nano_todo.py
```

CI: Python 3.10–3.13, `doctor`, `pytest`, the example check and the package build.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
