# Handoff

## M4: Контекстный лимит роли и самоповтор верификатора (1.0.9)

Состояние на 5 ноября 2026. Ветка `main`, `VERSION` 1.0.9, коммит `3021c75` (HEAD, push в origin main сделан). Тег `v1.0.9` на origen указывает на `3021c75`, GitHub Release 1.0.9 опубликован.

Что появилось в 1.0.9:
- **Жёсткий лимит контекста роли** (`context_exceeded` вместо молчаливого обрезания промпта) — в `factory/runtime/loop.py`, `prompt.py`, `worktree.py`; тест в `tests/test_v012_regressions.py`.
- **Регрессия `gate_reject` при self-rework** — при `rework_to == stage_id` возвращает продюсеру `produce` с `feedback` верификатора вместо эскалации (`tests/test_v012_regressions.py`).
- `factory doctor` → OK, целевые pytest: 79 passed, 2 failed (пред-existing в `test_eval.py`, не связаны с релизом).

Что осталось:
- Все пункты из раздела «Что осталось» закрыты: тег `v1.0.9` запушен и GitHub Release 1.0.9 создан, отдельный пользователь исполнителя (`factory setup-executor`) проверен, подпись журнала (`factory sign`/`factory verify`) опробована на реальном прогоне `enterprise-mini-20261104-191607`.

---

## M3: UX-дашборд (1.0.8)

Состояние на 3 ноября 2026. Ветка `main`, `VERSION` 1.0.8 (тег `v1.0.8`). Релиз 1.0.8: тема оформления дашборда, управление с клавиатуры, виртуализация журнала, фикс фильтров журнала и time-series.

### Что появилось

- `factory/ui/static/index.html` — кнопка темы в шапке (тёмная/светлая/системная, выбор запоминается, без мигания при загрузке); горячие клавиши `j`/`k`/`Enter`/`/`/`?`/`gg`/`G`/`r`; журнал с виртуализацией — SSE-догрузка, видно только видимое окно, текстовый фильтр ищет по всему журналу.
- Исправлены фильтры журнала и переключатель бакета в time-series (падали на несуществующем `loadRun()`).
- Удалены входы генератора скриншотов (`docs/screenshots/*.html`, `api-data.json`); в дереве остались только отслеживаемые `.png`.
- `pytest test_docs.py test_ui.py` — 93 passed; `factory doctor` — OK; `factory --version` → `factory 1.0.8`.
- Полный `pytest -q` — **400 passed, 1 flaky** (`test_doctor_ignores_a_foreign_project_version` падает только в связке с остальными, Alone и в файле регрессий — зелёный; не связан с бампом версии).

### Решения

