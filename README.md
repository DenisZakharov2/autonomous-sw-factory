# Autonomous Software Factory

[![CI](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DenisZakharov2/autonomous-sw-factory/actions/workflows/ci.yml) · [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English README](README.en.md) · [Лицензия](LICENSE) · [Пример прогона](examples/nano-todo/) · [Аудит](AUDIT.md) · [Быстрый старт](docs/quickstart.md)

---

**Версия 1.0.2.**

**Autonomous Software Factory делает разработку приложений и написание кода удобными, безопасными и прозрачными.** Это набор инструментов и правил, по которым работа идёт этапами: интервью, техническое задание, архитектура, сборка, приёмка, релиз, документация и выводы. На каждом этапе есть документ, который сдаётся и проверяется, и явное решение — продолжить или вернуть на доработку.

**Удобными** — потому что процесс ведёт человека, а не наоборот. Фабрика сама задаёт вопросы и подстраивает язык под уровень подготовки: новичку объясняет без жаргона, руководителю показывает варианты, ограничения и бюджет, разработчику — файлы, тесты и команды. Она проводит продукт с нуля «под ключ» — от интервью и ТЗ до работающего проверенного результата — или помогает команде закончить проект, на который уже нет сил, времени или ресурсов.

**Безопасными** — потому что автор не принимает свою работу: `PASS` ставит только независимый проверяющий. Реальный исполнитель работает в песочнице, где управляющие файлы доступны только для чтения, а перед изменениями в существующем проекте создаётся резервная копия и проверяется восстановление.

**Прозрачными и проверяемыми** — потому что каждый этап оставляет документ, каждое требование связано с задачей и тестом, а журнал хранит отпечатки шагов и файлов. Результат можно проверить, сравнить с эталоном и показать заказчику.

## Почему это удобно

1. **Сама задаёт вопросы.** На старте — не пустая форма, а адаптивное интервью: 1–3 вопроса за шаг, к каждому — рекомендованный вариант с обоснованием. Интервьюер определяет профиль собеседника и не вываливает длинную анкету.
2. **Подстраивается под уровень.** Три профиля — `USER_NON_TECH`, `TECH_MANAGER`, `DEVELOPER` — фиксируются в `state.json`. Для новичка карточка роли прямо запрещает жаргон; руководитель видит варианты, ограничения и бюджет режима; разработчик — файлы, шаблоны и команды. Один процесс, разный язык.
3. **С нуля — под ключ.** Режимы `nano`, `standard` и `enterprise` проводят продукт от интервью и ТЗ через архитектуру, сборку, приёмку, релиз и документацию к выводам. `factory next` выдаёт задание, шаблоны и точную команду закрытия этапа. «Под ключ» здесь — работающий и проверенный продукт в каталоге прогона, а не хостинг, деплой и сопровождение.
4. **Помогает закончить застрявший проект.** Режим `brownfield` для существующего git-репозитория: сначала `pre-factory.bundle`, архив рабочего дерева, `manifest.sha256` и проверенный restore drill, потом read-only аудит и точечные задачи. Не нужно переписывать всё заново и не нужно начинать с чистого листа.
5. **Не привязывает к инструменту и не требует промпт-инженерии.** Исполнитель на выбор: Hermes, OpenCode, `fake` без модели или человек. Ядро не вызывает вендорскую модель: `factory next` отдаёт JSON-задание, а роли, стадии и шаблоны уже описаны. Пользователю не нужно проектировать процесс самому.

Два разных читателя — два разных обещания: «под ключ» — для того, у кого есть идея и нет процесса; «закончить проект» — для того, у кого есть код и нет ресурсов его довести.

## Почему это безопасно

1. **Автор не принимает свою работу.** `PASS` ставит только `02-independent-verifier`; продюсер не может записать его ни при сдаче, ни при проверке. Перед каждым role-гейтом и каждым `TASK_DONE` эта роль действительно исполняется: пишет `verification.md` с тремя adversarial-проверками (команда и её фактический вывод) и одним вердиктом, а гейт читает этот вердикт — без `VERIFY PASS` в журнале `factory verify` закрытие не пройдёт. Человек может принять риск отдельной записью `accept-risk` — с причиной, сроком и ответственным.
2. **Изоляция исполнения.** Реальный исполнитель запускается в `bwrap`: корень файловой системы read-only, управляющая плоскость (`state.json`, журнал, `usage.jsonl`, evidence) смонтирована только для чтения, запись открыта только в объявленные заданием каталоги. Tripwire хеширует состояние и останавливает прогон при подмене. Это файловая граница, а не отдельный Unix-пользователь и не сетевая изоляция — ограничение названо честно.
3. **Бэкап и restore drill перед brownfield.** До первой правки существующего кода создаются `pre-factory.bundle`, архив рабочего дерева и `manifest.sha256`, а восстановление проверяется в отдельном клоне: `diff` статуса и `sha256sum -c`. Без `PASS` в `restore-drill.md` гейт `s_in` не закрывается.
4. **Журнал и защита от подмены.** `blackbox.jsonl` — хеш-цепочка SHA-256 с блокировкой записи и якорем в `state.json`; `factory verify` перехеширует файлы и ловит `artifact tampered`. `factory sign` кладёт HMAC-контрольную точку вне `runs/` и отличает `stale` от `rolled_back` и `diverged`. Легальная правка принятого артефакта — только `factory artifact re-record` с причиной и evidence.
5. **Лимиты, остановки и карантин.** У режима есть бюджеты по токенам, минутам и USD, у стадии — максимум попыток, у вето — лимит повторов (`max_veto_retries: 3`). При исчерпании — остановка и эскалация человеку, а не бесконечный цикл. Внешние скиллы проходят карантин SkillSpector с фиксацией хешей в `skills-lock.json`.

## Почему это прозрачно и проверяемо

1. **Каждый этап — документ и ворота.** Не «чат, в котором что-то написали», а сдаваемый артефакт по шаблону, проверка и явное решение гейта. `factory next` заранее говорит, что сдать и какой командой закрыть этап.
2. **Трассировка требований.** `factory trace` показывает цепочку «требование → задача → тест → приёмка» и называет разрывы. Ссылка на несуществующий тест — тоже разрыв; с 1.0.1 проверяется и имя функции после `::`.
3. **Отчёт для заказчика.** `AUDIT_TRAIL.md` собирается из журнала: кто, когда, что сдал, какой вердикт и как запись связана с предыдущей. Сводка — `factory status`, проверка — `factory verify`.
4. **Повторяемость и сравнение.** `factory eval` прогоняет эталонные кейсы на `fake` или реальном исполнителе и сверяет шаги, события и токены с baseline. `factory compare` показывает дельту двух прогонов, включая хеши артефактов.
5. **Расход виден.** `factory usage` показывает `steps`, `api_calls`, `prompt_chars`, `tokens`, `tokens_billable`, `cache_read`, `tokens_out` и `seconds` по стадиям, задачам или ролям. `tokens` — полный контекст с кешем, `tokens_billable` — только вход и выход. USD при `0.0` или `null` — не цена, а «не измерено».

Всё это лежит файлами в `runs/<id>/`: прогон можно заархивировать, передать и проверить позже — не только посмотреть в чате или облаке.

## Что Фабрика не обещает

- **Не «полностью автономная фабрика».** Ключевые решения — ТЗ, релиз, итоги — принимает человек. Это сделано намеренно.
- **Нет встроенной модели.** Код не «думает»; роль исполняет подключённый исполнитель или человек.
- **Нет обещания «22 агента за минуту».** Один прогон требует интервью, ТЗ, архитектуры и проверки.
- **Нет трекера задач, облака и деплоя.** Задачи и продукт живут в файлах прогона; хостинг и сопровождение — не эта задача.
- **Нет абсолютной безопасности.** Песочница — файловая граница; тот же uid и сеть остаются. Это ограничение лучше назвать, чем скрыть.
- **USD не измеряется.** `0.0` или `null` в `usage.jsonl` — не «бесплатно», а «провайдер не отдал цену». Сравнивайте `tokens_billable`.

## Кому подходит

- основателю или небольшой команде, у которой есть идея, но нет времени и сил собирать процесс разработки;
- команде, у которой проект уже есть, но нет ресурсов его закончить;
- заказчику, которому нужен отчёт «что и кем проверено», а не скриншот чата;
- тимлиду или аудитору, которому важен след решения;
- новичку, которому нужны вопросы, объяснения без жаргона и готовые шаблоны.

**Кому не подходит:** если нужен чат, который пишет код без человека; если это одноразовый прототип, который никто не будет принимать; если нет готовности писать ТЗ и архитектуру.

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

Ожидаемо: `VERIFY OK`. В эталоне 49 событий и 3/3 требований с тестами; продукт запускается.

## Что внутри

- **22 ролевые карты** с восемью разделами: назначение, входы, процедура, выходы, Definition of Done, вето, запреты, эскалация.
- **13 стадий** от `s_in`/`s00_intake` до `s9_learn` с явными гейтами.
- **Исполнимый Python-движок**: `init`, `next`, `submit`, `gate`, `verify`, `task`, `quarantine`, `trace`, `metrics`, `usage`, `doctor`.
- **Рантайм `factory run`**: задание → исполнитель → проверка → сдача → гейт; `pending_artifacts` и `next_role` в каждом задании, `usage.jsonl` с разбивкой токенов, evidence и стоп-гарды (подмена состояния, запись вне прогона, бюджет). Подключения: `fake` (без модели), `hermes`, `opencode`; реальные CLI запускаются в `bwrap` по умолчанию, управляющая плоскость read-only. `--parallel-tasks N` собирает независимые задачи `s4_build` в отдельных git worktree — см. [`docs/runtime.md`](docs/runtime.md).
- **Подписанные контрольные точки**: `factory sign` кладёт HMAC-SHA256 головы журнала в store вне `runs/`; `factory verify` отличает `stale` от `rolled_back` и `diverged`, то есть усечение журнала и пересчёт всей цепочки — см. [`docs/checkpoints.md`](docs/checkpoints.md).
- **Внешний eval-набор и сравнение прогонов**: `factory eval` гоняет фиксированные кейсы (`nano-todo`, `nano-product-mini`, `standard-mini`, `brownfield-proxy-mini`, `product-mini`), сверяет инварианты и числа с baseline и падает при росте шагов, падении событий или росте токенов больше чем на 10 %; `factory compare` показывает дельту стадий, стоимости и хешей артефактов двух прогонов — см. [`docs/eval.md`](docs/eval.md).
- **Журнал с хеш-цепочкой** SHA-256, блокировкой записи, state-якорем и семантической проверкой `PASS`.
- **Независимый верификатор**: `PASS` может поставить только `02-independent-verifier`; человек может принять риск отдельным событием.
- **Карантин скиллов**: адаптер `NVIDIA/SkillSpector 2.12.0` с fail-closed и локом хешей.
- **Шаблоны и схемы**: 38 шаблонов и 13 JSON-схем; шаблоны не ставят `PASS` заранее.
- **Эталонный прогон** `examples/nano-todo/`: 49 событий, 2 задачи, 3 REQ, все гейты пройдены, продукт запускается.

Цифры: 4 режима (3 из них проверены на настоящем проекте), 5 эталонных примеров, 282 теста, CI на Ubuntu, macOS и Windows (Python 3.10–3.13).

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
| `factory usage --run-id ID [--by stage\|task\|role] [--json]` | Расход из `runtime/usage.jsonl` по стадиям, задачам или ролям; `null` не превращается в ноль |
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
