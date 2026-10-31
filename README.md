# Autonomous Software Factory

**Версия 1.0.1.** Набор инструментов, который делает разработку с помощью ИИ проверяемой: работа идёт этапами с приёмкой, на каждом этапе есть сдаваемый документ, а журнал хранит цифровой отпечаток каждого шага и каждого файла. Это не «полностью автономная фабрика»: ключевые решения принимает человек.

[![CI](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml) · [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English README](README.en.md) · [Лицензия](LICENSE) · [Пример прогона](examples/nano-todo/) · [Аудит](AUDIT.md) · [Быстрый старт](docs/quickstart.md)

---

## Зачем это нужно

ИИ-агент пишет код за минуты. Проверить его работу по-прежнему дорого: непонятно, сделано ли именно то, что просили, кто смотрел результат и не изменился ли файл после приёмки. Обычно доказательств нет — остаются чат, список изменений и слово разработчика.

Фабрика превращает это в процесс с доказательствами:

- работа делится на **этапы** с явными воротами приёмки;
- на каждом этапе есть **документ**, который сдаётся и проверяется;
- результат принимает **независимый проверяющий** — автор не может поставить себе «принято»;
- **журнал** хранит цифровые отпечатки каждого шага и каждого файла.

## Что вы получаете

- **Доказательство, что сделано по техническому заданию (ТЗ).** `factory trace` показывает связку «требование → задача → тест → приёмка» и называет разрывы; ссылка на несуществующий тест — тоже разрыв.
- **Видно подмену после приёмки.** `factory verify` пересчитывает отпечатки файлов на диске: если файл изменили после сдачи или приёмки, проверка падает с `artifact tampered`.
- **Видно, кто и почему менял принятый файл.** Легальная правка оформляется командой `factory artifact re-record` с причиной и ссылкой на доказательство; `factory verify` печатает, сколько записей менялось после приёмки.
- **Прогон можно повторить и сравнить.** `factory eval` гоняет пять эталонных примеров на встроенном исполнителе `fake` (без модели) или реальном и сверяет шаги, события и (для реальных прогонов) токены с эталоном; `factory compare` показывает дельту двух прогонов.
- **Готовый отчёт для заказчика.** `AUDIT_TRAIL.md` собирается из журнала: кто, когда, что сдал, какой вердикт и как запись связана с предыдущей. Сводка — `factory status`, проверка — `factory verify`.
- **Исполнитель на выбор.** `fake` (без модели, проверяет цикл), Hermes, OpenCode — или человек, который делает шаги вручную.
- **Агент не ставит себе «принято».** Отметку «принято» ставит только независимый проверяющий; реальный исполнитель работает в изолированном окружении, где управляющие файлы доступны только для чтения.

## Как это выглядит на практике

```bash
# 1. Поставить и проверить окружение
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
factory doctor

# 2. Создать прогон и получить первое задание
factory init --run-id demo-01 --mode nano --idea "CLI todo без зависимостей"
factory next --run-id demo-01

# 3. Взять шаблоны, заполнить объявленные документы и сдать их
cp factory/templates/interview-brief.tmpl runs/demo-01/artifacts/interview-brief.md
cp factory/templates/GLOSSARY.tmpl runs/demo-01/artifacts/GLOSSARY.md
factory submit --run-id demo-01 --stage s00_intake \
  --role 01-smart-interviewer \
  --artifact artifacts/interview-brief.md \
  --artifact artifacts/GLOSSARY.md
factory verify --run-id demo-01 --stage s00_intake

# 4. Закрыть человеческие ворота
factory gate approve --run-id demo-01 --gate gate_s00 \
  --approver human:your-name --evidence "brief accepted"

# 5. Пройти остальные этапы: руками или исполнителем
factory run --run-id demo-01 --executor fake --max-steps 60
```

`factory next` печатает задание: этап, роль, какие документы сдать, кто проверяет и какой командой закрывается этап. Человек или исполнитель заполняет документы по шаблонам, сдаёт их, проверяющий выносит вердикт. `factory run` делает то же самое в цикле и останавливается на человеческих воротах с кодом `2`, печатая точную команду для человека.

Проверить эталонный прогон без модели:

```bash
FACTORY_ROOT="$PWD/examples/nano-todo" \
FACTORY_RUNS_ROOT="$PWD/examples" \
factory verify --run-id nano-todo --stage s9_learn --require-gate
```

Ожидаемо: `VERIFY OK`. В эталоне 33 события и 3/3 требований с тестами; продукт запускается.

## Чем это не является

- **Не «полностью автономная фабрика».** Решения по ТЗ, релизу и итогам принимает человек; он же проводит проверку восстановления перед работой с существующим проектом. Это сделано намеренно.
- **Нет встроенной модели.** Код не «думает»; роль исполняет подключённый исполнитель или человек.
- **Нет обещания «22 агента за минуту».** Один прогон требует интервью, ТЗ, архитектуры и проверки.
- **Нет трекера задач и облака.** Задачи живут в файлах прогона.

## Кому подходит

- небольшой команде или основателю, которые отдают разработку ИИ-агенту и хотят проверяемый результат;
- заказчику подряда, которому нужен отчёт «что и кем проверено», а не скриншот чата;
- тимлиду или аудитору, которому важен след решения, а не только итоговый код.

**Кому не подходит:** если нужен чат, который сам пишет код без участия человека; если это одноразовый прототип, который никто не будет принимать; если нет готовности писать ТЗ и архитектуру.

## Что внутри

- **22 ролевые карты** с восемью разделами: назначение, входы, процедура, выходы, Definition of Done, вето, запреты, эскалация.
- **13 стадий** от `s_in`/`s00_intake` до `s9_learn` с явными гейтами.
- **Исполнимый Python-движок**: `init`, `next`, `submit`, `gate`, `verify`, `task`, `quarantine`, `trace`, `metrics`, `doctor`.
- **Рантайм `factory run`**: задание → исполнитель → проверка → сдача → гейт; `pending_artifacts` и `next_role` в каждом задании, `usage.jsonl` с разбивкой токенов, evidence и стоп-гарды (подмена состояния, запись вне прогона, бюджет). Подключения: `fake` (без модели), `hermes`, `opencode`; реальные CLI запускаются в `bwrap` по умолчанию, управляющая плоскость read-only. `--parallel-tasks N` собирает независимые задачи `s4_build` в отдельных git worktree — см. [`docs/runtime.md`](docs/runtime.md).
- **Подписанные контрольные точки**: `factory sign` кладёт HMAC-SHA256 головы журнала в store вне `runs/`; `factory verify` отличает `stale` от `rolled_back` и `diverged`, то есть усечение журнала и пересчёт всей цепочки — см. [`docs/checkpoints.md`](docs/checkpoints.md).
- **Внешний eval-набор и сравнение прогонов**: `factory eval` гоняет фиксированные кейсы (`nano-todo`, `nano-product-mini`, `standard-mini`, `brownfield-proxy-mini`, `product-mini`), сверяет инварианты и числа с baseline и падает при росте шагов, падении событий или росте токенов больше чем на 10 %; `factory compare` показывает дельту стадий, стоимости и хешей артефактов двух прогонов — см. [`docs/eval.md`](docs/eval.md).
- **Журнал с хеш-цепочкой** SHA-256, блокировкой записи, state-якорем и семантической проверкой `PASS`.
- **Независимый верификатор**: `PASS` может поставить только `02-independent-verifier`; человек может принять риск отдельным событием.
- **Карантин скиллов**: адаптер `NVIDIA/SkillSpector 2.12.0` с fail-closed и локом хешей.
- **Шаблоны и схемы**: 38 шаблонов и 13 JSON-схем; шаблоны не ставят `PASS` заранее.
- **Эталонный прогон** `examples/nano-todo/`: 33 события, 2 задачи, 3 REQ, все гейты пройдены, продукт запускается.

Цифры: 4 режима (3 из них проверены на настоящем проекте), 5 эталонных примеров, 274 теста, CI на Ubuntu, macOS и Windows (Python 3.10–3.13).

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
| `factory next --run-id ID` | Выдать следующее задание в JSON |
| `factory run --run-id ID --executor fake\|hermes\|opencode` | Исполнить цикл: задание → исполнитель → проверка → сдача → гейт. Коды: `0` готово, `2` нужен человек, `3` эскалация |
| `factory submit --run-id ID --stage S --role R --artifact P` | Сдать артефакты продюсера |
| `factory verify --run-id ID [--stage S] [--require-gate]` | Детерминированная проверка |
| `factory sign --run-id ID --key-file KEY` | Подписать голову журнала HMAC-чекпоинтом вне `runs/` |
| `factory gate approve\|reject\|accept-risk --gate G --approver human:ID` | Решение гейта; `accept-risk` закрывает эскалацию с записью риска |
| `factory stage reset --run-id ID --stage S --approver human:ID --reason T` | Сбросить эскалированную стадию |
| `factory veto --run-id ID --stage S --role R --code C --evidence E` | Блокирующее вето роли |
| `factory task import\|list\|claim\|submit\|accept\|reject\|reset` | Задачи `s4_build` |
| `factory artifact re-record --run-id ID --path P --role R --reason T --evidence E` | Явно перезаписать хеш уже принятого артефакта: событие `ARTIFACT_RERECORD` с `old_sha256`, гейт не переоткрывается |
| `factory quarantine scan PATH --lock skills-lock.json` | Прогнать SkillSpector и записать лок |
| `factory quarantine verify-lock --lock skills-lock.json` | Обнаружить подмену скилла |
| `factory trace --run-id ID` | Матрица `REQ → task → test → acceptance` |
| `factory metrics --run-id ID [--write]` | Восемь метрик здоровья |
| `factory eval --cases eval/cases [--executor fake] [--json]` | Прогнать eval-кейсы, сверить инварианты и baseline; код `1` при регрессии |
| `factory eval --cases eval/cases --update-baseline eval/baseline.json` | Перезаписать эталон (только для зелёных кейсов) |
| `factory compare --left ID --right ID [--json]` | Дельта двух прогонов: стадии, шаги, токены, стоимость, хеши артефактов |
| `factory budget [--run-id ID]` | Лимиты попыток, режимный бюджет, израсходованные попытки |
| `factory artifacts` | Реестр артефактов |

## Журнал и доверие

`blackbox.jsonl` — единственный источник истины. Каждое событие содержит `event_id`, `seq`, `timestamp_utc`, стадию, задачу, роль, модель, `why`, `for_what`, `action`, хеши артефактов, вердикт, `prev_hash` и `hash`.

Гарантии:

- запись идёт под `fcntl.flock`, поэтому параллельные роли не ломают цепочку;
- `state.json` хранит последний хеш и число событий, поэтому удаление хвоста журнала обнаруживается;
- `submit`, `gate approve`, `task accept` и `artifact re-record` фиксируют SHA-256 артефактов, а `factory verify` перехеширует файлы на диске: подмена после сдачи или приёмки ловится как `artifact tampered`;
- `PASS` от продюсера отклоняется и при записи, и при проверке;
- `AUDIT_TRAIL.md` генерируется из JSONL и экранирует `|` и переводы строк;
- сама по себе хеш-цепочка **не подписана**: она защищает от случайной правки и наивной подмены, но не от злонамеренного пересчёта всей цепочки. Для сильной модели угроз есть `factory sign` — HMAC-контрольная точка вне `runs/`.

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
    loop.py              # задание → исполнитель → проверка → сдача → гейт
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
  schemas/               # 13 JSON-схем
  templates/             # 38 шаблонов
  pipeline.yaml          # граф стадий и режимы
  artifacts.yaml         # реестр артефактов
  models.yaml            # tier → runtime/env
  budget.yaml            # лимиты и поведение
tests/                   # инварианты, CLI, конкурентность, схемы, рантайм
examples/nano-todo/      # эталонный прогон
docs/                    # quickstart, runtime, checkpoints, eval, eval-plan, threat model, metrics, adapters
```

## Ограничения

- `models.yaml` намеренно не содержит вендорских ID. Тир разрешается рантаймом или переменными `FACTORY_MODEL_TIER_1/2/3`. Журнал записывает фактический `model_id`, который передал рантайм.
- Стоимость в `metrics` остаётся `null`: `cost_per_artifact_usd` считается по журналу, а журнал не хранит цены. Рантайм пишет токены и цену отдельно, в `runtime/usage.jsonl`.
- `Brownfield` поддерживает только git-репозитории. Для папки без git используйте `standard` и сделайте бэкап вручную.
- `s4_build` принимает задачи из `plan.md`; произвольный формат плана не парсится.
- Полный прогон требует модели или человека. `examples/nano-todo` — детерминированный reference run, а не доказательство автономности.
- `factory run` исполняет по одному шагу за вызов исполнителя и останавливается на человеческом гейте.
- Хеш-цепочка не подписана; подпись — отдельная команда `factory sign`, автоподписи в `factory run` нет.

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