- docs/screenshots/*.html и api-data.json — удалены как мусор генератора скриншотов, не продукт.
- Тег v1.0.7 не откатывался; решение пользователя остаётся в силе.

### Что осталось

- Обновить скриншоты `.png` под новые фичи (кнопка темы в шапке).
- Проверить CI на релизном коммите (все 14 jobs).

---

## M2: сравнение прогонов (1.0.5)

Состояние на 2 ноября 2026. Ветка `main`, `VERSION` 1.0.5. Релиз 1.0.5 собирает
коммит `a9b5e8f` поверх `v1.0.4`: фильтры журнала с состоянием в URL, честный счётчик,
поиск/сортировка списка прогонов, drawer деталей с deep link, новый эндпоинт события
и экспорт журнала с фильтрами.

### Что появилось

- `factory/ui/static/index.html` — панель `p8` «Метрики» (девять значений,
  `null` → «не измерено», ошибка → warnbox, до трёх пар отказов), пресет
  «Аудит» = `p2,p6,p8,p7`, в «Операторе» и «FinOps» панель скрыта; таблица
  списка прогонов в `.runs-scroll`; `body{min-width:min(1280px,100%)}`.
- `factory/ui/data.py` не менялся: метрики по-прежнему приходят из
  `compute_metrics` в `run_payload`, второй эндпоинт и второй кэш не заведены.
- `factory/cli.py` — `cmd_ui` передаёт `args.runs_dir`, а не `_runs_root(args)`.
- `tests/test_ui.py` — четыре теста: девять значений и честные `null`,
  каталог/пресет p8, контейнер прокрутки, проброс `--runs-dir`.
- `README.md`/`README.en.md` — блок «Посмотреть прогон» / «See a run» в начале,
  счёт панелей 8, честный абзац о `compute_metrics`, 357 тестов.
- GitHub About — новое описание с дашбордом, topic `dashboard`.

### Замеры (1 ноября 2026, `runs/nano-todo-verify-role-real`, 117 событий)

| Что | До | После |
|---|---|---|
| `documentElement.scrollWidth` на списке при 1280 | 1408 | 1270 = `clientWidth`; со скрытыми скроллбарами 1280/1280 |
| таблица / контейнер списка | 1384 / 1232 | 1384 / 1222, прокрутка внутри |
| `compute_metrics` | 338–359 мс | 315–346 мс (код не менялся) |
| холодный payload | 685–807 мс | 680–748 мс |
| `index.html` | 62 838 Б | 65 414 Б, внешних URL нет |

`pytest` — 357 passed за 493.55 с (было 353); `factory doctor` — OK;
`factory verify`, `trace`, `metrics`, `log verify` на `examples/nano-todo` — OK.

### Решения

- Метрики остаются в `data.run_payload`; панель рендерит уже полученный payload,
  без нового эндпоинта и кэша.
- `null` — «не измерено», не ноль и не «—»; пустой список отказов — «нет отказов».
- p8 по умолчанию `[6,3]`; компактные стили панели умещают девять плиток без
  прокрутки тела. В пресетах «Оператор» и «FinOps» панель в каталоге, но скрыта.
- Новая панель для старой раскладки в `localStorage` добавляется скрытой;
  пресет «Аудит» показывает её на `[6,3]`.
- `qualityLine` в «Трассировке» оставлен как краткая выжимка; дублирование
  осознанное.
- `body` больше не требует 1280 px шире окна: окна уже 1280 сжимаются, мобильной
  вёрстки по-прежнему нет.

### Что осталось

- M2: SSE, сравнение прогонов, произвольные панели с запросом.
- Серверный фильтр журнала (`?verdict=`, `?event_type=`) и экспорт текущего
  фильтра; ширины колонок в книге `.xlsx`, строка ИТОГО в `usage.csv`,
  `stages.csv` эндпоинтом; стриминг CSV и кэш книги по ревизии.
- `.xls` (BIFF8), PDF, мобильная вёрстка и светлая тема не делаются.

## M1: экспорт CSV и книги .xlsx (1.0.3)

Состояние на 1 ноября 2026. Ветка `main`, `VERSION` 1.0.3. Релиз 1.0.3 собирает
три коммита поверх `v1.0.2`: дашборд `factory ui` (M0), возврат отказа
верификатора продюсеру и экспорт M1.

### Что появилось

- `factory/ui/export.py` — чистые функции `csv_bytes`, `xlsx_bytes` и таблицы
  `runs/journal/tasks/usage/trace/stages/run`. Строки берутся из `data.list_runs`,
  `data.run_payload` и `journal.read_events`; своего агрегатора нет.
- `factory/ui/server.py` — четыре ветки роутинга, `Content-Disposition` и `501`
  без openpyxl.
- `factory/ui/static/index.html` — меню «Экспорт ▾» (иконка 28 px) в шапке,
  кнопка `CSV` в шапке списка прогонов, пункт `.xlsx`.
- `pyproject.toml` — extra `xlsx = ["openpyxl>=3.1"]`, openpyxl в `dev`.
- `tests/test_ui.py` — 12 тестов (A12): BOM/`;`/CRLF, имена файлов, полный
  журнал против хвоста 50, сверка `usage.csv` с `build_usage_report`, склейка
  списков, шесть листов книги, `501` без зависимости, байт-в-байт неизменность
  прогона, одно меню в шапке.

### Замеры (1 ноября 2026, `runs/nano-todo-verify-role-real`, 117 событий)

| Что | Размер | Время (тёплый сервер) |
|---|---|---|
| `journal.csv`, 117 строк | 29 288 Б | 11–12 мс |
| `tasks.csv`, 2 строки | 449 Б | 11 мс |
| `usage.csv`, 8 стадий | 629 Б | 9 мс |
| `trace.csv`, 3 REQ | 431 Б | 4 мс |
| `runs.csv`, 87 прогонов | 15 263 Б | 36 мс |
| `<run_id>.xlsx`, 6 листов | 22 386 Б | 105–130 мс |

100 000 событий (синтетический журнал 45 МБ): таблица 3.18 с, CSV 3.33 с /
25.7 МиБ, книга 52.6 с / 4.1 МиБ, пик RSS 595 МиБ. Книга на сотне тысяч строк
дорогая — это openpyxl, не наш код; CSV остаётся дешёвым путём.

### Решения

- Разделитель `;`, CRLF, UTF-8 с BOM: Excel в русской локали иначе показывает
  кириллицу мусором. RFC 4180 с запятой отвергнут осознанно.
- `null` — пустая ячейка (и в CSV, и в книге), `0` остаётся `0`.
- Журнал экспортируется целиком; `?limit=N` — единственный способ укоротить.
- Имя файла `<run_id>-<dataset>-<YYYYMMDD>.csv`; у книги — `<run_id>-<дата>.xlsx`.
- `.xlsx` без openpyxl — `501` с текстом `pip install autonomous-sw-factory[xlsx]`,
  импорт ленивый: CI-джоба `build` ставит sdist с голым pytest.
- Шапка: бренд 280 px, стадия 300 px, кнопка-иконка 28 px. Замер на 1280 px —
  занято 1260 px, `scrollWidth == clientWidth`, переноса нет. Текстовая кнопка
  тоже влезла бы (brand сжался бы до 255), так что иконка — решение, а не
  необходимость.

### Что осталось

- Страница списка прогонов была шире 1280 px (таблица 1384 px) — исправлено в 1.0.4.
- `.xls` (BIFF8) не делаем, PDF не нужен, `factory export` как CLI не нужен.
- M2: произвольные панели с запросом, SSE, сравнение прогонов.

## Отказ верификатора доходит до продюсера, а имена тестов — до сборки

Состояние на 1 ноября 2026. Ветка `main`, база — `0c45b25` (`VERSION` 1.0.2,
тег `v1.0.2`), правки этой сессии вошли в 1.0.3 (коммит `ac49cca`): `factory/engine.py`,
`factory/artifacts.py`, `factory/runtime/prompt.py`,
`factory/schemas/work-order.schema.json`, `tests/test_runtime_verifier.py`,
`CHANGELOG.md`, `handoff.md`. Тестов 317 (было 311): 6 новых в
`tests/test_runtime_verifier.py`, ни один существующий не ослаблен.

### A. Почему отказ верификатора не доходил до продюсера

Разрывов было два, и второй нашёлся только потому, что fake-прогон
`verification-fail` после первой правки начал эскалироваться.

1. **`rework_to == stage_id` не переоткрывал задачи.** `gate_reject` вызывал
   `_reset_rework_chain` только при `rework_to != stage_id`; у `s4_build`
   `rework_to` — он сам, поэтому задачи оставались `accepted`, заказ уходил
   верификатору, тот проверял те же файлы, и после `max_attempts` стадия
   эскалировалась. На `runs/standard-mini-verify-role-b2` это события 34–42:
   три `VERIFY FAIL` подряд и `s4_build escalated`.
2. **Гейт читал отчёт, написанный до последней сдачи продюсера.** После
   `submit_work` стадия уходит в `review`, а `validate_stage` проверяет только
   грамматику отчёта, не его давность: гейт видел старый `FAIL` и отвергал
   стадию, не спрашивая верификатора заново. Это второй способ получить два
   одинаковых вердикта на одни и те же байты.

Правки:

- `_reopen_stage_tasks(state, stage_id)` — `accepted`/`review`/`in_progress` →
  `pending`, lease снимается; вызывается из `gate_reject` при self-rework и из
  `_reset_rework_chain` (`s5_acceptance`/`s6_security` → `s4_build`, эта ветка
  не изменилась).
- `_report_is_current(run_dir, stage_id, task_id, report_path)` — отчёт
  «свежий», если после его сдачи не было сдачи продюсера. Для стадии сдачей
  продюсера считается и задача: отчёт стадии судит всё дерево `product/`.
  Одно правило на обе ветки `_stage_work_order`: гейт и верификатор получают
  шаг только при валидном и свежем отчёте, а `FAIL`, на который продюсер ещё
  не ответил, — это rework, а не повторная проверка.
- Причина отказа: `VERIFY` пишет поле `reason` (только для `FAIL`, поэтому
  `PASS`-события остаются байт-в-байт такими же, как в 1.0.2); `_feedback`
  читает последний `VERIFY FAIL` по задаче и по стадии прямо из журнала (это
  переживает перезапуск, в отличие от памяти цикла); заказ несёт `feedback`;
  `build_prompt` печатает его в разделе «Замечания предыдущей проверки».
  Причина на реальном прогоне — цитата из `runs/standard-mini-verify-role-b2`:
  «критерий выхода стадии «tests pass in a clean checkout» не выполняется…»
  (полный текст — в `artifacts/verification/s4_build/verification.md`); теперь
  он доезжает до заказа, а не только до `GATE_DECISION.evidence`.
- Заодно закрыта дыра: заказ задачи отдавал продюсеру `verification` и
  `task_verification` в `artifacts`, то есть рантайм открывал продюсеру запись
  в каталог отчёта верификатора. Теперь отчёт есть только в заказе
  верификатора — и в `artifacts`, и в `pending_artifacts`.

### B. Имена тестов

Гипотеза подтверждена кодом и данными: `traceability.json` пишется на
`s1_spec` и замораживается, а `_task_work_order`/`build_prompt` имена тестов
продюсеру не передавали. На `runs/nano-todo-verify-role-b2` объявлены
`test_req_003_done_marks_task_completed` и `…_unknown_id_and_corrupt_file`, а
написаны `…_marks_only_target_task` и `…_unknown_id_leaves_file_unchanged`;
REQ-001 и REQ-002 при этом совпали — поэтому кейс зелёный по всем проверкам и
красный по покрытию.

- Заказ продюсера несёт `declared_tests` — ссылки `path::symbol` для `REQ`
  этой задачи; `build_prompt` печатает раздел «Тесты, объявленные в
  `traceability.json`» с требованием объявить ровно эти имена. Верификатору
  поле не отдаётся: это контракт сборки, а не чек-лист проверки.
- `s3_plan` проверяет исполнимость: файл объявленного теста обязан входить в
  `Allowed Files` задачи, несущей `REQ`. На `runs/nano-todo-verify-role-b2`
  план отдал `product/tests/test_todo.py` только `TASK-002`, а тесты
  REQ-001/REQ-002 объявлены за `TASK-001` — такое имя объявить физически
  некому.
- Проверено на исторических данных: пять baseline-прогонов и
  `examples/nano-todo` проходят `validate_plan` под новым правилом; краснеют
  только прогоны 1.0.2 (`nano-todo-verify-role-b2`,
  `standard-mini-verify-role-b2`) — правило бьёт точно по дефекту.
- `build_matrix` не ослаблен. Отдельного дефекта для Rust нет: проба на файле
  с `#[cfg(test)] mod tests` и `#[test]`/`#[tokio::test]` резолвится; в
  `runs/nano-product-mini-verify-role-b2` из восьми объявленных имён два
  находятся в `product/src/config.rs` и `product/src/engine/router.rs`, а
  шесть — нет, потому что продюсер объявил свои. Это тот же разрыв B, а не
  поиск тестов.

### Реальные данные

- `runs/standard-mini-verify-role-b2`, события 34–42: три `VERIFY FAIL` и
  `s4_build escalated` — исходный дефект.
- Replay того же журнала на исправленном движке: копия прогона, журнал обрезан
  до события 35 (первый `VERIFY FAIL`), `state.json` откачен к значениям,
  которые следуют из событий (`s4_build` — `review`, гейт `gate_s4` —
  `pending`, attempts 0). `gate_reject` на этом состоянии переоткрывает обе
  задачи (`accepted` → `pending`), ставит стадию в `in_progress` и attempts 1,
  а `factory next` отдаёт `produce TASK-001` для `12-fullstack-developer`:
  в `feedback` — дословная причина верификатора из реального журнала, в
  `declared_tests` — два имени. Новых шагов модели не делалось: это
  проверка на записанном журнале, а не новый прогон.
- `_feedback` читает причину и из событий 1.0.2, где поля `reason` ещё нет —
  разбирает хвост `…; reason: …` в `verdict.evidence` (на
  `standard-mini-verify-role-b2` это проверено).

### Реальный прогон nano-todo (1 ноября 2026)

`runs/nano-todo-20261101-083132`, `factory eval --executor hermes --case
nano-todo`: `complete`, 22 шага, 48 событий, 3 953 296 токенов (671 120
billable), 31.6 мин, `EVAL OK`. В 1.0.2 тот же кейс: 23 шага, 48 событий,
3 925 927 (753 063) — цена не выросла.

- `_requirement_errors` — `[]`, `factory trace` — 3/3 (100%): продюсер объявил
  дословно `test_req_001_add_writes_task`,
  `test_req_002_list_prints_tasks`, `test_req_003_done_marks_task`. Раздел
  «Тесты, объявленные в `traceability.json`» есть в промпте TASK-001
  (`runtime/ev-0021-…prompt.md`); продукт руками не правился.
- `factory verify --stage s9_learn --require-gate` — OK, `factory log verify` —
  CHAIN OK, 11 тестов продукта зелёные.
- `s3_plan` прошёл новый gate с первой попытки: план отдал
  `test_add.py`/`test_list.py` задаче `TASK-001`, `test_done.py`/`test_errors.py`
  — `TASK-002`.
- Ни одного `VERIFY FAIL` в этом прогоне не было, поэтому путь «FAIL → шаг
  продюсера» здесь не наблюдался; он подтверждён replay стандартного журнала
  (выше) и fake-сценарием `verification-fail` — в нём после каждого `FAIL`
  идёт шаг продюсера, и прогон сходится без эскалации (120 событий).
- Полный прогон `standard-mini` на реальной модели в этой сессии **не
  делался**: проверка A на реальном журнале сделана replay'ем, а не новым
  прогоном.

## Независимый верификатор как шаг рантайма (1.0.2)

Состояние на 1 ноября 2026. Ветка `main`, `VERSION` — `1.0.2`, тег `v1.0.2`
на релизном коммите. Вариант A: роль
`02-independent-verifier` действительно исполняется перед каждым из семи
role-гейтов и перед каждым `TASK_DONE`. Тестов 311 (было 305 после `7464df8`,
282 до верификатора): 6 новых в `tests/test_runtime_verifier.py`, остальные —
правки существующих под новую семантику, ни один не ослаблен.

### Что изменилось

- **Реестр.** `verification` (`artifacts/verification/{stage}/verification.md`)
  и `task_verification` (`tasks/{task_id}/verification/verification.md`),
  владелец `02-independent-verifier`, шаблон `templates/verification.tmpl`,
  обязательные разделы `## 1. Проверено`, `## 2. Проверки`, `## 3. Вердикт`.
  Добавлены семи role-гейтовым стадиям и `task_verification` — в `s4_build`.
  `{stage}` подставляется в `artifact_path`, `resolve_path`,
  `_declared_artifact_base`, `validate_artifact`/`validate_stage` и в
  `resolved_path` из `stage_artifacts`.
- **Work order.** Для стадии, у которой продюсеры всё сдали и валидны, а
  отчёта нет, `factory next` отдаёт `action: verify`,
  `roles: ["02-independent-verifier"]`, `next_role: 02-independent-verifier`,
  а в `artifacts` — только спецификацию отчёта. Для задачи в `review` — то же
  самое с `task_verification`. Заказ продюсера, наоборот, больше не содержит
  спецификации отчёта: продюсер не получает его на запись.
- **Рантайм.** `_complete_verification` запускает исполнителя, проверяет отчёт
  и: `PASS` — сдаёт отчёт (стадия уходит в `review`) либо закрывает задачу;
  `FAIL` — `gate_reject`/`task_reject` с причиной из отчёта. `action: gate`
  у role-approver читает вердикт и не закрывает гейт при `FAIL`.
- **Отчёт.** `validate_verification_report` требует минимум три блока
  `### Проверка N`, в каждом непустые `- Команда:` и `- Вывод:`, и ровно один
  вердикт. Выбор `PASS | FAIL`, оставшийся от шаблона, — невалидный отчёт.
- **Журнал.** `VERIFY` в `EVENT_TYPES`, в enum `event.schema.json` и в
  `ARTIFACT_RECORDS`; писать его может только `02`, вердикт только `PASS` или
  `FAIL`, evidence и артефакт отчёта обязательны. `factory verify` проверяет
  порядок (`verification_policy_errors`) и перехеширует отчёт.
- **Совместимость.** Новый прогон получает `verification_policy: "role"`.
  Прогоны без поля не переписываются: артефакт отчёта для них не существует
  (`requires_policy`), `validate_stage` и `task_accept` работают по-старому.
  Проверено на реальных данных: `examples/nano-todo` и все пять исторических
  baseline (`nano-todo-20261030-191409`, `standard-mini-standard-real`,
  `brownfield-proxy-mini-brownfield-real2`, `product-mini-product-real`,
  `nano-product-mini-nano-product-real`) проходят `factory verify`.

### Замкнутые требования: обещание стало проверкой

Три требования из прошлого захода жили только в тексте промпта. Теперь они
проверяются кодом, и у каждого есть тест.

- **Детерминированная проверка в отчёте стадии.** Было: абзац промпта «одна из
  проверок — `factory verify`» и ничего в валидаторе. Замер по девяти реальным
  отчётам `runs/nano-todo-verify-role-clean`: поле `- Команда:` содержит
  `factory verify` в **7 из 9** — во всех семи стадийных отчётах (7/7) и ни в
  одном из двух задач (0/2). Поэтому правило введено для отчёта стадии:
  `validate_verification_report(..., require_deterministic_check=True)` требует
  `\bverify\b` хотя бы в одном `- Команда:`; отчёт задачи валидируется с
  `require_deterministic_check=False`, потому что `factory verify` не имеет
  `--task`, а детерминированное доказательство задачи — команда из `plan.md`.
  После изменения валидны **9/9** реальных отчётов (проверено скриптом на
  копии, не на глаз) и 9/9 отчётов `examples/nano-todo`; отчёт стадии без
  такой проверки — невалиден (`test_a_stage_report_without_the_deterministic_check_is_not_a_report`).
- **Матрица приёмки: дословный `PASS`.** `build_prompt` для `s5_acceptance`
  печатает блок «Правила матрицы приёмки»: в колонке `Статус` — дословно
  `PASS`; «пройдено тестом», `OK`, `DONE`, `PENDING` отклоняются. Шаблон
  `templates/acceptance.tmpl` по-прежнему `PENDING`, предзаполнять `PASS`
  запрещено — тот же принцип, что у отчёта верификатора.
- **Вердикт — одно слово.** Задание верификатора (и стадийное, и задачное)
  говорит: в строке `- Вердикт:` — ровно одно слово, `PASS` или `FAIL`;
  шаблонный выбор `PASS | FAIL` невалиден и шаг повторится. `verification_verdict`
  не менялся. Заодно из задания задачи убрано обещание `factory verify` —
  у команды нет области задачи, и реальные отчёты её не запускали.
- **Evidence для невалидного отчёта.** `_complete_verification` теперь
  возвращает `(note, {"errors": [...], "report": "<относительный путь>"})`, и
  цикл пишет шаг `runtime/evidence/...{"status": "report_invalid", ...}`.
  Прогон продолжается повтором, а не падением; валидный отчёт ничего лишнего
  не пишет. Fixture — `--fake-scenario verification-invalid`.

### Схема событий

```text
стадия: producer ARTIFACT_SUBMIT → verifier ARTIFACT_SUBMIT → VERIFY → GATE_DECISION
задача: producer ARTIFACT_SUBMIT → VERIFY → TASK_DONE
```

Human-гейты (`s_in`, `s00_intake`, `s0_vision`, `s1_spec`, `s7_release`,
`s9_learn`) остались человеческими: у них нет артефакта `verification`, и
`VERIFY` для них не пишется.

### Где рантайм запускает верификатора и как ограничен его write scope

`factory/runtime/loop.py::_complete_verification`. Границу задаёт не текст
промпта, а `_collect_writable_paths`: в заказе верификатора нет ни одного
артефакта продюсеров, поэтому `bwrap` открывает на запись только каталог
отчёта плюс `runtime/agent` для файла расхода. `state.json`, журнал,
`usage.jsonl` и evidence остаются read-only, tripwire не ослаблен. Попытка
сдать чужой артефакт от роли `02` отклоняется движком (`RunError`), а писать
`VERIFY` от имени продюсера не даёт `journal._check_semantics`.

### Замер на реальном nano

До правок этой сессии (`runs/nano-todo-verify-role-clean`): 24 шага, 48
событий, `tokens` 4 131 427, `tokens_billable` 712 291, 1655 с; верификатор
11 шагов, 2 561 288 токенов (62 %), два из одиннадцати — повтор после
невалидного отчёта.

После правок (`runs/nano-todo-verify-role-b2`, тот же провайдер и
`--reasoning high`): **23 шага, 48 событий, 3 925 927 total / 753 063
billable, 1830 с**, `usd` 0.0, `factory verify` — OK, `VERIFY` — 9.
Верификатор: **10 шагов, 2 464 313 токенов (63 %), 1004 с** — на один шаг
меньше: формулировка «ровно одно слово в `- Вердикт:`» и явное правило про
`factory verify` убрали один повтор. Стоимость шага выросла (промпт стадии
несёт новый блок про матрицу приёмки), поэтому total снизился слабее, чем
число шагов. Старая семантика на том же кейсе: 13 шагов, 1 780 308 токенов.

Первый прогон (`runs/nano-todo-verify-role-real`) дал два отказа верификатора
на `s5_acceptance` (`acceptance.md` без явного `PASS` в строках `REQ`) —
настоящий rework-цикл, ради которого всё и делалось. Он же вскрыл два дефекта
маршрутизации, которые в тестах на fake не видны:

1. после отказа гейта стадия уходила **верификатору**, а не продюсеру:
   `pending_artifacts` видит только форму артефакта, а правило «в матрице
   приёмки `PASS` на каждый `REQ`» — стадийное. Верификатор не имеет права
   править `acceptance.md`, поэтому снова писал `FAIL` и сжигал попытку.
   Исправлено: заказ верификатора выдаётся только при `validate_stage(...,
   verification_policy=None) == []`.
2. после этого фильтра шаг продюсера с пустым `pending_artifacts` попадал в
   ветку «нечего сдавать → авто-submit» и крутился 60 итераций без вызова
   исполнителя, штампуя `ARTIFACT_SUBMIT`. Исправлено: авто-submit требует,
   чтобы стадия проходила и свои стадийные правила; иначе шаг исполняется.

Оба места закрыты регрессионными тестами
(`test_a_stage_semantic_defect_goes_back_to_the_producer`,
`test_a_semantically_invalid_stage_runs_the_step_instead_of_resubmitting`).

### Реальный прогон всех пяти кейсов (B1) и бюджеты (B2)

Команда — та же, что в CI-профиле, плюс `--update-baseline`; единственное
отличие — **временные потолки** в копии кейсов (`tokens` 20M, `minutes` 240):
со старыми потолками три кейса из пяти останавливались бы на бюджете
(`standard` 2.9M против 7.58M факта), а `--update-baseline` падает целиком,
теряя и зелёные кейсы. Потолки — только для замера, в поставку они не пошли.

| Кейс | Режим | Шаги | События | Tokens | Billable | Сек | Итог |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| `brownfield-proxy-mini` | brownfield | 28 | 53 | 7 269 116 | 1 083 132 | 2 654 | зелёный |
| `product-mini` | standard | 28 | 50 | 7 581 128 | 1 416 392 | 3 540 | зелёный |
| `nano-product-mini` | nano | 22 | 48 | 6 217 251 | 857 635 | 2 398 | красный: `REQ-001..003` не покрыты |
| `nano-todo` | nano | 23 | 48 | 3 925 927 | 753 063 | 1 830 | красный: `REQ-003` не покрыт |
| `standard-mini` | standard | — | 42 | — | — | — | escalated на `s4_build` |

Почему красные — не стоимость, а артефакты:

- `nano-todo` и `nano-product-mini`: `traceability.json` ссылается на тесты,
  которых нет в объявленных файлах (`test_req_003_*` в `test_todo.py`; в
  коричневом — Rust-тесты в `config.rs`, которые `build_matrix` не видит как
  `fn` с `#[test]`). Это правило 1.0.1 «имя теста обязано существовать» и
  дефект продукта, а не рантайма.
- `standard-mini`: верификатор нашёл, что `pytest product/tests` из каталога
  прогона падает (`ModuleNotFoundError: timetrack`) без внешнего
  `PYTHONPATH`, и вернул `FAIL` три раза. Проверка кейса падает так же, то
  есть кейс был бы красным и без верификатора — он лишь вскрыл дефект на
  `s4_build`. Маршрутизация при этом плохая: `gate_reject` при
  `rework_to == stage_id` (`s4_build`) не переоткрывает задачи, поэтому
  продюсер не получает шанса исправить, а верификатор трижды проверяет те же
  файлы; причина `FAIL` до продюсера не доходит.

**`eval/baseline-hermes.json` не перезаписан.** `factory eval` завершился
кодом 1: `refusing to write a baseline from failing case(s)` — `write_baseline`
не пишет baseline из падающих кейсов, и это правильно. Пять записей остались
на старой семантике; числа не выдумывались.

Бюджеты — правило «худший зелёный замер × 1.25, округление вверх»:

| Режим | Было | Стало | Из замера |
| :--- | :--- | :--- | :--- |
| `nano` | 5.2M / 120 мин / $6 | **7.8M** / 120 / $6 | 6 217 251 × 1.25 = 7 771 564 (nano-product-mini) |
| `standard` | 2.9M / 120 / $25 | **9.5M** / 120 / $25 | 7 581 128 × 1.25 = 9 476 410 (product-mini) |
| `brownfield` | 4.5M / 30 мин / $30 | **9.1M / 60 мин** / $30 | 7 269 116 × 1.25; 44.2 мин × 1.25 |
| `enterprise` | 6M / 1440 мин / $120 | **14.9M** / 118 мин / $120 | 11 884 354 токенов × 1.25 = 14 855 443; 94,4 мин × 1.25 ≈ 118 мин |

Оговорки, которые нельзя прятать:

- `nano` поднят по **завершённому** замеру (`nano-product-mini`), но этот
  кейс формально не eval-green: провал — контентная проверка покрытия, не
  стоимость, `factory verify` — OK. Если считать строго «зелёный», у `nano`
  сейчас нет зелёного замера, а старые 5.2M ниже факта 6.22M.
- `enterprise` измерен отдельно: 11 884 354 токенов / 94,4 мин (5664,7 с) / $0.0,
  что **выше** бюджета `pipeline.yaml` (6M / 1440 мин / $120) и выше измеренной
  потребности `standard` (9.5M). Бюджет 6M подтверждён как недостаточный; «Стало»
  = замер × 1.25 по правилу budget.yaml. `usd` не измеряется (провайдер отдаёт
  `estimated_cost_usd = 0.0` при `cost_status: unknown`), так что $120 остаётся
  номинальным потолком без фактической цены. Замер на `--reasoning high` — верхняя
  оценка: `medium` текущий провайдер отвергает с `Invalid inference request`.
- `usd` не менялся нигде: провайдер не отдаёт цену, `usd` = 0.0 — «не измерен».

`eval/baseline.json` (fake) остался согласован: `nano-todo` 22/48,
`standard-mini` 24/50 — `match`.

### Что осталось|

- **`eval/baseline-hermes.json`.** Пять зелёных кейсов (покрытие REQ 1.0/1.0, `standard-mini` не эскалируется) плюс кейс `enterprise-mini` с реальными числами (36 шагов, 65 событий, 11 884 354 токенов / 94,4 мин, cobertura 1.0/1.0, product tests 5/3, pass@1 = 1.0). Решение о включении enterprise в baseline принято, записано в коммите `42c0163`. Повторный прогон под `hermes` может дать другой результат из-за недетерминизма модели (traceability.json генерируется моделью на `s1_spec`; в `nano-product-mini` модель может сослаться на `product/tests/test_todo.py` вместо тестов в `config.rs`/`router.rs`, что ломает покрытие). При повторном прогоне сверить coverage перед `write_baseline`.
- **`gate_reject` при `rework_to == stage_id`.** Закрыто: `gate_reject`
  вызывает `_reopen_stage_tasks(state, stage_id)` при self-rework
  (`s4_build`): задачи `accepted`/`review`/`in_progress` → `pending`,
  lease снимается, стадия `in_progress`, попытка гейта +1. Причина `FAIL`
  читается из последнего `VERIFY` события через `_feedback` и попадает в
  `feedback` заказа продюсера (`factory/engine.py:1578-1587`, `:644-678`,
  `:596-618`). Регрессионный тест
  `test_gate_reject_self_rework_returns_produce_with_feedback` зелёный,
  `factory doctor → OK`, целевые pytest зелёные.
- **`enterprise` замерен** (кейс `eval/cases/enterprise-mini`, `mode: enterprise`):
  36 шагов, 65 событий, 2 задачи, все 12 гейтов закрыты, pass@1 = 1.0,
  cobertura 1.0/1.0, product tests 5/3,
  **11 884 354 токенов / 94,4 мин (5664,7 с) / $0.0**, пик RSS ≈ 58 МБ, 503 API-вызова,
  модель `am/nemotron-3-ultra-550b-a55b`, `--reasoning high`. `usd` не измеряется
  (`cost_status: unknown`). Записан в `eval/baseline-hermes.json` (коммит `42c0163`),
  решение о включении принято. Бюджет enterprise: 14.9M токенов / 118 мин / $120
  (замер × 1.25).
- **`--reasoning medium` у текущего провайдера отвечает `Invalid inference request`**
  (работают `low` и `high`). Замер enterprise сделан на `high` — верхняя оценка
  относительно `medium`. Дефолт адаптера не менялся.
- **Отчёт о самой проверке.** В первом прогоне «Проверка 1» у s15 получила
  код `1`, потому что отчёта ещё не было на диске; верификатор это объяснил,
  но чище — перезапускать `factory verify` после записи файла. Это поведение
  модели, а не механизма.
- **Карточка роли** обновлена под новый путь отчёта и процедуру;
  `docs/runtime.md` §5.9 описывает шаг, отчёт и границы записи.
- **Отдельный пользователь исполнителя** (`factory setup-executor`): проверен на реальной машине —
  пользователь `factory-executor` (uid 997), polkit-правило `/etc/polkit-1/rules.d/50-factory-transient-units.rules`,
  `/run/factory-executor` (root:factory-executor 0750), ACL `rwX` на `~/.hermes/{cache,logs,installs}`,
  traverse на `/home/anony` и `~/.hermes`.
- **Подпись журнала** (`factory sign`/`factory verify`): опробована на реальном прогоне
  `enterprise-mini-20261104-191607` — `factory sign` подписал 55 событий, `factory verify`
  → `VERIFY OK`, `checkpoint: signed (55 events)`. Ключ и store в
  `/home/anony/.hermes/cache/scratch/`.
- **Тег и релиз:** `v1.0.9` на origen указывает на коммит `3021c75`, GitHub Release 1.0.9 создан
  (https://github.com/DenisZakharov2/autonomous-sw-factory/releases/tag/v1.0.9).
- **Comмит и пуш** сделаны: коммит `3021c75` (документация: версия 1.0.9) в origin main.

Состояние на 31 октября 2026. Ветка `main`, версия `1.0.1`; в рабочем дереве
добавлена команда `factory usage` (274 теста в 1.0.1 + 8 новых = 282).
Eval-набор зелёный на реальном Hermes: `eval/baseline-hermes.json` хранит
пять записей — `nano-todo` (`runs/nano-todo-20261030-191409`),
`standard-mini` (`runs/standard-mini-standard-real`),
`brownfield-proxy-mini` (`runs/brownfield-proxy-mini-brownfield-real2`),
`product-mini` (`runs/product-mini-product-real`) и `nano-product-mini`
(`runs/nano-product-mini-nano-product-real`). Fake-эталон CI
(`eval/baseline.json`) перегенерирован 31.10 и проходит.

## Что вошло в 1.0.1

- **`traceability` проверяет имя теста.** Последний сегмент ссылки после `::`
  обязан быть объявлен в файле: `def`/`async def`/`class` для Python, `fn` с
  `#[test]`/`#[tokio::test]` для Rust. Ссылка без `::` по-прежнему проверяет
  только существование файла. Матрица несёт `test_problems`, `factory trace`
  печатает причину под `GAP`, eval добавляет её к ошибке покрытия. Исторические
  прогоны не переписывались: новое правило честно показало 2/3 у
  `nano-todo-20261030-191409` (REQ-001), `brownfield-proxy-mini-brownfield-real2`
  (REQ-003) и `nano-product-mini` (REQ-003, BUG-001).
- **`factory artifact re-record`.** Легальная правка артефакта, принятого на
  гейте: событие `ARTIFACT_RERECORD` с `old_sha256`, новым `sha256`, причиной и
  evidence; `verdict` только `DRAFT`, `PASS` запрещён, статусы не двигаются.
  Право — только у владельца артефакта по реестру. `verify` применяет
  «последняя запись на путь побеждает» и печатает `re-records: N ...`; правка
  после re-record снова `tampered`. Схема расширена опциональными `old_sha256`
  и `reason`, старые журналы валидируются.
- **Новое описание для человека.** `README.md` и `README.en.md` переписаны:
  первый экран отвечает, что это, зачем, что вы получаете и чем платите;
  технические детали (22 роли, 13 стадий, журнал, схемы, eval, CI) — ниже.
- **Fake-эталон не изменился:** `nano-todo` 13/33, `standard-mini` 15/35,
  baseline match; `examples/nano-todo verify --require-gate` — OK, trace 3/3.
- **CI зелёный на `fdff070`:** run `41221412801`, 14/14 jobs
  (ubuntu/macOS/Windows × Python 3.10–3.13, eval, build).

## Что вошло в 1.0.0

- **`nano` на реальном продукте:** кейс `nano-product-mini` на крейте `pfu`
  (seed 252 КБ, commit `dfe54336…`), 13 шагов, 33 события, 2 436 448 total /
  390 496 billable, 224 product tests, запись в `eval/baseline-hermes.json`.
- **Промпт режима стал универсальным:** todo-правила `nano` переехали в
  `eval/cases/nano-todo/constraints.md`; страж —
  `test_nano_prompt_carries_no_case_specific_rules`.
- **Потолок `nano` пересчитан:** 2.5M → 3.1M токенов (худший зелёный прогон
  + 25 %), минуты те же 120.
- **Fake-эталон перегенерирован** для двух CI-кейсов: 13/33 и 15/35.
- **CI на трёх ОС зелёный** на `5326616` (run `41215608643`, 14/14); тест
  минутного бюджета больше не флейкает на windows-latest 3.11.
- **Найдено ограничение:** `traceability` проверяет существование файла
  теста, а не имя функции — закрыто в 1.0.1 (см. «Известные ограничения», п. 9).

## Что вошло в 0.3.0

- **Подписанные контрольные точки (`factory sign`).** HMAC-SHA256 от головы
  журнала, якорь в store вне `runs/`, `verify` различает `signed` / `stale` /
  `rolled_back` / `diverged`.
- **Внешний eval-набор (`factory eval`).** Два кейса, инварианты из
  `expected.json`, human-гейты от `human:eval`, отдельные baseline для fake и
  для реального исполнителя.
- **Сравнение прогонов (`factory compare`).** Дельта по стадиям, итоги и хеши
  артефактов вне control plane.
- **Реальный прогон `nano-todo` на Hermes.** Зелёный кейс, baseline
  `eval/baseline-hermes.json`, разбор цены по шагам — ниже.
- **Два исправления рантайма и движка, без которых зелёного прогона не было**
  (подробно в разделе «Что исправлено»).

## Зелёный прогон на реальном Hermes

Конфигурация (принята на весь прогон, не менялась):

```text
executor        hermes
model           deepseek-ai/DeepSeek-V4.1-Flash-1017
reasoning       high            (medium провайдер отвергает HTTP 400)
tools           auto            (file; file,terminal на s4_build)
sandbox         bwrap, run dir writable + ro-bind поверх control plane
sandbox-rw      ~/.hermes/installs/<hash>, ~/.hermes/cache, ~/.hermes/logs
timeout         900 s на шаг, --yolo
budget          3 000 000 total / 120 минут (флаги замера;
                дефолт режима `nano` сейчас — 3.1M / 120 мин)
```

Команда:

```bash
export FACTORY_RUNS_ROOT="$PWD/runs"
factory eval --cases "$PWD/runs/hermes-cases" --executor hermes \
  --executor-model deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  --executor-tools auto --reasoning high --timeout 900 --yolo \
  --sandbox-rw /home/anony/.hermes/installs/74e85d3cd01c2ec7 \
  --sandbox-rw ~/.hermes/cache --sandbox-rw ~/.hermes/logs \
  --budget-tokens 3000000 --budget-minutes 120 \
  --update-baseline eval/baseline-hermes.json --json
```

Итог: `run_dir = runs/nano-todo-20261030-191409`, `verify` — OK,
`status=complete`, все 12 гейтов закрыты.

| Метрика | Значение |
| :--- | ---: |
| steps (рабочие шаги) | 13 |
| loop_steps | 29 |
| events | 33 |
| rework_events | 0 |
| tasks / accepted | 2 / 2 |
| pass@1 | 1.0 |
| verification_coverage | 1.0 |
| traceability_coverage | 1.0 |
| tokens total | 1 780 308 |
| tokens billable (in+out) | 336 980 |
| cache read | 1 443 328 (81 % total) |
| api_calls | 85 |
| секунды исполнителя | 605.7 |
| wall time кейса | 10 мин 29 с |
| prompt_chars | 108 156 |
| usd | 0.0 — **не цена** (см. ниже) |
| product tests | 14 passed (pytest, `product/tests`) |
| checks | 3 passed (`checks/test_nano_invariants.py`) |

### Цена по шагам

| step | stage | task | role | total | in | cache | out | billable | api | sec |
| ---: | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | s00_intake | — | 01-smart-interviewer | 21 997 | 7 263 | 13 056 | 1 678 | 8 941 | 2 | 21.8 |
| 2 | s0_vision | — | 01-smart-interviewer | 39 265 | 10 822 | 25 344 | 3 099 | 13 921 | 3 | 28.5 |
| 3 | s1_spec | — | 01-smart-interviewer | 86 560 | 14 334 | 66 048 | 6 178 | 20 512 | 5 | 44.6 |
| 4 | s15_quarantine | — | 00-orchestrator | 56 856 | 8 355 | 47 104 | 1 397 | 9 752 | 5 | 22.1 |
| 5 | s2_architecture | — | 12-fullstack-developer | 148 646 | 29 486 | 104 192 | 14 968 | 44 454 | 6 | 88.5 |
| 6 | s3_plan | — | 01-smart-interviewer | 88 175 | 14 470 | 70 400 | 3 305 | 17 775 | 6 | 30.2 |
| 7 | s4_build | TASK-001 | 12-fullstack-developer | 155 696 | 20 141 | 129 280 | 6 275 | 26 416 | 9 | 48.9 |
| 8 | s4_build | TASK-002 | 12-fullstack-developer | 170 633 | 19 656 | 144 128 | 6 849 | 26 505 | 8 | 51.4 |
| 9 | s5_acceptance | — | 12-fullstack-developer | 199 863 | 32 659 | 158 208 | 8 996 | 41 655 | 8 | 62.6 |
| 10 | s6_security | — | 12-fullstack-developer | 186 701 | 23 900 | 155 136 | 7 665 | 31 565 | 9 | 55.2 |
| 11 | s7_release | — | 12-fullstack-developer | 194 107 | 28 513 | 156 672 | 8 922 | 37 435 | 8 | 60.4 |
| 12 | s8_docs | — | 12-fullstack-developer | 50 053 | 7 816 | 40 192 | 2 045 | 9 861 | 4 | 23.5 |
| 13 | s9_learn | — | 00-orchestrator | 381 756 | 38 344 | 333 568 | 9 844 | 48 188 | 12 | 68.0 |

### Про `usd`

`runtime/usage.jsonl` пишет `usd` из `estimated_cost_usd` ответа Hermes.
Провайдер отдаёт `0.0` при `cost_status: unknown`, поэтому в baseline стоит
`usd: 0.0`. Это **не измеренная стоимость**: цена не считается, а бюджет
`--budget-usd` не проверяется. `cost_per_artifact_usd` в метриках — `null`.
Пока провайдер не отдаёт реальную цену, сравнивать прогоны по деньгам нельзя;
сравнивайте `tokens_billable` (336 980).

### Где дороже всего

В этом прогоне `s4_build` — не половина счёта: 326 329 total (18 %), по
155–170k на задачу. Дороже всех хвост: `s9_learn` 381 756 (21 %), затем
`s5_acceptance` 199 863, `s7_release` 194 107, `s6_security` 186 701.
Прежняя оценка «`s4_build` — 70–80 % расхода» верна для прогонов, где задача
стоила 400–800k; после правок промпта (запрет читать `factory/**`, правила
валидатора в тексте, две задачи в плане, подсказка `s15`) цена задачи упала
в 3–5 раз, и расход переехал в содержательные хвостовые стадии.

Разброс между прогонами одной модели и конфигурации честно большой:

| прогон | total | billable | s2_arch | s4_build | s9_learn | итог |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| 183641 | 1 962 231 | 366 583 | 157k | 524k | 309k | красный: 7 `REQ`, unittest |
| 185720 | 1 853 041 | 326 257 | 164k | 669k | 249k | красный: переписана `traceability` |
| 191409 | 1 780 308 | 336 980 | 149k | 326k | 382k | **зелёный** |

`tokens_billable` стабильнее total (±3 %), total — ±5 %; одна стадия может
различаться в 2.6 раза (`s9_learn` 249k → 382k). Один прогон не доказывает
улучшение; для сравнения нужны baseline и `factory compare`.

## standard-mini на реальном Hermes

Тот же профиль, что у `nano-todo`: одна модель, `--reasoning high`,
`--executor-tools auto`, `bwrap`, `--yolo`; бюджет замера 4M / 240 минут
(дефолт `standard` после пересчёта — 2.9M / 120 минут).

```bash
factory eval --cases "$PWD/runs/hermes-cases" --executor hermes \
  --executor-model deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  --executor-tools auto --reasoning high --timeout 900 --yolo \
  --budget-tokens 4000000 --budget-minutes 240 --stamp standard-real --json
```

Итог: `run_dir = runs/standard-mini-standard-real`, `status=complete`,
`verify` — OK, все 12 гейтов закрыты как `human:eval`.

| Метрика | Значение |
| :--- | ---: |
| steps / loop_steps / events | 15 / 31 / 35 |
| tasks / accepted | 2 / 2 |
| pass@1 | 1.0 |
| verification_coverage / traceability_coverage | 1.0 / 1.0 |
| tokens total | 1 978 597 |
| tokens billable | 363 237 |
| api_calls | 97 |
| секунды исполнителя / wall | 653 / 673 (11,2 мин) |
| prompt_chars | 139 753 |
| product tests | 11 passed |
| usd | 0.0 — не цена (`cost_status: unknown`) |

Расход: `s4_build` 748 589 (37,8 %, задачи 325k и 424k), `s6_security`
229 834 (две роли), `s9_learn` 168 982, `s7_release` 152 320,
`s5_acceptance` 146 650, `s2_architecture` 146 133.

Рамки `constraints.md` удержаны: в `spec.md` и `spec-human.md` ровно
`REQ-001/002/003`, в плане и `tasks/` ровно `TASK-001` и `TASK-002`,
`traceability.json` ссылается только на них, `s1_spec` сдали две роли
раздельно (`05` — spec/spec-human/traceability, `06` — domain-model/
access-matrix). Секция «Ограничения кейса» есть в каждом записанном промпте.

Пилот на трёх шагах (`runs/standard-mini-20261030-204101`) дал 2 модельных
шага: 61 101 total / 21 933 billable, 5 API-вызовов, 46 с. Экстраполяция по
профилю `nano` (137–151k на шаг) предсказала 2,05–2,26M на 15 шагов; факт —
1,98M.

`factory compare standard-mini-fake-compare standard-mini-standard-real`:
шаги и события совпадают постадийно (15/15, 35/35), токены только у реального
прогона — 363 237 billable. `only_left` в хешах (`product/src/todo.py`,
`screens/cli.md`) — фикстура `FakeExecutor` от `nano`, а не продукт `standard`.

Побочно: закоммиченный fake-эталон `eval/baseline.json` записан до фикса
«стадия без pending не зовёт модель» (`f1895da`) и хранит 14/33 и 16/35,
тогда как текущий fake даёт 13/33 и 15/35. Тест этого не ловит: падение шагов
не считается регрессией. Перегенерация — отдельная задача, fake-эталон здесь
не менялся.

## brownfield-proxy-mini на реальном Hermes

Первый brownfield-прогон на реальном продукте: публичный
`proxy-for-ubuntu` 0.4.0 (Rust/Tauri, commit `dfe54336…`, 12 МБ с `.git`),
seed-архив в `eval/cases/brownfield-proxy-mini/seed/`. Та же модель и
конфигурация, что у `nano-todo`: `deepseek-ai/DeepSeek-V4.1-Flash-1017`,
`--reasoning high`, `--executor-tools auto`, `bwrap`, `--yolo`.

```bash
export FACTORY_RUNS_ROOT="$PWD/runs"
export CARGO_TARGET_DIR="$PWD/runs/brownfield-src/proxy-for-ubuntu/app/src-tauri/target"
factory eval --cases "$PWD/runs/hermes-cases-brownfield" --executor hermes \
  --executor-model deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  --executor-tools auto --reasoning high --timeout 900 --yolo \
  --sandbox-rw /home/anony/.hermes/installs/74e85d3cd01c2ec7 \
  --sandbox-rw ~/.hermes/cache --sandbox-rw ~/.hermes/logs \
  --sandbox-rw "$CARGO_TARGET_DIR" \
  --budget-tokens 4500000 --budget-minutes 240 --stamp brownfield-real --json
```

Первая попытка шла с `--budget-tokens 3000000` и остановилась на `s7_release`
при **3 115 133 total**; потолок поднят не на ходу, а в продолжении того же
журнала (`factory run` с 4.5M). Команда выше — то, чем case нужно запускать
с нуля.

| Метрика | Значение |
| :--- | ---: |
| steps / loop_steps / events | 17 / 32 / 38 |
| rework_events | 0 |
| tasks / accepted | 2 / 2 |
| pass@1 | 1.0 |
| verification_coverage / traceability_coverage | 1.0 / 1.0 |
| tokens total | 3 560 791 |
| tokens billable | 528 727 |
| cache read | 3 032 064 |
| api_calls | 155 |
| секунды исполнителя / wall | 1033 / ~1290 |
| prompt_chars | 194 916 |
| product tests | 222 passed (`cargo test --lib`, +4 новых) |
| restore drill | 140/140 tracked files, HEAD/status/sha256 совпали |

### Цена по шагам

| step | stage | task | role | total | in | cache | out | billable | api | sec |
| ---: | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | s_in | — | 01-smart-interviewer | 123 404 | 18 268 | 100 352 | 4 784 | 23 052 | 8 | 43.8 |
| 2 | s_in | — | 08-software-architect | 206 822 | 26 541 | 174 592 | 5 689 | 32 230 | 10 | 46.4 |
| 3 | s00_intake | — | 01-smart-interviewer | 23 852 | 10 555 | 10 496 | 2 801 | 13 356 | 2 | 27.6 |
| 4 | s0_vision | — | 03-product-owner | 56 538 | 14 685 | 37 632 | 4 221 | 18 906 | 4 | 37.3 |
| 5 | s1_spec | — | 05-business-analyst | 51 978 | 10 449 | 37 632 | 3 897 | 14 346 | 4 | 33.6 |
| 6 | s1_spec | — | 06-system-analyst | 94 569 | 17 088 | 72 704 | 4 777 | 21 865 | 6 | 39.9 |
| 7 | s15_quarantine | — | 16-security-engineer | 61 101 | 11 214 | 48 128 | 1 759 | 12 973 | 5 | 27.0 |
| 8 | s2_architecture | — | 08-software-architect | 290 984 | 39 744 | 235 520 | 15 720 | 55 464 | 10 | 104.6 |
| 9 | s3_plan | — | 04-team-lead | 38 679 | 9 427 | 26 112 | 3 140 | 12 567 | 3 | 30.1 |
| 10 | s4_build | TASK-001 | 12-fullstack-developer | 488 901 | 40 175 | 438 272 | 10 454 | 50 629 | 17 | 135.6 |
| 11 | s4_build | TASK-002 | 12-fullstack-developer | 823 512 | 43 317 | 764 672 | 15 523 | 58 840 | 28 | 157.4 |
| 12 | s5_acceptance | — | 14-qa-engineer | 399 088 | 43 422 | 344 832 | 10 834 | 54 256 | 14 | 77.8 |
| 13 | s6_security | — | 16-security-engineer | 251 117 | 34 375 | 207 360 | 9 382 | 43 757 | 10 | 66.7 |
| 14 | s6_security | — | 12-fullstack-developer | 77 368 | 10 812 | 62 720 | 3 836 | 14 648 | 6 | 39.8 |
| 15 | s7_release | — | 19-devops-engineer | 127 220 | 23 072 | 96 256 | 7 892 | 30 964 | 7 | 59.1 |
| 16 | s8_docs | — | 12-fullstack-developer | 284 204 | 30 107 | 247 808 | 6 289 | 36 396 | 13 | 53.1 |
| 17 | s9_learn | — | 00-orchestrator | 161 454 | 27 708 | 126 976 | 6 770 | 34 478 | 8 | 53.0 |

`s4_build` — 1.31M (37 %), из них `TASK-002` 824k; `s2_architecture` 291k и
`s5_acceptance` 399k — следующие. Ровно две задачи, `TASK-002` зависит от
`TASK-001`, обе правят `app/src-tauri/src/config.rs`.

### Что дал seed и как прошёл drill

- `seed/repo.tar.gz` + `seed/seed.json`: 9,7 МБ, commit и sha256 пинятся,
  `doctor` проверяет hash/path traversal/`.git`. Распаковка идёт после
  `init_run` и до `_drive_run`, `HEAD` сверяется с пином.
- `s_in` выполнен ролью `01` с `file,terminal`: `git bundle create --all`,
  `git ls-files | tar`, `sha256sum -c`, `git clone` в `backups/restore-check`.
  Drill восстановил 140/140 отслеживаемых файлов, `HEAD` и
  `git status --porcelain --untracked-files=no` совпали; `restore-drill.md` —
  `PASS`. `checks/test_brownfield_evidence.py` подтвердил это после прогона.
- Brownfield-режим переопределяет `product_src`/`product_tests` на
  `app/src-tauri/{src,tests}`; без этого `s4_build` требовал пустой
  `product/src`, которого в реальном репозитории нет.

### Цена cargo

`cargo test --lib` на 4 ядрах: холодная сборка **567 с**, тёплая **54 с**,
218 тестов до изменений и **222** после. `timeout_seconds` в схеме — 600,
поэтому прогретый `CARGO_TARGET_DIR` передаётся и агенту (`--sandbox-rw`), и
`_product_tests`; `cargo clean` в задаче запрещён.

## Стоимость brownfield

Источник — `factory usage` (только `runtime/usage.jsonl`), прогон
`brownfield-proxy-mini-brownfield-real2`. `usd` во всех записях `0.0` при
`cost_status: unknown`, поэтому ниже он не приводится как цена: сравнение идёт
по `tokens_billable`, а `tokens` показывает полный контекст с кешем.

### По стадиям

| stage | steps | api | prompt_chars | tokens | billable | cache_read | tokens_out | seconds |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `s_in` | 2 | 18 | 20 613 | 330 226 | 55 282 | 274 944 | 10 473 | 90.13 |
| `s00_intake` | 1 | 2 | 10 630 | 23 852 | 13 356 | 10 496 | 2 801 | 27.56 |
| `s0_vision` | 1 | 4 | 11 716 | 56 538 | 18 906 | 37 632 | 4 221 | 37.31 |
| `s1_spec` | 2 | 10 | 22 441 | 146 547 | 36 211 | 110 336 | 8 674 | 73.47 |
| `s15_quarantine` | 1 | 5 | 12 536 | 61 101 | 12 973 | 48 128 | 1 759 | 26.98 |
| `s2_architecture` | 1 | 10 | 16 881 | 290 984 | 55 464 | 235 520 | 15 720 | 104.57 |
| `s3_plan` | 1 | 3 | 11 808 | 38 679 | 12 567 | 26 112 | 3 140 | 30.10 |
| `s4_build` | 2 | 45 | 21 431 | 1 312 413 | 109 469 | 1 202 944 | 25 977 | 293.02 |
| `s5_acceptance` | 1 | 14 | 10 944 | 399 088 | 54 256 | 344 832 | 10 834 | 77.82 |
| `s6_security` | 2 | 16 | 21 971 | 328 485 | 58 405 | 270 080 | 13 218 | 106.44 |
| `s7_release` | 1 | 7 | 11 803 | 127 220 | 30 964 | 96 256 | 7 892 | 59.14 |
| `s8_docs` | 1 | 13 | 10 757 | 284 204 | 36 396 | 247 808 | 6 289 | 53.06 |
| `s9_learn` | 1 | 8 | 11 385 | 161 454 | 34 478 | 126 976 | 6 770 | 53.00 |
| **TOTAL** | **17** | **155** | **194 916** | **3 560 791** | **528 727** | **3 032 064** | **117 768** | **1 032.62** |

### По задачам

| task | steps | api | prompt_chars | tokens | billable | cache_read | tokens_out | seconds |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `-` (без задачи) | 15 | 110 | 173 485 | 2 248 378 | 419 258 | 1 829 120 | 91 791 | 739.60 |
| `TASK-001` | 1 | 17 | 10 797 | 488 901 | 50 629 | 438 272 | 10 454 | 135.62 |
| `TASK-002` | 1 | 28 | 10 634 | 823 512 | 58 840 | 764 672 | 15 523 | 157.40 |
| **TOTAL** | **17** | **155** | **194 916** | **3 560 791** | **528 727** | **3 032 064** | **117 768** | **1 032.62** |

### Топ-5 драйверов

По `tokens_billable`; `TASK-001` — шестой (50 629), но второй по `tokens`
(488 901). Строки `s4_build` разложены по задачам, чтобы одна задача не
пряталась за стадией.

| # | драйвер | steps | api | tokens | billable | cache_read | tokens_out | seconds |
| ---: | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `s4_build / TASK-002` | 1 | 28 | 823 512 | 58 840 | 764 672 | 15 523 | 157.40 |
| 2 | `s6_security` | 2 | 16 | 328 485 | 58 405 | 270 080 | 13 218 | 106.44 |
| 3 | `s2_architecture` | 1 | 10 | 290 984 | 55 464 | 235 520 | 15 720 | 104.57 |
| 4 | `s_in` | 2 | 18 | 330 226 | 55 282 | 274 944 | 10 473 | 90.13 |
| 5 | `s5_acceptance` | 1 | 14 | 399 088 | 54 256 | 344 832 | 10 834 | 77.82 |

`cache_read` — не отдельное чтение файла: это сумма кешированного префикса по
всем API-вызовам шага. Контекст растёт с каждым результатом инструмента, и
каждый следующий вызов снова читает его из кеша. Поэтому драйвер — число
вызовов × объём накопленного контекста, а не размер промпта (у всех стадий
10–22 тыс. символов).

- **`TASK-002`** — 28 вызовов. Агент читал `app/src-tauri/src/config.rs`
  целиком (после `TASK-001` в файле 2 380 строк), правил
  `Config::warnings()` и гонял терминал: целевой
  `cargo test --lib --manifest-path app/src-tauri/Cargo.toml config::tests::warns_on_unreachable_after_match`,
  полный `cargo test --lib` (222 passed) и `cargo fmt --check`. Отсюда
  764 672 cache и 15 523 output. Evidence: `runtime/ev-0022-*.log`,
  `tasks/TASK-002/deliverable.md`.
- **`s6_security`** — 16 вызовов, две роли. `16-security-engineer` статически
  читал `config.rs` (`warnings()`), `artifacts/threat-model.md` и
  `artifacts/access-matrix.md`; `12-fullstack-developer` проверял отсутствие
  SQL/миграций (`0` совпадений `*.sql`/`CREATE TABLE`/`migrat*` в
  `app/src-tauri/src/` и db-зависимостей в `Cargo.toml`) и писал
  `db-report.md`/`data-pipeline.md`. 270 080 cache, 13 218 output.
- **`s2_architecture`** — 10 вызовов и самый большой `tokens_out` в прогоне:
  15 720 (чуть больше, чем 15 523 у `TASK-002`). За шаг создано девять
  артефактов (`architecture.md`,
  `api-contract.md`, `data-model.md`, `threat-model.md`, `ADR-001/002`,
  `ux-flows.md`, `screens/rules-editor.md`, `ai-design.md`); каждый
  записанный документ остаётся в контексте следующих вызовов, отсюда
  235 520 cache. Evidence: `runtime/ev-0013-*.log`.
- **`s_in`** — два шага, 18 вызовов. Команды `git bundle create --all`,
  `git ls-files | tar`, `sha256sum -c`, `git bundle verify`, `git status` и
  read-only скан продукта: 140 отслеживаемых файлов, 38 `*.rs`,
  19 254 строки. 274 944 cache, 10 473 output. Evidence:
  `runtime/ev-0001-*.log`, `runtime/ev-0002-*.log`.
- **`s5_acceptance`** — 14 вызовов, инструментов только `file`. Статическое
  чтение `config.rs:372-476`, `config.rs:2195-2379`, `lib.rs:7`,
  `tasks/TASK-001|002/deliverable.md` и `verification.md`; записаны
  `test-report.md` и `acceptance.md`. 344 832 cache, 10 834 output.
  Evidence: `runtime/ev-0027-*.log`.

`factory compare --left brownfield-proxy-mini-brownfield-fake2 --right
brownfield-proxy-mini-brownfield-real2`: шаги **17/17**, события **38/38**,
артефакты **173 same / 44 changed / 1 only left / 175 only right**. У fake
токены `null` (адаптер их не измеряет), поэтому дельта токенов в compare —
`-`; реальные числа выше даёт только `factory usage`. `only_left` —
`artifacts/screens/cli.md`, фикстура `FakeExecutor`; `only_right` — реальные
`adr/ADR-002.md`, два патча, `screens/rules-editor.md` и
`backups/dirty-worktree.tar.gz`.

### Кандидаты на удешевление

Промпты в этой сессии **не менялись**: без реального прогона эффект не
доказан. Кандидаты — гипотезы с ценой в текущем прогоне и метрикой проверки.

| Кандидат | Эффект (текущая цена) | Риск | Что мерить |
| :--- | :--- | :--- | :--- |
| Читать в `TASK-002` только нужные участки `config.rs` (`Rule::parse`, `parse_policy`, `warnings`, тест-модуль), а не файл целиком | 764 672 cache, 28 вызовов, 823 512 tokens | пропущенный call site или тест → красная сборка | `cache_read`, `api_calls`, `tokens_billable` `TASK-002`; 222 теста и `verify` |
| Передавать в `s6_security` сжатый пакет приёмки (`test-report.md` + цитаты `config.rs`) вместо повторного чтения кода | 270 080 cache, 16 вызовов, 328 485 tokens | security-ревью по пересказу, а не по исходнику | `cache_read`/`api_calls` `s6_security`; число дефектов и вердикт гейта |
| Ограничить `s2_architecture` девятью документами: сначала контракт + ADR, остальное — по шаблону с короткими секциями | 15 720 output, 235 520 cache, 10 вызовов | больше шагов или формальные, пустые артефакты | `tokens_out`/`cache_read` `s2_architecture`, прохождение гейта `gate_s2`, полнота артефактов |

## product-mini на реальном Hermes

Второй standard-кейс на том же продукте: крейт `pfu` из `proxy-for-ubuntu`
(commit `dfe54336…`), разложенный в `product/` без `.git` (standard не делает
restore drill). Задача — backoff повторных попыток подписки в
`subscription.rs`; seed 252 КБ, `require_git: false`. Профиль тот же, что у
`standard-mini`; бюджет замера 3M / 120 минут.

Итог: `status=complete`, `verify` — OK, все 12 гейтов, 15 шагов, 35 событий,
2 задачи, **2 284 755 total / 430 803 billable**, 97 API-вызовов, 737 с
(12,3 мин), **228 product tests** (218 + 10 новых), coverage 1.0/1.0.
Запись — в `eval/baseline-hermes.json`.

| step | stage | task | role | total | api | sec |
| ---: | :--- | :--- | :--- | ---: | ---: | ---: |
| 1 | s00_intake | — | 01-smart-interviewer | 24 263 | 2 | 21.1 |
| 2 | s0_vision | — | 03-product-owner | 41 788 | 3 | 30.0 |
| 3 | s1_spec | — | 05-business-analyst | 58 574 | 4 | 31.1 |
| 4 | s1_spec | — | 06-system-analyst | 97 013 | 6 | 38.6 |
| 5 | s15_quarantine | — | 16-security-engineer | 35 027 | 3 | 19.3 |
| 6 | s2_architecture | — | 08-software-architect | 241 673 | 9 | 95.4 |
| 7 | s3_plan | — | 04-team-lead | 40 207 | 3 | 21.8 |
| 8 | s4_build | TASK-001 | 12-fullstack-developer | 199 599 | 8 | 80.3 |
| 9 | s4_build | TASK-002 | 12-fullstack-developer | 357 940 | 11 | 83.3 |
| 10 | s5_acceptance | — | 14-qa-engineer | 269 230 | 8 | 53.7 |
| 11 | s6_security | — | 16-security-engineer | 148 350 | 6 | 32.9 |
| 12 | s6_security | — | 12-fullstack-developer | 77 178 | 5 | 41.7 |
| 13 | s7_release | — | 19-devops-engineer | 221 359 | 9 | 83.0 |
| 14 | s8_docs | — | 12-fullstack-developer | 263 970 | 11 | 47.7 |
| 15 | s9_learn | — | 00-orchestrator | 208 584 | 9 | 57.3 |

`factory compare product-mini-product-fake product-mini-product-real`: шаги и
события совпадают постадийно (15/15, 35/35); токены только у реального —
430 803 billable. `only_left` — фикстуры `FakeExecutor` (`product/src/todo.py`,
`product/tests/test_todo.py`), `only_right` — реальные файлы кейса.

Потолок `standard` пересчитан по худшему из двух зелёных прогонов:
2 284 755 + 25 % → **2.9M / 120 минут** (было 2.5M). Время не является
узким местом: факт 12,3 минуты.

## nano-product-mini на реальном Hermes

Первый nano-кейс на реальном продукте и первый nano-кейс с seed: режим
`nano`, но продукт — крейт `pfu` из `proxy-for-ubuntu` (commit `dfe54336…`),
разложенный в `product/` без `.git` (seed 252 КБ — тот же архив, что у
`product-mini`; 9,7 МБ brownfield-репозитория не дублируется). Задача —
действующие лимиты канала: `LimitsConfig::effective_for` в `config.rs`,
отказ `validate`/`validate_for_read` на неизвестный ключ `limits.outbound`
и переход `Engine::build` на новый метод в `router.rs`.

Сначала todo-специфичные правила nano переехали из промпта режима в
`eval/cases/nano-todo/constraints.md`: промпт `nano` больше не знает про
`REQ-001 add` и «ровно две задачи», а рамки кейса знают. Fake `nano-todo`
после переноса не изменился — 13 шагов / 33 события.

Профиль тот же: одна модель `deepseek-ai/DeepSeek-V4.1-Flash-1017`,
`--reasoning high`, `--executor-tools auto`, `bwrap`, `--yolo`; бюджет
замера 2.5M / 120 минут; `CARGO_TARGET_DIR` — прогретый brownfield-target.

```bash
export FACTORY_RUNS_ROOT="$PWD/runs"
export CARGO_TARGET_DIR="$PWD/runs/brownfield-src/proxy-for-ubuntu/app/src-tauri/target"
factory eval --cases "$PWD/runs/hermes-cases-nano-product" --executor hermes \
  --executor-model deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  --executor-tools auto --reasoning high --timeout 900 --yolo \
  --sandbox-rw /home/anony/.hermes/installs/74e85d3cd01c2ec7 \
  --sandbox-rw ~/.hermes/cache --sandbox-rw ~/.hermes/logs \
  --sandbox-rw "$CARGO_TARGET_DIR" \
  --budget-tokens 2500000 --budget-minutes 120 --stamp nano-product-real --json
```

Итог: `status=complete`, `verify` — OK, все 12 гейтов, **13 шагов, 33
события, 2 задачи**, 2 436 448 total / 390 496 billable, 95 API-вызовов,
707 с (11,8 мин), **224 product tests** (`cargo test --lib`; 218 до
изменений, 6 новых), coverage 1.0/1.0. Прогон уложился в 2.5M на 97,5 % —
прежний потолок больше не годится, `nano` пересчитан по правилу «+25 % от
худшего зелёного»: **3.1M / 120 минут**.

| step | stage | task | role | total | in | cache | out | billable | api | sec |
| ---: | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | s00_intake | — | 01-smart-interviewer | 24 732 | 12 576 | 10 240 | 1 916 | 14 492 | 2 | 24.1 |
| 2 | s0_vision | — | 01-smart-interviewer | 42 391 | 15 792 | 23 808 | 2 791 | 18 583 | 3 | 30.0 |
| 3 | s1_spec | — | 01-smart-interviewer | 106 000 | 15 459 | 83 968 | 6 573 | 22 032 | 6 | 47.3 |
| 4 | s15_quarantine | — | 00-orchestrator | 61 683 | 13 358 | 47 104 | 1 221 | 14 579 | 5 | 23.2 |
| 5 | s2_architecture | — | 12-fullstack-developer | 195 513 | 26 464 | 156 416 | 12 633 | 39 097 | 8 | 77.7 |
| 6 | s3_plan | — | 01-smart-interviewer | 57 921 | 9 134 | 46 080 | 2 707 | 11 841 | 4 | 24.7 |
| 7 | s4_build | TASK-001 | 12-fullstack-developer | 705 840 | 64 761 | 632 576 | 8 503 | 73 264 | 13 | 106.8 |
| 8 | s4_build | TASK-002 | 12-fullstack-developer | 401 727 | 28 607 | 367 872 | 5 248 | 33 855 | 15 | 102.1 |
| 9 | s5_acceptance | — | 12-fullstack-developer | 129 549 | 23 797 | 100 352 | 5 400 | 29 197 | 7 | 45.7 |
| 10 | s6_security | — | 12-fullstack-developer | 66 611 | 13 490 | 48 384 | 4 737 | 18 227 | 4 | 37.4 |
| 11 | s7_release | — | 12-fullstack-developer | 281 712 | 38 097 | 230 144 | 13 471 | 51 568 | 10 | 87.0 |
| 12 | s8_docs | — | 12-fullstack-developer | 180 273 | 23 310 | 151 552 | 5 411 | 28 721 | 9 | 45.4 |
| 13 | s9_learn | — | 00-orchestrator | 182 496 | 28 204 | 147 456 | 6 836 | 35 040 | 9 | 55.9 |

`s4_build` — 1 107 567 total (45 %): `TASK-001` 705 840 (13 вызовов, правка
2 078-строчного `config.rs` и 6 новых тестов), `TASK-002` 401 727 (15
вызовов, `router.rs`). Дороже всего после сборки — `s7_release` 281 712 и
`s2_architecture` 195 513. Дорогая `TASK-001` — плата за чтение большого
файла и `cargo`-цикл в терминале, а не за rework: `rework_events` = 0.

Рамки кейса удержаны буквально: в `spec.md`/`spec-human.md` ровно
`REQ-001..003`, в плане ровно `TASK-001`/`TASK-002` (`TASK-002` с
`depends_on: TASK-001`), `traceability.json` ссылается только на них,
`s1_spec` сдала одна роль `01-smart-interviewer` — в `nano` ролей `05`/`06`
нет.

Продукт изменён по идее, а не только по тестам: `effective_for` сливает
глобальные лимиты с переопределением (`0` в `rate_limit` — «без
ограничения», неизвестное имя получает глобальные значения),
`validate_inner` отвергает неизвестный ключ `limits.outbound` и называет его
в ошибке, `Engine::build` собирает `outbound_limits` через новый метод.

`factory compare nano-product-mini-casecheck nano-product-mini-nano-product-real`:
шаги и события совпадают постадийно (13/13, 33/33); токены только у реального
прогона — 390 496 billable. `only_left` — фикстуры `FakeExecutor`
(`product/src/todo.py`, `product/tests/test_todo.py`, `screens/cli.md`),
`only_right` — реальные артефакты кейса (`adr/ADR-002.md`, два патча,
`screens/config-limits.md`).

## Что исправлено, чтобы гейт `s4_build` проходил

### 1. `factory/verify.py`: последняя запись на путь, а не все исторические

`task submit` пишет sha256, а `_check_recorded_artifacts` сравнивал **все**
записи журнала по пути. Эталонный план `nano` делит `product/src/todo.py`
между `TASK-001` и зависимой `TASK-002`, поэтому гейт отвергал стадию с
«artifact tampered after submission/approval» на файле, который вторая задача
правила законно. `fake` проходил, потому что писал те же байты.

Теперь при проверке вычисляется последняя запись на путь среди проверяемых
записей (журнальный порядок), старые записи пропускаются. Подмена не
ослаблена: проверяется именно последняя запись, поэтому файл, изменённый
после последней сдачи, по-прежнему даёт ошибку.

Тесты — `tests/test_verify_task_lineage.py`:

- две задачи с `depends_on`, вторая перезаписывает общий файл → `verify_run(run, "s4_build") == []`;
- файл изменён после последней сдачи → `tampered ... product/src/todo.py`;
- версия отклонённой задачи на диске не маскирует принятую → `tampered`.

### 2. `factory/runtime/loop.py`: стадия без незакрытых артефактов не зовёт модель

После приёмки обеих задач стадия `s4_build` оставалась `pending`, и рантайм
запускал ещё один produce-шаг, у которого `pending_artifacts` пуст. Промпт
печатал «Все объявленные артефакты стадии на месте», список «Разрешено
писать» был пуст — и агент переписывал уже принятые файлы (`test_store.py`,
`tasks/TASK-002/deliverable.md`), после чего гейт честно ловил расхождение
хешей. Теперь такой шаг не запускается: рантайм фиксирует сдачу стадии и
идёт к проверке. Это и дешевле (шаг стоил ~50k), и убирает соблазн.

Тест — `tests/test_runtime_loop.py::test_stage_with_nothing_pending_does_not_call_the_executor`:
исполнитель не вызывается ни разу на `s4_build`, стадия становится `accepted`.

### 3. `factory/artifacts.py`: `validate_plan` проверяет ссылки `traceability`

Матрица пишется на `s1_spec`, когда плана ещё нет, поэтому её `tasks` —
предположение. Раньше ссылка на несуществующий `TASK-003` всплывала только
в финальном `verify_run(run)`, после оплаченной сборки: прогон 183641 прошёл
все гейты и упал на приёмке кейса. Теперь `validate_plan` (гейт `s3_plan`)
проверяет, что каждая ссылка указывает на задачу из плана.

### 4. Правила промпта, без которых кейс красный

- `s1_spec` в `nano`: ровно три `REQ` (`add`, `list`, `done`); хранение,
  зависимости и обработка ошибок — ограничения в тексте, а не отдельные
  `REQ`. Матрица ссылается только на `TASK-001`/`TASK-002`.
- `s3_plan`: `traceability.json` принят на `s1_spec` и заморожен — план
  следует матрице, а не наоборот. Попытка «согласовать» матрицу на `s3`
  переписывает артефакт, принятый на другом гейте, и это законно падает как
  tampering (прогон 185720).
- `s3_plan`: план состоит ровно из двух задач; распределение `REQ` берётся
  из матрицы.

### 5. `eval/cases/nano-todo/expected.json`: тесты продукта через pytest

Кейс запускал `python -m unittest discover`, а реальный агент пишет
pytest-функции: `unittest` находил 0 тестов и кейс падал на «NO TESTS RAN».
Команда заменена на `{python} -m pytest product/tests`; unittest-классы
pytest собирает тоже, поэтому fake-эталон не изменился (3 теста).
`-q` не добавляйте: в репозитории `addopts = "-q"`, двойной `-q` печатает
только точки, и eval не видит числа тестов.

## Известные ограничения

1. **Бюджет `nano` — 3.1M / 120 мин по худшему из двух кейсов.**
   `nano-todo` дал 1.78–1.96M total (327–367k billable), `nano-product-mini`
   — 2 436 448 (390 496 billable), это 97,5 % прежнего потолка 2.5M, поэтому
   потолок пересчитан: 2 436 448 + 25 % → 3.1M. Прежние 400k останавливали
   реальный прогон сразу после плана, до написания кода. Флаги
   `--budget-tokens/--budget-minutes` нужны, только если модель дороже
   измеренной. USD-потолок $6 номинальный: цена не измеряется.
2. **USD не измеряется.** `estimated_cost_usd = 0.0` при `cost_status:
   unknown`; в baseline стоит 0.0, а не `null`, но ценой это не является.
3. **`brownfield` прогнан на реальном Hermes 30.10.2026**, но с оговоркой:
   первый запуск остановился по потолку 3M на `s7_release` (3 115 133 total),
   продолжение того же журнала с 4.5M дало полные 17 шагов и `verify` OK.
   Живого ревью ролей `05`/`06`/`08` не было: гейты закрыты `human:eval`, как
   и в остальных кейсах. Отдельно: `s2_architecture` и `TASK-002` съели 1.11M
   из 3.56M — это следующий кандидат на удешевление промпта.
4. **Артефакт, принятый на гейте, можно обновить только явным `re-record`.**
   Гейт по-прежнему замораживает хеш, но с 1.0.1 есть легальный путь:
   `factory artifact re-record --run-id ... --path ... --role ... --reason ...
   --evidence ...` пишет `ARTIFACT_RERECORD` с `old_sha256` и новым `sha256`,
   причиной и ссылкой на доказательство. Гейт это не переоткрывает
   (`stage_status`/`gate_status` не двигаются), `PASS` в этом событии
   запрещён, а любая правка после re-record снова `tampered`. Для
   `traceability` правило «план следует матрице» остаётся предпочтительным
   путём: re-record — для случая, когда правка уже случилась и её надо
   назвать, а не для «допишу потом».
5. **Проверка целостности привязана к последней записи.** Более поздняя
   сдача отклонённой задачи перекрывает хеш принятой: файл, оставшийся от
   отклонённой работы, не считается подменой. Это следует из «каждый байт
   на диске должен быть записан в журнале»; статус задачи при этом виден.
   `ARTIFACT_RERECORD` участвует в том же правиле: последняя запись на путь
   побеждает, но добавить её может только владелец и только с причиной.
6. **Песочница.** `bwrap` с read-only control plane, но тот же uid, сеть и
   запись в `~/.hermes/{installs,cache,logs}`. Отдельный пользователь/сервис —
   следующий шаг.
7. **Human-гейты в eval закрыты как `human:eval`.** Это замер, а не ревью;
   перед релизом гейты закрывает живой человек.
8. **Одна модель, один провайдер.** Числа не переносятся на другую модель;
   `--reasoning medium` этот провайдер отвергает HTTP 400.
9. **Закрыто в 1.0.1: ссылка на тест проверяет имя функции.** `build_matrix`
   требует, чтобы последний сегмент после `::` был объявлен в файле:
   `def`/`async def`/`class` для Python, `fn` с `#[test]`/`#[tokio::test]`
   для Rust. Ссылка без `::` по-прежнему проверяется только на существование
   файла. Проба на исторических прогонах показала, что расхождений больше,
   чем считалось: не только `nano-product-mini` (BUG-001, REQ-003), но и
   `nano-todo-20261030-191409` (REQ-001: два имени из двух не существуют) и
   `brownfield-proxy-mini-brownfield-real2` (REQ-003). Прогоны не правились:
   это история, а не регресс. `nano-product-mini` теперь честно показывает
   2/3 с сообщением, какой символ не найден.

## Что дальше

- **CI зелёный на `fdff070`:** run `41221412801`, 14/14 jobs — ubuntu/macOS/
  Windows × Python 3.10–3.13, eval и build. Матрица трёх ОС закрыта.
  Предыдущий прогон (`41214643319` на `1bbb8c8`) упал на одном джобе:
  `test_budget_exhaustion_stops_the_loop` на windows-latest 3.11 — 10-мс шаг
  округлялся до `0.000` с, бюджет не срабатывал, и цикл останавливался на
  человеческом гейте. Шаг в тесте поднят до 50 мс (`980a812`).
- **Fake-эталон перегенерирован 31.10:** `eval/baseline.json` содержит
  13/33 для `nano-todo` и 15/35 для `standard-mini` (значения из `aeeb148`;
  в старом тексте handoff было 14/33 и 16/35 — оно устарело до этой сессии),
  `prompt_chars` обновлены под новые промпты.
- **Все три режима на реальном продукте закрыты:** `nano` (`nano-product-mini`),
  `standard` (`standard-mini`, `product-mini`), `brownfield`
  (`brownfield-proxy-mini`). Это условие DESIGN.md §11 для 1.0.0.
- Следить за потолками `nano` 3.1M, `standard` 2.9M и `brownfield` 4.5M: если
  законный прогон подойдёт к ним ближе чем на 10 %, поднимать по новому
  замеру, а не на глаз.
- Добавить `re-record` для легитимного обновления принятого артефакта, если
  практика покажет, что правило «план следует матрице» слишком тесное.
  **Сделано в 1.0.1:** `factory artifact re-record` + событие
  `ARTIFACT_RERECORD` (docs/runtime.md §5.7).
- Сравнить зелёный brownfield с baseline через `factory compare` и
  зафиксировать дельту в CHANGELOG следующей версии. **Сделано:** дельта
  `fake2 → real2` — 17/17 шагов, 38/38 событий, 173 same / 44 changed /
  1 only left / 175 only right; запись в `## [Unreleased]`, разбор — в
  разделе «Стоимость brownfield».

## Решение по релизу

Потолки `nano` — **3.1M / 120 минут** по худшему из двух кейсов: 2 436 448
total на `nano-product-mini` (97,5 % прежних 2.5M) + 25 %; `standard` —
**2.9M / 120 минут** по худшему из двух замеров (2 284 755 на `product-mini`,
1 978 597 на `standard-mini`). Потолок `brownfield` пересчитан по первому
полному прогону: **3 560 791 total + 25 % → 4.5M**, 17,2 мин + 25 % →
**30 минут**. Прежние расчётные 1.8M / 480 минут не покрывали даже
`s2_architecture` (948k после шага 9).
