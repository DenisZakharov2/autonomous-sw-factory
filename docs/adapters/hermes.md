# Hermes Agent adapter

## One-shot mode (`factory run --executor hermes`)

`factory/runtime/hermes.py` runs:

```bash
hermes -z "<prompt>" --in <run_dir> --usage-file <runtime>/usage.json \
  --reasoning medium [--yolo] [-m model]
```

- `-z` answers one turn and exits; `--in` sets the working directory to the run.
- `--usage-file` writes real token counts; the adapter copies them into
  `runs/<id>/runtime/usage.jsonl`.
- `failed: true` in that file becomes a non-zero exit code: Hermes can exit 0
  after a provider error, and the factory must not treat an error message as an
  artifact.
- Reasoning level is provider-specific: some providers reject effort levels they
  do not implement and answer HTTP 400. Measured on this install: `medium` is
  rejected, `high` works — check the level with one short request before a long
  run instead of trusting a default.
- `--yolo` auto-approves the agent's own tool calls; it never closes a factory
  gate.
- Measured on a full `nano-todo` run: 13 steps, 1 780 308 total / 336 980
  billable tokens, 10.5 minutes, `verify` OK. The `nano` budget (2.5M) is sized
  from that measurement; raise it only for a more expensive model.

## Capabilities

| Capability | Support |
| :--- | :--- |
| Subagents | yes (`delegate_task`) |
| Per-role model | yes, via delegation config |
| File writes | yes |
| Shell | yes |
| Human gates | via chat / CLI |

## Run a stage

1. `factory next --run-id <id>` — take the JSON work order.
2. Spawn one subagent per role. Give it:
   - the role card path;
   - the work order JSON;
   - artifact templates and schemas;
   - write scope and `Allowed Files`;
   - the rule: never write `PASS`, never edit another role's artifacts.
3. The child writes files and returns a short handoff: files, REQ, check command, exit code, open questions.
4. Verify the files yourself (`factory verify`), then `factory submit`.
5. Gate: `factory gate approve` with `human:<id>` or `02-independent-verifier`.

## Notes

- Hermes subagents have isolated context; pass absolute paths, not conversation history.
- Children cannot ask the user questions. If a gate needs a human, stop and ask in the parent chat.
- For `nano`, roles `00` and `01` can be the parent; `12` and `02` are subagents.
- Do not let a child call `factory gate approve`; the parent records the decision after checking evidence.

## Example

```text
parent: factory next --run-id demo
parent: delegate_task(goal="Execute s00_intake as 01-smart-interviewer", context="work order ...")
child: writes artifacts/interview-brief.md, artifacts/GLOSSARY.md
parent: factory submit ... ; factory verify ... ; factory gate approve --approver human:denis
```
