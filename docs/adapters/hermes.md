# Hermes Agent adapter

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
