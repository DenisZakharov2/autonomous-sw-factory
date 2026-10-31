# Autonomous Software Factory

**Version 1.0.1.** A toolkit that makes AI-assisted development provable: work moves through stages with acceptance gates, every stage has a document to hand in, and a journal keeps a digital fingerprint of every step and every file. It is not a "fully autonomous factory": a human makes the calls at the key gates.

[![CI](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml) · [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Русский README](README.md) · [License](LICENSE) · [Reference run](examples/nano-todo/) · [Audit](AUDIT.md) · [Quick start](docs/quickstart.md)

---

## Why this exists

An AI agent writes code in minutes. Checking that work is still expensive: you cannot tell whether the thing you asked for was actually built, who reviewed it, or whether a file changed after it was accepted. Usually there is no evidence — just a chat log, a list of changes and the developer's word.

The factory turns that into a process with evidence:

- work is split into **stages** with explicit acceptance gates;
- each stage has a **document** that is submitted and checked;
- only an **independent verifier** can accept a result — the author cannot mark their own work as passed;
- a **journal** keeps a digital fingerprint of every step and every file.

## What you get

- **Proof that the work matches the written requirements.** `factory trace` shows the "requirement → task → test → acceptance" chain and names the gaps; a link to a test that does not exist is a gap too.
- **Substitution after acceptance is visible.** `factory verify` re-hashes the files on disk: if a file changed after submission or acceptance, the check fails with `artifact tampered`.
- **You can see who changed an accepted file and why.** A legitimate edit is recorded with `factory artifact re-record`, with a reason and a link to evidence; `factory verify` prints how many records changed after acceptance.
- **A run can be repeated and compared.** `factory eval` drives five reference examples on the built-in `fake` executor (no model) or a real one and compares steps, events and (for real runs) tokens with the recorded reference; `factory compare` shows the delta between two runs.
- **A report you can hand to a customer.** `AUDIT_TRAIL.md` is rendered from the journal: who, when, what was submitted, what the verdict was and how the record links to the previous one. The summary is `factory status`; the check is `factory verify`.
- **Bring your own executor.** `fake` (no model, proves the loop), Hermes, OpenCode — or a human doing the steps by hand.
- **An agent cannot accept its own work.** Only an independent verifier can mark it accepted; a real executor runs in an isolated environment where the control files are read-only.

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

## What this is not

- **Not a "fully autonomous factory".** A human decides on the requirements, the release and the lessons, and performs the restore check before working on an existing project. That is deliberate.
- **No built-in model.** The code does not "think"; a connected executor or a human performs the role.
- **No "22 agents in a minute" promise.** One run requires an interview, written requirements, architecture and review.
- **No issue tracker and no cloud.** Tasks live in the run's files.

## Who it is for

- a small team or a founder who hands development to an AI agent and wants a result they can check;
- a customer who needs a report of what was checked and by whom, not a screenshot of a chat;
- a team lead or an auditor who needs the trail behind a decision, not just the final code.

**Not for you** if you want a chat that writes code without a human; if this is a throwaway prototype nobody will accept; or if you are not willing to write requirements and an architecture.

## What's inside

- **22 role cards** with eight sections: purpose, inputs, procedure, outputs, definition of done, veto, prohibitions, escalation.
- **13 stages** from `s_in`/`s00_intake` to `s9_learn`, each with an explicit gate.
- **A Python engine**: `init`, `next`, `submit`, `gate`, `verify`, `task`, `quarantine`, `trace`, `metrics`, `doctor`.
- **The `factory run` runtime**: order → executor → verify → submit → gate; `pending_artifacts` and `next_role` in every order, `usage.jsonl` with token breakdown, evidence and stop-guards (state substitution, writes outside the run, budget). Adapters: `fake` (no model), `hermes`, `opencode`; real CLIs run in `bwrap` by default with a read-only control plane. `--parallel-tasks N` builds independent `s4_build` tasks in separate git worktrees — see [`docs/runtime.md`](docs/runtime.md).
- **Signed checkpoints**: `factory sign` stores an HMAC-SHA256 anchor of the journal head outside `runs/`; `factory verify` tells `stale` from `rolled_back` and `diverged` — see [`docs/checkpoints.md`](docs/checkpoints.md).
- **An external eval set and run comparison**: `factory eval` drives fixed cases (`nano-todo`, `nano-product-mini`, `standard-mini`, `brownfield-proxy-mini`, `product-mini`) on `fake` or a real executor, checks invariants and numeric baselines, and fails on extra steps, fewer events or more than 10 % token growth; `factory compare` shows the stage, cost and artifact-hash delta of two runs — see [`docs/eval.md`](docs/eval.md).
- **A SHA-256 hash-chained journal** with file locking, a state anchor and semantic `PASS` checks.
- **Producer != Verifier**: only `02-independent-verifier` can record `PASS`; a human can accept risk with a separate event.
- **SkillSpector 2.12.0 quarantine adapter** with a fail-closed decision table and a hash lock.
- **38 templates and 13 JSON schemas**; templates never pre-fill `PASS`.
- **A reference run** in `examples/nano-todo/`: 33 events, 2 tasks, 3 requirements, all gates closed, the product runs.

The numbers: 4 modes (3 of them exercised on a real project), 5 reference examples, 274 tests, CI on Ubuntu, macOS and Windows (Python 3.10–3.13).

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
