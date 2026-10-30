# Autonomous Software Factory

**Версия 0.2.2.** Это исполнимый kit для стадийной разработки ПО с независимой проверкой, а не «полностью автономная фабрика из 22 думающих агентов». Движок выдаёт work orders, проверяет артефакты и ведёт журнал; роли исполняет рантайм (`factory run` с адаптерами Hermes, OpenCode или детерминированным fake), либо человек.

[![CI](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml) · [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English README](README.en.md) · [Лицензия](LICENSE) · [Пример прогона](examples/nano-todo/) · [Аудит](AUDIT.md)

---

## Что это

- **22 ролевые карты** с восемью разделами: назначение, входы, процедура, выходы, Definition of Done, вето, запреты, эскалация.
- **13 стадий** от `s_in`/`s00_intake` до `s9_learn` с явными гейтами.
- **Исполнимый Python-движок**: `init`, `next`, `submit`, `gate`, `verify`, `task`, `quarantine`, `trace`, `metrics`, `doctor`.
- **Рантайм `factory run`**: work order → исполнитель → проверка → сдача → гейт; `pending_artifacts` и `next_role` в каждом ордере, `usage.jsonl` с durable-шагом и разбивкой токенов, evidence и стоп-гарды (подмена состояния, запись вне прогона, бюджет). Адаптеры: `fake` (без модели), `hermes`, `opencode`; реальные CLI запускаются в `bwrap` по умолчанию, управляющая плоскость read-only. `standard` проверен до `complete`; `--parallel-tasks N` собирает независимые задачи `s4_build` в отдельных git worktree; `--executor-tools auto` платит за `file`, а `terminal` включает только на сборке — см. [`docs/runtime.md`](docs/runtime.md).
- **Подписанные контрольные точки**: `factory sign` кладёт HMAC-SHA256 головы журнала в store вне `runs/`; `factory verify` отличает `stale` от `rolled_back` и `diverged`, то есть усечение журнала и пересчёт всей цепочки — см. [`docs/checkpoints.md`](docs/checkpoints.md).
- **Журнал с хеш-цепочкой** SHA-256, блокировкой записи, state-якорем и семантической проверкой `PASS`.
- **Независимый верификатор**: `PASS` может поставить только `02-independent-verifier`; человек может принять риск отдельным событием.
- **Карантин скиллов**: адаптер `NVIDIA/SkillSpector 2.12.0` с fail-closed и локом хешей.
- **Шаблоны и схемы**: 38 шаблонов и 9 JSON-схем; шаблоны не ставят `PASS` заранее.
- **Эталонный прогон** `examples/nano-todo/`: 33 события, 2 задачи, 3 REQ, все гейты пройдены, продукт запускается.

## Чего здесь нет

- **Нет встроенной модели.** Код не «думает»; роль исполняет рантайм. `factory run` умеет вызвать Hermes или OpenCode, но по умолчанию работает `fake` — детерминированный исполнитель на эталонных артефактах, который доказывает цикл, а не автономность.
- **Нет полной автономности.** Гейты `s1_spec`, `s7_release`, `s9_learn` и Brownfield-drill требуют человека. Это сделано намеренно.
- **Нет трекера issues и облака.** Задачи живут в `state.json` и `tasks/`; внешние адаптеры — responsibility рантайма.
- **Нет обещания «22 агента за одну минуту».** Один прогон минимум требует интервью, ТЗ, архитектуры и проверки.

## Быстрый старт

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"

factory doctor
factory init --run-id demo-01 --mode nano
factory status --run-id demo-01
factory next --run-id demo-01
```

`factory next` печатает JSON work order: стадию, роли, артефакты, шаблоны, схемы, DoD, гейт и следующую команду. Рантайм (или человек) создаёт артефакты по шаблонам и сдаёт их.

Прогнать цикл без модели (детерминированный `fake` пишет эталонные артефакты):

```bash
factory run --run-id demo-01 --executor fake --max-steps 60
factory gate approve --run-id demo-01 --gate gate_s00 --approver human:your-name --evidence "brief accepted"
factory run --run-id demo-01 --executor fake --max-steps 60
```

С реальным исполнителем:

```bash
factory run --run-id live-01 --executor hermes --yolo --max-steps 60 --timeout 900 \
  --sandbox-rw ~/.hermes --sandbox-rw ~/.local/state --sandbox-rw ~/.cache
factory run --run-id live-01 --executor opencode --executor-model provider/model
```

Ручной путь остаётся доступным:

```bash
factory submit --run-id demo-01 --stage s00_intake \
  --role 01-smart-interviewer \
  --artifact artifacts/interview-brief.md \
  --artifact artifacts/GLOSSARY.md

factory verify --run-id demo-01 --stage s00_intake
factory gate approve --run-id demo-01 --gate gate_s00 \
  --approver human:your-name --evidence "brief accepted"

factory next --run-id demo-01
```

Проверить эталонный прогон:

```bash
FACTORY_ROOT="$PWD/examples/nano-todo" \
FACTORY_RUNS_ROOT="$PWD/examples" \
factory verify --run-id nano-todo --stage s9_learn --require-gate

FACTORY_RUNS_ROOT="$PWD/examples" factory trace --run-id nano-todo
FACTORY_RUNS_ROOT="$PWD/examples" factory metrics --run-id nano-todo
python -m unittest discover -s examples/nano-todo/product/tests -v
```

## Режимы

| Режим | Ролей | Для чего |
| :--- | ---: | :--- |
| `nano` | 4 | Один интервьюер/spec-author, один fullstack-разработчик, один верификатор, один оркестратор. Минимальный продукт. |
| `standard` | 12 | Продуктовая команда: product owner, аналитики, архитектор, fullstack, QA, security, DevOps. |
| `enterprise` | 22 | Все роли, включая mobile, DBA, data engineer, tech writer, SRE. |
| `brownfield` | 12 | Standard + обязательный backup/restore drill до первой записи. |

`nano` — честные 4 роли: `01-smart-interviewer` в этом режиме дополнительно пишет `spec.md`, `spec-human.md`, `traceability.json` и `plan.md`, потому что ролей `05` и `04` в нём нет. Это записано в карточке роли и проверяется `factory doctor`.

## Стадии и гейты

| Стадия | Выход | Гейт |
| :--- | :--- | :--- |
| `s_in` | Backup, restore-drill, brownfield audit | человек |
| `s00_intake` | Interview brief, GLOSSARY | человек |
| `s0_vision` | Vision, backlog, competitors, product metrics | человек (advisory) |
| `s1_spec` | spec, spec-human, traceability, domain model, access matrix | человек, блокирующий |
| `s15_quarantine` | Security review skills, skills-lock | `02-independent-verifier` |
| `s2_architecture` | Architecture, API contract, data model, threat model, ADR, UX, AI design | `02-independent-verifier` |
| `s3_plan` | Plan, technical debt | `02-independent-verifier` |
| `s4_build` | Deliverables, code, tests | `02-independent-verifier` |
| `s5_acceptance` | Test report, acceptance matrix | `02-independent-verifier` |
| `s6_security` | App security review, DB report, data pipeline | `02-independent-verifier` |
| `s7_release` | Deploy runbook, ops runbook, release notes | человек |
| `s8_docs` | Product README, user guide | `02-independent-verifier` |
| `s9_learn` | Metrics, lessons learned, improvement patches | человек |

## Команды

| Команда | Назначение |
| :--- | :--- |
| `factory doctor` | Проверить конфиг, роли, схемы, шаблоны, версию и лицензию |
| `factory init --run-id ID --mode MODE` | Создать изолированный прогон |
| `factory status --run-id ID` | Показать стадию, гейты, задачи, журнал |
| `factory next --run-id ID` | Выдать следующий work order в JSON |
| `factory run --run-id ID --executor fake\|hermes\|opencode` | Исполнить цикл: order → исполнитель → проверка → сдача → гейт. Коды: `0` готово, `2` нужен человек, `3` эскалация |
| `factory submit --run-id ID --stage S --role R --artifact P` | Сдать артефакты продюсера |
| `factory verify --run-id ID [--stage S] [--require-gate]` | Детерминированная проверка |
| `factory sign --run-id ID --key-file KEY` | Подписать голову журнала HMAC-чекпоинтом вне `runs/` |
| `factory gate approve|reject|accept-risk --gate G --approver human:ID` | Решение гейта; `accept-risk` закрывает эскалацию с записью риска |
| `factory stage reset --run-id ID --stage S --approver human:ID --reason T` | Сбросить эскалированную стадию |
| `factory veto --run-id ID --stage S --role R --code C --evidence E` | Блокирующее вето роли |
| `factory task import|list|claim|submit|accept|reject|reset` | Задачи `s4_build` |
| `factory quarantine scan PATH --lock skills-lock.json` | Прогнать SkillSpector и записать лок |
| `factory quarantine verify-lock --lock skills-lock.json` | Обнаружить подмену скилла |
| `factory trace --run-id ID` | Матрица `REQ → task → test → acceptance` |
| `factory metrics --run-id ID [--write]` | Восемь метрик здоровья |
| `factory budget [--run-id ID]` | Лимиты попыток, режимный бюджет, израсходованные попытки |
| `factory artifacts` | Реестр артефактов |

## Журнал и доверие

`blackbox.jsonl` — единственный источник истины. Каждое событие содержит `event_id`, `seq`, `timestamp_utc`, стадию, задачу, роль, модель, `why`, `for_what`, `action`, хеши артефактов, вердикт, `prev_hash` и `hash`.

Гарантии:

- запись идёт под `fcntl.flock`, поэтому параллельные роли не ломают цепочку;
- `state.json` хранит последний хеш и число событий, поэтому удаление хвоста журнала обнаруживается;
- `submit`, `gate approve` и `task accept` фиксируют SHA-256 всех артефактов, а `factory verify` перехеширует файлы на диске: подмена после сдачи или приёмки ловится как `artifact tampered`;
- `PASS` от продюсера отклоняется и при записи, и при проверке;
- `AUDIT_TRAIL.md` генерируется из JSONL и экранирует `|` и переводы строк;
- хеш-цепочка **не подписана**. Она защищает от случайной правки и наивной подмены, но не от злонамеренного пересчёта всей цепочки. Для сильной модели угроз добавьте HMAC/Ed25519-контрольную точку вне `runs/`.

## Карантин скиллов

```bash
factory quarantine scan ./skills/my-skill \
  --name my-skill --pack local --commit local --license MIT \
  --lock skills-lock.json --no-llm
```

Решение:

| `recommendation` | `is_complete` | exit code | Действие |
| :--- | :--- | :--- | :--- |
| `SAFE` | `true`, coverage ≥ 95 | 0 | `AUTO_ALLOW`, запись лока |
| `CAUTION` | `true` | 0 | `ESCALATE_HUMAN` |
| `DO_NOT_INSTALL` | любое | 0 | `REJECT_AND_PURGE` |
| любое | `false` / упавший анализатор | любой | `BLOCK_INCOMPLETE` |
| любое | `true` | ≠ 0 | `BLOCK_RETRY` |

Лок хранит `pack`, `commit`, `path`, `sha256` каталога, `license`, версию сканера, `severity`, `recommendation`, `is_complete`, `coverage_percent`, `scanned_at`, `approved_by`. `factory quarantine verify-lock` пересчитывает хеш и обнаруживает подмену файла.

## Структура

```text
factory/                 # движок и конфиги
  cli.py                 # команды
  journal.py             # хеш-цепочка, блокировка, state-якорь
  engine.py              # init/next/submit/gate/task
  artifacts.py           # реестр, Markdown/JSON проверки
  pipeline.py            # стадии, режимы, владельцы
  quarantine.py          # SkillSpector-адаптер
  runtime/               # исполняемый цикл
    base.py              # протокол Executor
    loop.py              # order → execute → verify → submit → gate
    prompt.py            # сборка промпта
    submit.py            # какие пути разрешено сдавать
    guard.py             # tripwire на state.json и каталог прогонов
    usage.py             # usage.jsonl и бюджет режима
    evidence.py          # runtime/evidence/*.json
    fake.py              # детерминированный исполнитель
    opencode.py          # адаптер OpenCode
    hermes.py            # адаптер Hermes
    sandbox.py           # bwrap: read-only корень и управляющая плоскость
    fixtures/nano/       # эталонные артефакты для fake
  roles/                 # 22 карточки
  schemas/               # 9 JSON-схем
  templates/             # 38 шаблонов
  pipeline.yaml          # граф стадий и режимы
  artifacts.yaml         # реестр артефактов
  models.yaml            # tier → runtime/env
  budget.yaml            # лимиты и поведение
tests/                   # инварианты, CLI, конкурентность, схемы, рантайм
examples/nano-todo/      # эталонный прогон
docs/                    # quickstart, runtime, checkpoints, threat model, metrics, adapters
```

## Ограничения

- `models.yaml` намеренно не содержит вендорских ID. Тир разрешается рантаймом или переменными `FACTORY_MODEL_TIER_1/2/3`. Журнал записывает фактический `model_id`, который передал рантайм.
- Стоимость в `metrics` остаётся `null`: `cost_per_artifact_usd` считается по журналу, а журнал не хранит цены. Рантайм пишет токены и цену отдельно, в `runtime/usage.jsonl`.
- `Brownfield` поддерживает только git-репозитории. Для папки без git используйте `standard` и сделайте бэкап вручную.
- `s4_build` принимает задачи из `plan.md`; произвольный формат плана не парсится.
- Полный прогон требует модели или человека. `examples/nano-todo` — детерминированный reference run, а не доказательство автономности.
- `factory run` исполняет по одному шагу за вызов исполнителя и останавливается на человеческом гейте. Параллельные задачи в git worktree — отдельный релиз `0.2.x`.

## Разработка

```bash
python -m pip install -e ".[dev]"
pytest -q
factory doctor
python examples/build_nano_todo.py
```

CI: Python 3.10–3.13, `doctor`, `pytest`, проверка примера и сборка пакета.

## Лицензия

MIT. См. [LICENSE](LICENSE) и [NOTICE](NOTICE).
