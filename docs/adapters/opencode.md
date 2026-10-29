# OpenCode adapter

## Capabilities

| Capability | Support |
| :--- | :--- |
| Subagents | yes (agent sessions) |
| Per-role model | yes, per agent config |
| File writes | yes |
| Shell | yes |
| Hooks | partial |

## Run a stage

1. `factory next --run-id <id>`.
2. Start an agent with the role card and work order; keep a fresh context.
3. Agent writes artifacts and `deliverable.md`; parent runs `factory verify` and `factory submit`.
4. Human gates via `factory gate approve --approver human:<id>`.

## Notes

- Do not rely on automatic hooks; run `factory doctor` and `factory log verify` manually.
- If an agent cannot write files, it returns a patch and the parent applies it; still record the artifact hash via `factory submit`.
- Model IDs differ from Claude/OpenAI; record the actual ID in `models.yaml` override or env, not a fake tier name.

## Degradations

- No universal hook API: CI must run `factory verify` anyway.
- Per-agent tool permissions vary; set write scope to the task's `Allowed Files`.
