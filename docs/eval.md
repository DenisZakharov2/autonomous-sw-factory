# Внешний eval-набор

`factory verify` отвечает на вопрос «прогон цел и артефакты не подменены».
Он не отвечает на другой: стало ли лучше после правки рантайма. Eval-набор —
это фиксированные задачи с ожидаемыми инвариантами и метриками, которые
гоняются до и после изменения и дают сравнимые числа.

Набор не сравнивает текст: он зависит от модели. Сравниваются артефакты,
паритет `REQ`, задачи `s4_build`, закрытые гейты, запускаемые тесты продукта и
числа из журнала и `runtime/usage.jsonl`.

## Чем это не является

- `examples/nano-todo` — эталон одного цикла на `FakeExecutor`, а не набор
  регрессий: он не ловит деградацию по шагам, токенам и rework.
- Eval на `fake` доказывает движок и контракты, а не качество модели.
- Прогон на реальном Hermes остаётся ручным и бюджетируемым: в CI он дорог,
  медленен и недетерминирован.

## Состав кейса

```text
eval/cases/<case-id>/
  case.json       # id, mode, title, max_steps, budget, thresholds
  idea.md         # единственный источник истины о продукте
  constraints.md  # необязательные рамки кейса: REQ, задачи, роли стадии
  seed/           # необязательный seed: tar.gz + seed.json (существующее дерево)
  expected.json   # инварианты: артефакты, REQ, задачи, гейты, тесты продукта
  checks/         # необязательные pytest-проверки продукта
```

`case.json` валидируется схемой `eval-case.schema.json`;
`expected.json` — схемой `eval-expected.schema.json`. Идентификатор кейса
обязан совпадать с именем каталога и проходить тот же regex, что `run_id`.

Поля `case.json`:

| Поле | Смысл |
| :--- | :--- |
| `mode` | режим фабрики: `nano`, `standard`, `brownfield`, `enterprise` |
| `max_steps` | предел рабочих шагов прогона (по умолчанию 200) |
| `max_cycles` | сколько раз перезапускать цикл после человеческого гейта |
| `budget` | переопределение режимного бюджета (`tokens`, `minutes`, `usd`) |
| `thresholds` | порог регрессии для этого кейса |

`expected.json` фиксирует:

- `status` — ожидаемый финальный статус (`complete`);
- `artifacts` — id из `factory/artifacts.yaml`, которые обязаны существовать
  (каталоги — непустыми, `per_task` — по отчёту на каждую задачу);
- `requirements` — точный набор `REQ` в `spec.md` и `traceability.json`; каждый
  обязан быть покрыт задачей, тестом и строкой приёмки. «Покрыт тестом»
  означает: файл из `tests` существует внутри каталога прогона, каталог
  продуктовых тестов не пуст, и последний сегмент ссылки после `::` объявлен
  в этом файле — `def`/`async def`/`class` для Python, `fn` с `#[test]` или
  `#[tokio::test]` для Rust, слово для прочих расширений. Ссылка без `::`
  проверяется только на существование файла. Сегменты перед последним
  (`Class::method`, `tests::name`) не сопоставляются: для Rust достаточно
  наличия модуля, вложенность Python не требуется;
- `tasks.count` — число задач `s4_build`;
- `gates_closed` — гейты, которые обязаны быть `approved`;
- `submitters` — какие роли обязаны сдать артефакты стадии (проверяет, что
  мультиролевая стадия не схлопнулась в одного исполнителя);
- `product_tests` — команда тестов продукта, минимальное число тестов и
  таймаут; `{python}` подставляется текущим интерпретатором.

`checks/*.py` копируются в прогон и запускаются через `pytest` из каталога
прогона: проверка может читать артефакты относительными путями. Каталог
необязателен; без него кейс всё равно полноценный.

