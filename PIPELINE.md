# PIPELINE.md — стадии, гейты и режимы

Машиночитаемый источник: [`factory/pipeline.yaml`](factory/pipeline.yaml). Реестр артефактов: [`factory/artifacts.yaml`](factory/artifacts.yaml). Если документ и YAML расходятся, прав YAML; `factory doctor` проверяет это.

## 1. Стадии

| № | ID | Название | Роли (базовый набор) | Артефакты | Гейт | Попытки |
| ---: | :--- | :--- | :--- | :--- | :--- | ---: |
| 0 | `s_in` | Brownfield intake | 00, 01, 08, 02 | onboarding-plan, restore-drill, brownfield-audit | `gate_s_in` — человек | 2 |
| 10 | `s00_intake` | Greenfield interview | 00, 01, 02 | interview-brief, GLOSSARY | `gate_s00` — человек | 3 |
| 20 | `s0_vision` | Vision and scope | 03, 01, 02 | vision, backlog, competitors, product metrics | `gate_s0` — человек | 3 |
| 30 | `s1_spec` | Dual-view spec | 05, 06, 01, 02 | spec, spec-human, traceability, domain-model, access-matrix | `gate_s1` — человек, blocking | 3 |
| 40 | `s15_quarantine` | Skill quarantine | 16, 00, 02 | security-review-skills, skills-lock | `gate_s15` — 02 | 3 |
| 50 | `s2_architecture` | Architecture | 08, 07, 09, 17, 16, 02 | architecture, api-contract, data-model, threat-model, ADR, ux-flows, screens, ai-design | `gate_s2` — 02 | 3 |
| 60 | `s3_plan` | Task decomposition | 04, 02 | plan, technical-debt | `gate_s3` — 02 | 3 |
| 70 | `s4_build` | Parallel build | 09, 10, 11, 12, 13, 15, 18, 04, 02 | deliverable, product/src, product/tests | `gate_s4` — 02 | 3 |
| 80 | `s5_acceptance` | Acceptance | 14, 15, 02 | test-report, acceptance | `gate_s5` — 02 | 3 |
| 90 | `s6_security` | App security | 16, 17, 18, 11, 12, 02 | security-review-app, db-report, data-pipeline | `gate_s6` — 02 | 3 |
| 100 | `s7_release` | Release | 19, 20, 02 | runbook-deploy, runbook-ops, release-notes | `gate_s7` — человек | 3 |
| 110 | `s8_docs` | Product docs | 21, 14, 12, 02 | product/README, user-guide | `gate_s8` — 02 | 3 |
| 120 | `s9_learn` | Metrics and improvement | 00, 03, 02 | metrics, lessons-learned, patches | `gate_s9` — человек | 2 |

`max_attempts` — число отказов гейта до `escalated`. `rework_to` — стадия, куда возвращается работа; для `s5` и `s6` это `s4_build`.

## 2. Режимы

| Режим | Роли | Бюджет |
| :--- | :--- | :--- |
| `nano` | 00, 01, 12, 02 | 2.5M токенов, 120 мин, $6 |
| `standard` | 00, 01, 02, 03, 04, 05, 06, 08, 12, 14, 16, 19 | 2.5M, 120 мин, $25 |
| `enterprise` | все 22 | 6M, 1440 мин, $120 |
| `brownfield` | те же 12, что `standard` | 1.8M, 480 мин, $30 |

`nano` и `standard` — по замеру полного реального прогона (`nano`: 1.78M
total, три прогона 1.78–1.96M; `standard-mini`: 1.98M, 15 шагов); остальные —
расчёт, на реальном Hermes не проверялись.

Бюджеты — **предохранители**, а не измеренная стоимость: `nano` и `standard`
откалиброваны по замеру полного прогона, `enterprise` и `brownfield` — расчёт. `factory metrics` возвращает `cost_per_artifact_usd: null`, пока рантайм не пишет токены и цену. Не выдавайте оценку за факт.

В `nano` роль `01-smart-interviewer` пишет `spec`, `spec-human`, `traceability` и `plan`; роль `12-fullstack-developer` — архитектуру, код, тесты, приёмку, security, release и docs. Это компромисс минимализма; он записан в `stage_roles` и `artifact_owners`.

## 3. Work order

`factory next` возвращает JSON:

