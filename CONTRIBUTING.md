# Contributing

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
factory doctor
pytest -q
```

## What to run before a PR

```bash
factory doctor
pytest -q
python examples/build_nano_todo.py
FACTORY_ROOT="$PWD/examples/nano-todo" FACTORY_RUNS_ROOT="$PWD/examples" \
  factory verify --run-id nano-todo --stage s9_learn --require-gate
python -m build
```

All must pass. CI runs the same set on Python 3.10–3.13.

## Changing a role card

Every card needs:

- YAML frontmatter with `role_id`, `title`, `default_model_tier`,
  `reasoning_effort`, `active_stages`, `write_scopes`, `veto_type`,
  `veto_scope`, `blackbox_logging`;
- the eight sections in order: Назначение, Входы, Процедура, Выходы,
  Критерии готовности, Право вето, Запреты, Эскалация;
- a body unique from the other 21 cards;
- `active_stages` that exist in `pipeline.yaml`;
- `write_scopes` from `code`, `own_artifacts`, `factory_state`.

## Changing the pipeline

1. Edit `factory/pipeline.yaml`.
2. Update `factory/artifacts.yaml` if a new artifact appears.
3. Add a template and, for JSON, a schema.
4. Run `factory doctor`; it checks order, owners in every mode, gates and
   template sections.
5. Update `PIPELINE.md` and `ROSTER.md` if the change is user-visible.

## Changing an invariant

Do not weaken:

- `Producer != Verifier`;
- the human gates `s1_spec`, `s7_release`, `s9_learn`;
- `--fail-on-incomplete`;
- run-id/path validation;
- state anchor and journal semantics.

Such a change needs an ADR, a test, and an explicit human commit — not a normal
PR.

## Pull request checklist

- [ ] `factory doctor` passes.
- [ ] `pytest -q` passes.
- [ ] Reference run still verifies.
- [ ] New behavior has a test (positive and negative).
- [ ] No secrets, `.env`, `runs/` or generated caches in the diff.
- [ ] Docs updated if a command, stage, role or artifact changed.

## Documentation

`tests/test_docs.py` reads every fenced code block and treats a line starting
with `factory <subcommand>` as a documented command that must exist in the CLI
parser. A plan or roadmap that names a future command inside a fence fails the
suite; describe planned commands in a table or as inline code until the parser
learns them.

## Style

- Python 3.10+, stdlib where reasonable; `PyYAML` and `jsonschema` are the only
  runtime dependencies.
- No silent `except: pass`.
- Errors are `FactoryError` subclasses with user-facing messages.
- Markdown templates must not pre-fill `PASS` or `[ПРОВЕРЕНО]`.