`constraints.md` — необязательный UTF-8 Markdown с рамками кейса. Харнесс
копирует его в каталог прогона, а `build_prompt` вкладывает текст секцией
«Ограничения кейса» (до 4000 символов) в каждый промпт исполнителя; заголовки
самого файла понижаются на уровень, чтобы заголовок секции остался
единственным `H1`. Без файла секции нет и пустых заголовков не появляется;
пустой файл равен отсутствию.
Рамки — число `REQ`, состав задач `s4_build`, роли, которые сдают
мультиролевую стадию, требования к тестам продукта — принадлежат кейсу, а не
режиму: промпт режима универсален для всех его кейсов. `standard-mini` и
`product-mini` задают так три `REQ` и две задачи, `nano-todo` и
`nano-product-mini` — свои три `REQ`, две задачи и одного автора `s1_spec`.
Конкретных `REQ`, имён команд и числа задач в промпте режима нет: иначе
`nano` умел бы только todo-кейс.

## Seed для существующего дерева

Greenfield-кейс начинает с пустого каталога, brownfield и продуктовые
кейсы — с существующего репозитория или крейта. `factory eval` не
подкладывает дерево сам, поэтому кейс несёт `seed/repo.tar.gz` (или
`seed/product.tar.gz`) и `seed/seed.json`:

- `seed.json` фиксирует `source`, `commit`, `archive`, `sha256` и
  `require_git`; схема — `eval-seed.schema.json`;
- `run_case` распаковывает архив в `run_dir` после `init_run` и до
  `_drive_run`; манифест сверяется по sha256, а после распаковки
  `git rev-parse HEAD` обязан совпасть с `commit`;
- `factory doctor` проверяет хеш, отсутствие path traversal, наличие
  `.git/HEAD` при `require_git: true` и отсутствие специальных файлов;
- распаковка отказывается писать поверх управляющей плоскости (`state.json`,
  `blackbox.*`, `idea.md`, `constraints.md`, `runtime/`, `checks/`);
- архив, а не каталог: вложенный `.git` нельзя закоммитить в репозиторий
  фабрики (git превращает его в gitlink), а restore drill нужен реальную
  историю.

Seed — это код продукта, а не артефакты фабрики. Brownfield-режим
переопределяет только пути к коду: `product_src` → `app/src-tauri/src`,
`product_tests` → `app/src-tauri/tests` (`artifact_paths` в `pipeline.yaml`);
всё остальное остаётся общим реестром. Тесты продукта запускаются из
`run_dir`, поэтому Rust-кейс указывает
`cargo test --lib --manifest-path app/src-tauri/Cargo.toml`.

Большой архив попадает в sdist (`recursive-include eval *`): это цена
самодостаточного кейса. Если он не нужен в пакете, seed держат вне кейса и
сверяют только коммит источника — но тогда `doctor` не может проверить
целостность дерева.

## Команды

```bash
factory eval --cases eval/cases --executor fake
factory eval --cases eval/cases --executor fake --json
factory eval --cases eval/cases --executor fake --case nano-todo
factory eval --cases eval/cases --update-baseline eval/baseline.json
```

`--case ID` (можно повторять) сужает набор: CI гоняет только быстрые
greenfield-кейсы, а brownfield-кейс с cargo-сборкой запускается отдельно.
`factory doctor` при этом валидирует все кейсы каталога.

`factory eval` прогоняет каждый кейс в отдельном `runs/<case-id>-<stamp>/`,
считает метрики и сверяет их с baseline. По умолчанию baseline —
`<cases>/../baseline.json`. Коды возврата: `0` — все кейсы прошли и регрессий
нет; `1` — кейс не выполнил инвариант или baseline регрессировал.

`--update-baseline` перезаписывает эталон **только** когда все кейсы зелёные:
записать baseline из падающего прогона нельзя.

```bash
factory compare --left nano-todo-20261030-120000 --right nano-todo-20261030-130000
factory compare --left run-a --right run-b --json
```

`factory compare` показывает дельту двух прогонов: шаги и события по стадиям,
токены, стоимость, секунды, задачи и хеши артефактов. Он отвечает, какая
стадия заплатила за изменение и что пересобралось содержательно, а что
совпало побайтно.

