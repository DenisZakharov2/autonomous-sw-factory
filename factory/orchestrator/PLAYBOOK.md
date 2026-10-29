# PLAYBOOK.md — операционный регламент

## 1. Роли в исполнении

| Кто | Что делает |
| :--- | :--- |
| Человек | Выбирает режим, проходит интервью, одобряет `spec-human`, релиз и self-improvement |
| Оркестратор (`00`) | Ведёт state, выдаёт work orders, следит за бюджетом и эскалациями |
| Продюсеры | Создают артефакты в своих scopes, сдают `deliverable.md` |
| Верификатор (`02`) | Запускает `factory verify`, проводит 3 adversarial-проверки, ставит `PASS` |
| Security (`16`) | Сканирует скиллы, пишет `skills-lock.json`, проверяет приложение |
| DevOps (`19`) | Деплой, rollback drill, release notes |

## 2. Запуск

```bash
factory doctor
factory init --run-id <id> --mode nano|standard|enterprise|brownfield
factory status --run-id <id>
factory next --run-id <id>
```

`factory next` — единственный источник «что делать дальше». Не угадывайте стадию по документации.

## 3. Цикл стадии

1. `next` вернул `produce` → прочитать шаблоны и карточки ролей.
2. Создать артефакты; заполнить `tasks/<task_id>/deliverable.md` (что, REQ, проверка, diff).
3. `factory submit` от имени продюсера.
4. `factory verify --stage <stage>` — устранить ошибки.
5. `factory gate approve` или `reject` — решение человека/верификатора.
6. `next` — следующая стадия.

## 4. Задачи

```bash
factory task import --run-id <id>                 # из plan.md
factory task list --run-id <id>
factory task claim --run-id <id> --task-id TASK-001 --role 12-fullstack-developer
factory task submit --run-id <id> --task-id TASK-001 --role 12-fullstack-developer --artifact product/src/x.py
factory task accept --run-id <id> --task-id TASK-001
factory task reject --run-id <id> --task-id TASK-001 --reason "test missing"
```

`task accept` делает только `02-independent-verifier`. После трёх отказов задача уходит в `escalated`.

## 5. Гейты

```bash
factory gate approve --run-id <id> --gate gate_s1 \
  --approver human:name --evidence "spec-human approved"
```

- `gate_s1` требует `human_spec_approved`; человек читает `spec-human.md`, а не `spec.md`.
- `gate_s7` требует `runbook-deploy.md` с фактическим rollback drill.
- `gate_s9` требует metrics, lessons и human review патча.
- `gate_s_in` требует `restore-drill.md` с `PASS` и evidence.

## 6. Сбои

| Симптом | Действие |
| :--- | :--- |
| `factory verify` показывает missing artifact | Продюсер не сдал файл; вернуть в `produce` |
| REQ parity failed | `05`/`01` правит `spec-human.md`; `02` не закрывает гейт |
| `state.json` mismatch | Не редактировать вручную; `factory log verify`, восстановить из `.archive/` |
| Сканер скиллов неполный | `BLOCK_INCOMPLETE`; не устанавливать |
| 3 отказа | `escalated`; пакет вопросов человеку |
| Бюджет исчерпан | `pause`; человек решает `degrade`, `extend` или `stop` |

## 7. Бюджет

Бюджеты режимов — в `factory/pipeline.yaml`. Они оценочные. `factory metrics` не выдумывает стоимость: `cost_per_artifact_usd = null`, пока рантайм не пишет токены и цену. При исчерпании — `pause`, не молчаливое продолжение.

## 8. Завершение

```bash
factory verify --run-id <id> --stage s9_learn --require-gate
factory trace --run-id <id>
factory metrics --run-id <id> --write
factory log verify --run-id <id>
```

Прогон считается закрытым, когда все стадии `accepted`, все гейты `approved`, `traceability` 100%, `metrics` записаны, журнал валиден.
