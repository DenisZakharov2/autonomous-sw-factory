# OpenCode adapter

## One-shot mode (`factory run --executor opencode`)

`factory/runtime/opencode.py` runs:

```bash
opencode run --auto --standalone --format json [--model provider/model] "<prompt>"
```

- `--format json` streams events; the adapter reads `usage`/`cost` if present and
  treats a `{"type":"error"}` event as a failed step even when the CLI exits 0.
- `--standalone` avoids depending on the background server.
- The step has a hard timeout (`--timeout`, default 1800 s). Without a selected
  provider `opencode run` hangs and prints `Transport: socket closed`; the
  timeout turns that into exit code 124 instead of a stuck factory.
- Provider must be configured first (`opencode auth login` or a provider in
  `~/.config/opencode/opencode.jsonc`). Version tested: `v2.0.29`.

Generated prompts and stdout/stderr land in `runs/<id>/runtime/ev-*.prompt.md`
and `ev-*.log`.

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