```json
{
  "schema_version": "1.0.0",
  "action": "produce",
  "run_id": "demo-01",
  "stage": "s1_spec",
  "stage_title": "Dual-view specification",
  "roles": ["05-business-analyst", "06-system-analyst", "02-independent-verifier"],
  "task_id": null,
  "artifacts": [
    {
      "id": "spec",
      "path": "artifacts/spec.md",
      "template": "templates/spec.tmpl",
      "schema": null,
      "owner": "05-business-analyst",
      "required_sections": ["## 1. Требования", "## 2. Приёмка"]
    }
  ],
  "instructions": "Stage s1_spec: produce the listed artifacts...",
  "gate": {"id": "gate_s1", "approver": "human", "blocking": true, "evidence": ["factory verify --stage s1_spec"]},
  "next_command": "factory submit --run-id demo-01 --stage s1_spec --role <role> --artifact <path>"
}
```

`action`:

| Значение | Что делать |
| :--- | :--- |
| `produce` | Создать артефакты по шаблонам и сдать |
| `verify` | Артефакты сданы; запустить `factory verify` |
| `gate` | Артефакты валидны; нужен человек или владелец гейта |
| `wait` | Задача в работе; ждать |
| `blocked` | Нужна эскалация |
| `complete` | Все стадии приняты |

## 4. Задачи `s4_build`

`plan.md` должен содержать таблицу:

```markdown
| `task_id` | Роль | `REQ` | `depends_on` | `Allowed Files` | Команда проверки |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `TASK-001` | `12-fullstack-developer` | `REQ-001`, `REQ-002` | `-` | `product/src/todo.py`, `product/tests/test_todo.py` | `python3 -m unittest discover -s product/tests` |
```

`factory gate approve --gate gate_s3` импортирует таблицу в `state.json`. Жизненный цикл:

```text
pending -> in_progress -> review -> accepted
                         \-> pending (rework, attempts < 3)
                         \-> escalated (attempts >= 3)
```

Команды:

```bash
factory task list --run-id <id>
factory task claim --run-id <id> --task-id TASK-001 --role 12-fullstack-developer
factory task submit --run-id <id> --task-id TASK-001 --role 12-fullstack-developer --artifact product/src/todo.py
factory task accept --run-id <id> --task-id TASK-001
factory task reject --run-id <id> --task-id TASK-001 --reason "test missing"
```

`task accept` пишет `TASK_DONE` со статусом `PASS` от `02-independent-verifier` и evidence. `task reject` пишет `REWORK FAIL` и увеличивает `attempts`.

## 5. Гейты

Гейт закрывается командой:

```bash
factory gate approve --run-id <id> --gate gate_s1 \
  --approver human:denis --evidence "spec-human approved"
```

Правила:

- человеческий гейт принимает только `human:<id>`;
- роль-гейт принимает только объявленного владельца (`02-independent-verifier` или `19-devops-engineer`);
- перед закрытием выполняется `factory verify` без требования закрытого гейта; ошибки блокируют решение;
- `gate_s1` выставляет `human_spec_approved: true`;
- `gate_s_in` выставляет `restore_drill_passed: true`;
- отказ увеличивает `attempts`; после `max_attempts` стадия становится `escalated`.

## 6. Возобновление

Состояние хранится в `runs/<run_id>/state.json`. `factory next` читает его и продолжает с первой непринятой стадии. Принятые задачи не повторяются. Если процесс упал:

```bash
factory status --run-id <id>
factory next --run-id <id>
```

Если `state.json` повреждён, `factory verify` сообщит об ошибке; не редактируйте state вручную, восстановите из архива или начните прогон с `--force` (старый уйдёт в `.archive/`).

## 7. Проверки

`factory verify` проверяет:

- JSON-схемы `state`, `event`, `pipeline`, `skills-lock`, `traceability`, `metrics`;
- 22 карточки: 8 разделов, уникальность, `active_stages`, `write_scopes`, tier;
- pipeline: порядок стадий, владельцы артефактов в каждом режиме, гейты;
- шаблоны: обязательные разделы;
- журнал: цепочка, seq, event_id, семантика `PASS`;
- state: `last_event_hash`, `event_count`, `current_stage`;
- артефакты стадии: разделы Markdown, JSON-схемы, REQ-паритет, покрытие плана и приёмки.

`factory verify --stage <id> --require-gate` дополнительно требует закрытый гейт и человеческие флаги.

## 8. Ошибки и эскалация

| Ситуация | Действие |
| :--- | :--- |
| Артефакт не проходит схему | `REWORK`, вернуть продюсеру |
| REQ-паритет нарушен | Блокирующее вето `05` / `02` |
| 3 отказа задачи | `task escalated`, человек |
| 3 отказа гейта | `stage escalated`, человек |
| Неполный скан скилла | `BLOCK_INCOMPLETE`, установка запрещена |
| Сканер упал | `BLOCK_RETRY`, не установка |
| Откат не проверен | `s7_release` не закрывается |
| Человек снимает вето | `RISK_ACCEPTANCE` с риском, сроком, ответственным |
