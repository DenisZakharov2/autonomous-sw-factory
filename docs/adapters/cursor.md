# Cursor adapter

## Capabilities

| Capability | Support |
| :--- | :--- |
| Subagents | no native equivalent |
| Per-role model | no |
| File writes | yes |
| Shell | yes |
| Hooks | no |

## Run a stage

1. `factory next --run-id <id>` in the terminal.
2. Open one Cursor chat per role. Paste the role card path, work order, template and write scope.
3. The chat writes artifacts; close it; open a fresh chat for the next role.
4. Parent runs `factory verify`, `factory submit`, `factory gate approve`.
5. Copy the handoff into `tasks/<task_id>/deliverable.md`; do not rely on chat history.

## Degradations

- No per-role model routing: one model per session. Record the actual model in the journal.
- No isolated subagent context: manually start a new chat and do not paste producer reasoning into the verifier chat.
- No hooks: run `factory doctor` and `factory log verify` manually.

## Rule

If a step requires a capability Cursor lacks, mark it `human_step` in the handoff. Silent skipping is forbidden.