## Baseline и порог регрессии

Baseline — JSON по схеме `eval-baseline.schema.json`: по каждому кейсу
`status`, `steps` (рабочие шаги из `usage.jsonl`), `loop_steps`, `events`,
`rework_events`, `tasks`, `gates_closed`, `pass_at_1`, `tokens`,
`tokens_billable`, `usd`, `seconds`, `prompt_chars`, покрытие верификации и
трассируемости.

Правила по умолчанию:

- `steps` не растут;
- `events` не падают;
- `tokens_billable` и `usd` не растут больше чем на 10 %;
- `status` не меняется.

Неизвестные числа остаются `null` и не сравниваются: `fake` не тратит токены,
и превращать «неизвестно» в ноль нельзя. Порог можно переопределить в
`case.json` (`thresholds`).

## Человеческие гейты

Eval обязан пройти гейты, поэтому харнесс закрывает их сам от имени
`human:eval` и пишет это в журнал. Это не человеческое решение: перед
релизом гейты закрывает живой `human:<id>`. В отчёте аудитор увидит
`decided_by: human:eval` и не примет его за ревью.

Если задан `FACTORY_APPROVAL_KEY`, eval не сможет закрыть человеческий гейт:
харнесс не подделывает HMAC-токен. Это намеренная граница, а не дефект.

## Изоляция

На время кейса `factory eval` выставляет `FACTORY_ROOT` в каталог прогона,
поэтому root-артефакт `skills-lock.json` пишется внутрь `runs/<id>/`, а не в
корень репозитория. Тот же приём нужен, если вы проверяете такой прогон
вручную:

```bash
FACTORY_ROOT="$PWD/runs/nano-todo-20261030-120000" factory verify \
  --run-id nano-todo-20261030-120000 --require-gate
```

Реальные исполнители запускаются в `bwrap`, как и `factory run`; eval не
отключает песочницу. Для ручного прогона на Hermes задайте бюджет явно:

```bash
factory eval --cases eval/cases --executor hermes --budget-minutes 60
```

## Прогон на реальном Hermes

CI остаётся на `fake`. Реальные числа снимаются отдельной сессией и отдельным
эталоном `eval/baseline-hermes.json`; смешивать их с fake-эталоном нельзя —
шаги и токены другие, получится ложная регрессия.

```bash
export FACTORY_RUNS_ROOT="$PWD/runs"
factory eval --cases "$PWD/runs/hermes-cases" --executor hermes \
  --executor-model <model-id> --executor-tools auto --reasoning high \
  --timeout 900 --yolo --budget-tokens 3000000 --budget-minutes 120 \
  --update-baseline eval/baseline-hermes.json --json
```

`--cases` указывает на каталог с одним скопированным кейсом: `factory eval`
гоняет весь каталог, а два реальных кейса удваивают счёт. Песочница остаётся
включённой; `--sandbox-rw` — только install Hermes, `~/.hermes/cache` и
`~/.hermes/logs`.

Измерено 30.10.2026 на `nano-todo`, одна модель
`deepseek-ai/DeepSeek-V4.1-Flash-1017`, `--reasoning high`:

| Метрика | Значение |
| :--- | ---: |
| steps / loop_steps / events | 13 / 29 / 33 |
| rework_events | 0 |
| pass@1 | 1.0 |
| verification_coverage | 1.0 |
| tokens total | 1 780 308 |
| tokens billable (in+out) | 336 980 |
| api_calls | 85 |
| секунды исполнителя / wall | 606 / 629 |
| product tests | 14 passed |

`usd` в записи — `0.0`, потому что провайдер отдаёт `estimated_cost_usd: 0.0`
при `cost_status: unknown`. Это не измеренная цена; бюджет `--budget-usd` не
проверяется, а `cost_per_artifact_usd` остаётся `null`. Сравнивайте прогоны
по `tokens_billable`.

