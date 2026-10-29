# Claude Code adapter

## Capabilities

| Capability | Support |
| :--- | :--- |
| Subagents | yes (Task tool) |
| Per-role model | yes (per subagent) |
| File writes | yes |
| Shell | yes |
| Hooks | yes |

## Run a stage

1. `factory next --run-id <id>` in the terminal.
2. Open one subagent per role with the role card and work order. Use a fresh context per role.
3. The subagent writes only its artifacts and `deliverable.md`.
4. Parent runs `factory verify`, then `factory submit`.
5. Human gates: `factory gate approve --approver human:<id>`.
6. Optional hook: run `factory doctor` on session start; run `factory log verify` before commit.

## Notes

- `AGENTS.md` is read-only entry; it must not auto-init a run.
- Skills with `disable-model-invocation: true` are human slash-command steps; if a role card references one, mark it `human_step` in the handoff.
- Do not give the verifier the producer's transcript; give paths and artifacts only.

## Degradations

- If the runtime cannot pin a model per subagent, record the actual model in the journal via `factory` and accept a single tier.
- If hooks are unavailable, run `factory doctor` manually before each session.
