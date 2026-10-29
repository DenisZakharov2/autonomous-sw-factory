# Autonomous Software Factory

**Version 0.1.0-preview.** An executable, stage-gated software factory kit with an independent verifier and a hash-chained flight recorder. It is not a swarm of 22 thinking agents: the engine issues work orders and validates artifacts; a host runtime (Hermes, Claude Code, OpenCode, Cursor) or a human executes the roles.

[![CI](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml) · [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Русский README](README.md) · [License](LICENSE) · [Reference run](examples/nano-todo/)

## What you get

- **22 role cards** with eight sections: purpose, inputs, procedure, outputs, definition of done, veto, prohibitions, escalation.
- **13 stages** from `s_in`/`s00_intake` to `s9_learn`, each with an explicit gate.
- **A Python engine**: `init`, `next`, `submit`, `gate`, `verify`, `task`, `quarantine`, `trace`, `metrics`, `doctor`.
- **A SHA-256 hash-chained journal** with file locking, a state anchor and semantic `PASS` checks.
- **Producer != Verifier**: only `02-independent-verifier` can record `PASS`; a human can accept risk separately.
- **SkillSpector 2.12.0 quarantine adapter** with a fail-closed decision table and a hash lock.
- **38 templates, 9 JSON schemas, a 33-event reference run** in `examples/nano-todo/`.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"

factory doctor
factory init --run-id demo-01 --mode nano
factory next --run-id demo-01
```

`factory next` prints a JSON work order: stage, roles, artifacts, templates, schemas, definition of done, gate and the next command. A runtime (or a human) writes the artifacts and submits them:

```bash
factory submit --run-id demo-01 --stage s00_intake \
  --role 01-smart-interviewer \
  --artifact artifacts/interview-brief.md \
  --artifact artifacts/GLOSSARY.md

factory verify --run-id demo-01 --stage s00_intake
factory gate approve --run-id demo-01 --gate gate_s00 \
  --approver human:your-name --evidence "brief accepted"
```

Verify the reference run:

```bash
FACTORY_ROOT="$PWD/examples/nano-todo" \
FACTORY_RUNS_ROOT="$PWD/examples" \
factory verify --run-id nano-todo --stage s9_learn --require-gate

FACTORY_RUNS_ROOT="$PWD/examples" factory trace --run-id nano-todo
FACTORY_RUNS_ROOT="$PWD/examples" factory metrics --run-id nano-todo
python -m unittest discover -s examples/nano-todo/product/tests -v
```

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

## Trust model

`blackbox.jsonl` is the single source of truth. Each event carries `event_id`, `seq`, `timestamp_utc`, stage, task, role, model, `why`, `for_what`, `action`, artifact hashes, verdict, `prev_hash` and `hash`.

- Writes are serialized with `fcntl.flock`, so parallel roles cannot corrupt the chain.
- `state.json` stores the last hash and event count; deleting the journal tail is detected.
- A producer cannot record `PASS`; the rule is enforced at append time and at verification time.
- `AUDIT_TRAIL.md` is rendered from JSONL with `|` and newlines escaped.
- The chain is **not signed**. It detects accidental edits and naive tampering, not a determined attacker who recomputes every hash. Add an HMAC/Ed25519 checkpoint outside `runs/` for that threat model.

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

`factory quarantine verify-lock` recomputes the directory hash and detects a substituted file.

## Limitations

- `models.yaml` contains no vendor model IDs. Tiers resolve through the runtime or `FACTORY_MODEL_TIER_1/2/3`; the journal records the `model_id` the runtime actually passed.
- Cost metrics stay `null` until the runtime records tokens and cost. We do not invent numbers.
- Brownfield supports git repositories only.
- `s4_build` imports tasks from the Markdown table in `plan.md`; arbitrary plan formats are not parsed.
- `examples/nano-todo` is a deterministic reference run, not proof of model autonomy.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