`total` в 5 раз больше billable: Hermes повторно считает кешированный префикс
на каждом API-вызове. Потолок `nano` в `pipeline.yaml` — **3.1M / 120 минут**
по худшему из двух nano-кейсов (см. ниже: 2 436 448 + 25 %); прежние 2.5M
закрывались на 97,5 %. Флаги `--budget-*` нужны, только если модель дороже
измеренной.

Измерено 31.10.2026 на `nano-product-mini` — тот же режим `nano`, но seed —
крейт `pfu` из `proxy-for-ubuntu` (`product/`, 252 КБ, без `.git`), задача в
`config.rs` и `router.rs`. 13 шагов, 33 события, 2 задачи, 2 436 448 total /
390 496 billable, 95 API-вызовов, 707 с исполнителя, 11,8 минуты, `pass@1`
1.0, 224 product tests (`cargo test --lib`; 218 до изменений, 6 новых).
`constraints.md` удержаны буквально: три `REQ`, ровно `TASK-001`/`TASK-002`,
`TASK-002` с `depends_on`, `s1_spec` сдала одна роль `01-smart-interviewer`
(в `nano` нет `05`/`06`). Запись — в `eval/baseline-hermes.json`.

Измерено 30.10.2026 на `standard-mini` (та же модель и конфигурация,
`constraints.md`: три `REQ`, две задачи, две роли на `s1_spec`): 15 шагов,
35 событий, 1 978 597 total / 363 237 billable, 97 API-вызовов, 653 с
исполнителя, 11,2 минуты, `pass@1` 1.0, 11 product tests. Второй замер —
`product-mini` на реальном крейте `pfu` (`proxy-for-ubuntu`): 15 шагов,
35 событий, 2 284 755 total / 430 803 billable, 97 API-вызовов, 737 с,
12,3 минуты, 228 product tests. Потолок `standard` в `pipeline.yaml` —
**2.9M / 120 минут** по худшему из двух: 2 284 755 + 25 %. Прежние 1.5M
остановили бы `standard-mini` на `s6_security` (1 553 552 после шага 12).

Измерено 30.10.2026 на `brownfield-proxy-mini` (seed — публичный
`proxy-for-ubuntu` 0.4.0, commit `dfe54336…`, Rust/Tauri; та же модель и
конфигурация): 17 шагов, 38 событий, 2 задачи, 3 560 791 total / 528 727
billable, 155 API-вызовов, 1033 с исполнителя, 17,2 минуты, `pass@1` 1.0,
`verification_coverage` и `traceability_coverage` 1.0, 222 product tests
(`cargo test --lib`). Restore drill восстановил 140 отслеживаемых файлов с
совпадением `HEAD`, `git status` и sha256. Потолок `brownfield` в
`pipeline.yaml` — **4.5M / 30 минут**: 3 560 791 + 25 % и 17,2 мин + 25 %.
Первый запуск с потолком 3M остановился на `s7_release` при 3 115 133 total;
это число — нижняя граница замера, полный прогон стоит 3.56M. Запись лежит в
`eval/baseline-hermes.json` рядом с `nano-todo`, `standard-mini`,
`product-mini` и `nano-product-mini` — пять записей, по одной на каждый
зелёный прогон реального исполнителя.

## CI

`doctor` и `pytest` идут на `ubuntu-latest`, `macos-latest` и
`windows-latest` (Python 3.10–3.13). Платформенные примитивы не удаляются:
`bwrap`-тесты пропускаются по его отсутствию, POSIX-mode-bit проверка
выполняется только на POSIX, shell-заглушки адаптеров — только на POSIX,
а лок журнала на Windows использует `msvcrt.locking` вместо `flock`.

Eval в CI работает на `fake`: без сети, без `bwrap` и укладывается в минуты.
Linux-джоб выполняет `factory eval --cases eval/cases --executor fake
--case nano-todo --case standard-mini`; brownfield-кейс требует seed и
cargo-сборки и запускается отдельно. Baseline лежит в репозитории, поэтому
рост шагов или удвоение токенов валит сборку.
