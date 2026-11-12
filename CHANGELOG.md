# Changelog

All notable changes are recorded here. From `1.0.0` the project follows Semantic Versioning.

---

## [Unreleased]

Закрывает находку независимого аудита от 12 ноября 2026: гейт и `task accept` принимали вердикт, выданный **до** последней правки продюсера. Формально `PASS` писал верификатор, отчёт был валиден, цепочка хешей цела — но судя он был по коду, который продюсер потом переписал. Дыра не требовала никакой неверной конфигурации: она работала при полностью включённой песочнице.

### Исправлено
- **Вердикт должен быть про текущие байты.** События `VERIFY` и `REWORK` теперь записывают не только отчёт верификатора, но и хеши артефактов, которые он смотрел. `task accept` и `gate approve` отказывают, когда эти артефакты изменились после записи вердикта, а отчёт на диске — тот же самый: `the verifier's report ... predates the last submission`. Ответ берётся из журнала, а не из времени модификации файла (время файла принадлежит исполнителю и подделывается тривиально). Отчёт, написанный верификатором после изменения, — новый вердикт про новые байты и принимается; повторная сдача тех же байтов изменением не считается.
- **Отчёт стадии должен быть сдан.** Раньше гейт закрывался по файлу, который никто не сдавал: `factory next` предлагал гейт только для текущего (сданного) отчёта, а `gate approve` проверял только наличие файла. Теперь гейт требует того же, что и роутер: отчёт сдан верификатором и сдан после последней сдачи продюсером.
- **Отказ и сброс задачи записывают, на что смотрели.** `task reject` и `task reset` — это тоже решения по байтам, и теперь они их фиксируют. Это и есть путь, которым продюсер возвращает задачу себе: оба шага теперь оставляют след в журнале.
- **`factory verify` видит решение, которое лежит на старых байтах.** Проверка порядка в `verification_policy_errors` помнила только «был ли где-то вердикт `PASS`»: липкая запись не инвалидировалась, и прогон, закрытый по устаревшему вердикту, выглядел чистым. Теперь она сверяет хеши, которые верификатор смотрел, с хешами, которые приняло решение. Прогон, сделанный уязвимой версией фабрики, остаётся аудируемым: `factory verify` говорит, на каких байтах вердикт простарел.
- **Риск-приёмка больше не подменяет вердикт молча.** Стадия в статусе `review` ждёт вердикта, а не несёт риск: `gate accept-risk` на ней отказывает. Приёмка риска на ролевом гейте теперь обязана называть вердикт, который она перекрывает (`parent_event_id` — событие `VERIFY`), и `factory verify` сообщает, если перекрывать нечего. Человек может закрыть эскалированную стадию — но в журнале видно, чей вердикт он перекрыл и почему.
- **Отчёт верификатора защищён от продюсера.** Каталоги отчётов (`artifacts/verification/`, `tasks/*/verification/`) монтируются в песочницу только для чтения — поверх записываемого корня прогона. Раньше продюсер получал в запись родительский каталог своего артефакта, а вместе с ним и каталог отчёта: отчёт, написанный продюсером, ничем не отличался от отчёта верификатора. Роль, которой отчёт писать положено, своё право на запись сохраняет. Каталоги отчётов создаются заранее (`factory init`, импорт плана): защищать то, чего ещё нет, нельзя. Отчёт верификатора исключён из write-scope продюсера **всегда**, а не только когда отчёт валиден.
- **Запись движка больше не следует по symlink.** `runtime/evidence/` создавался лениво, поэтому на первом шаге его не было — и защищать его было нечем: исполнитель успевал создать каталог и подложить в него ссылку с именем, которое движок предсказуемо выберет следующим. Движок писал через неё — от имени оператора, в любой файл. Теперь `runtime/`, `runtime/agent/` и `runtime/evidence/` создаются на `factory init`, а записи движка в зоне, доступной исполнителю (evidence, метрики, отчёт задачи), идут через `safe_write_text`: ссылка удаляется, файл пишется заново, цель за пределами прогона не страдает.
- **Грант исполнителю больше не покрывает корень прогона.** Права на каталог — это права удалить и заменить его содержимое, а в корне лежит control plane. Прежний грант давал исполнителю `rwX` на всё дерево прогона и ставил default-ACL на корень: атомарная запись движка создавала новый inode `state.json`, который наследовал права исполнителя, — и отзыв доступа переставал работать ровно тогда, когда был нужен. Теперь грант идёт по рабочим каталогам (`artifacts/`, `tasks/`, `product/`, `runtime/agent/`), default-ACL ставится на них, а не на корне, и корень остаётся недоступен исполнителю на запись. `factory doctor` предупреждает, если на корне прогона всё же есть чужие права на запись или default-ACL.
- **Журнал не теряет commit из-за зеркала.** `AUDIT_TRAIL.md` — производное представление, но его сбой (нет места, права) стоял между записью события и сохранением `state.json`: событие было в журнале, а якорь — нет, и `factory verify` это видел. Теперь сбой зеркала не прерывает транзакцию: событие зафиксировано, якорь сдвинут, а в `state.json` появляется `mirror_error`. Зеркало перестраивается из журнала: `factory log audit --run-id <id> [--rebuild]`.
- **Запись строки журнала не может быть частичной.** `write(2)` вправе записать меньше, чем дали; прежний одиночный `os.write` оставлял в журнале обрывок строки, и всякое чтение журнала потом падало на невалидном JSON. Теперь запись идёт циклом до конца, а «записали ноль байтов» — ошибка.
- **Зеркало журнала дописывается, а не перерисовывается.** Перерисовка всего журнала на каждом событии — квадратичная по длине прогона: на тысяче событий это миллион разборов JSON. Теперь зеркало получает одну строку за событие.
- **Якорь состояния стал таким же долговечным, как журнал.** `state.json` переписывается атомарно, но без `fsync`; журнал свой `fsync` делает. Теперь fsync есть и у файла, и у каталога.
- **`--no-sandbox` вместе с `--executor-user` требует явного флага.** Без песочницы отдельный пользователь — единственная граница, и она не защищает control plane: исполнитель может писать журнал и закрывать свои гейты. Предупреждение в stderr легко потерять в логе, поэтому такая комбинация теперь отказывает, пока оператор не скажет `--allow-unsafe-no-sandbox`. `factory doctor` называет случай, когда песочница не работает вовсе (нет `bubblewrap` или запрещён user namespace): тогда граница исполнителя держится на одном tripwire. `docs/threat-model.md` §3.0 — таблица «слой → что закрывает → что не закрывает»: слои не взаимозаменяемы.

### Документация
- `PIPELINE.md` §4 и §5 — правило свежести вердикта и требование сдать отчёт стадии; `docs/runtime.md` §5.10 — почему ответ берётся из хешей, а не из времени файла.

---

## [1.0.12] — 2026-11-11

Закрывает находки независимого аудита от 10 ноября 2026 (ревизия `d40148d`): polkit-правило давало оператору root без пароля, чужой чекпоинт проходил как подписанный, токен человеческого гейта был бессрочным и годился и на приёмку, и на отказ.

### Исправлено
- **Root без пароля через polkit убран.** Правило `50-factory-transient-units.rules` разрешало оператору любой транзиентный юнит `run-*` / `factory-*`, а `systemd-run --system` без `--unit` называется именно `run-*` — любой процесс оператора, включая агента вне песочницы, получал root без пароля. Теперь единственная привилегированная программа — `/usr/local/libexec/factory-root-helper`: фиксированный список подкоманд (`ping`, `install-policy`, `retire-legacy-polkit`, `ensure-user`, `ensure-env-root`, `install-env`, `remove-env`, `remove-pid`, `stop`, `run-as`), никакого shell, никакого произвольного argv. Helper устанавливается один раз (`factory setup-executor` печатает одну команду `sudo install`) и ставит polkit-правило только на себя. `factory doctor` сообщает, если старое правило ещё лежит на диске.
- **Helper не исполняет код оператора.** Установленная программа — самодостаточная копия модуля без `import factory`, с shebang на системном интерпретаторе, принадлежащем root. Прежняя схема (shebang на `sys.executable` оператора и импорт пакета) давала правку файла в домашнем каталоге — то есть root без пароля.
- **Чужой чекпоинт больше не выглядит подписанным.** `verify` отклоняет store, чей `run_id` не совпадает с каталогом прогона (статус `foreign`), и подписывает поля записи, а не обёртку документа.
- **Токен человеческого гейта привязан к решению и к сроку.** `FACTORY_APPROVAL_KEY` — не менее 32 байт; токен `exp.HMAC(key, run|subject|approver|action|exp)` живёт не дольше 24 часов и годится ровно для одного действия (`approve`, `reject`, `accept-risk`, `reset`, `task-reset`). Минтит `factory approval-token`.
- **Журнал не следует по symlink.** Добавка в `blackbox.jsonl` открывается с `O_NOFOLLOW`.
- **Сиды на Python без `filter="data"`.** На 3.10.0–3.10.11 и 3.11.0–3.11.3 (до бэкпорта CVE-2007-4559) распаковка больше не идёт «как есть»: ссылки и спецфайлы отклоняются, биты режима, включая setuid, снимаются.
- **Фильтр стадий в дашборде** заполняется стадиями, а не типами событий.
- **Control plane вне reach исполнителя.** ACL-грант исполнителю больше не покрывает `state.json`, журнал, `checkpoint.json`, `runtime/usage.jsonl`, `runtime/evidence` и `checks`.
- **Тесты границы доверия проверяют тот механизм, который в продукте.** Раньше они вызывали `systemd-run` напрямую и пропускали себя, когда он есть; теперь идут через `pkexec` + helper.
- **Идентичность исполнителя.** `USER`, `LOGNAME` и `SHELL` в окружении исполнителя — его, а не оператора; pid-файлы шагов убираются.
- **Собранный пакет версионирован правильно.** `pyproject.toml` объявлял статическую версию `0.0.0` рядом с `[tool.setuptools.dynamic]`, и setuptools брал именно её: wheel и sdist выходили как `autonomous_sw_factory-0.0.0`, хотя пакет внутри отвечал `factory 1.0.12`. Теперь версия объявлена динамической и берётся из файла `VERSION`; CI проверяет имя собранного wheel'а.
- **Проверка старой границы не падает у непривилегированного пользователя.** Каталог правил polkit — `0750 root:polkitd`, и `Path.is_file()` на `PermissionError` поднимает исключение, а не возвращает `False`: `factory doctor` падал на любом Linux без root. Проверка спрашивает polkit напрямую (транзиентный юнит запускается без пароля — значит root доступен) и сообщает об этом **предупреждением**: на CI-раннере root без пароля выдан через `sudo` осознанно, и это не наша дыра. Предупреждение срабатывает, только если `sudo -n` не проходит, а юнит запускается.
- **Пути helper'а проверяются как POSIX.** На Windows `Path("/run/...").is_absolute()` ложно, и валидация отвергала любой путь; теперь сравнение идёт через `PurePosixPath` — так helper работает на единственной платформе, где он живёт.

### Документация
- `docs/runtime.md` §6.1 описывает новую границу и единственную команду с `sudo`; `docs/threat-model.md` §3.1 — что граница исполнителя закрывает и что нет (сеть остаётся открытой: агенту нужен API модели); `SECURITY.md` — новая привилегированная граница.
- `README.md` больше не обещает «полную автономность» — `tests/test_docs.py::test_readme_does_not_claim_full_autonomy` снова зелёный после рерайта README в 1.0.11.

---

## [1.0.11] — 2026-11-08

Запись пропущена при выпуске; собрана здесь по коммитам между `28b8fd0` и `d40148d`.

### Исправлено
- **Пакет собирался без ресурсов.** `roles/*`, `memory/*`, `ui/static/*`, `templates/**/*`, `runtime/fixtures/**/*` не попадали в `.whl` — после `pip install` фабрика ломалась. Версия пакета теперь берётся из файла `VERSION`, а не дублируется в `pyproject.toml`.
- **Симлинки в worktree-merge.** Ссылка в дереве могла увести `merge()` за пределы разрешённых путей и подставить содержимое приватного файла в прогон.
- **Изоляция исполнителя.** Shell-инъекция в `ensure_env_root`, TOCTOU на staging-файле env и подмена оператора через `LOGNAME` — устранены.
- **Песочница.** Исправлена перевёрнутая проверка родительских путей; `CONTROL_PLANE_FILES`/`CONTROL_PLANE_DIRS` добавлены в запрет на монтирование.
- **Чекпоинты.** Исправлены статусы `stale`/`diverged` и `KeyError: 'index'`.
- **Токены и бюджет.** Вес кириллицы в оценке контекста, `context_window` вместо ручной константы, `None` в бюджете больше не обнуляет счётчик.
- **Карантин.** `verify_lock` допускает `MEDIUM` при человеческом одобрении; порядок веток в `decide()` исправлен.
- **RAG.** Исправлены токенизатор, индексация строк и кэш между проектами.
- **Дашборд.** Formula Injection в экспорте CSV, графики usage, ETag и срез хвоста списка.
- **Бюджет enterprise** приведён к 15M токенов в `PIPELINE.md`.

### Инфраструктура
- **CI** гоняет рабочие кейсы eval с fake-исполнителем, ставит Rust для продуктовых тестов, добавляет `--baseline` и проверяет собранный wheel в чистом окружении.
- **README переписан** для неспециалистов: честные ограничения вверху, технические детали вынесены в `TECHNICAL.md`.

---

## [1.0.10] — 2026-11-06

### Исправлено
- **`fixes.patch` убран из корня** — сырой дифф аудита 1.0.9, продублированный внутри коммита 53b3e21; не является продуктом.
- **Ссылки на архивный handoff.md починили** — `DESIGN.md`, `TECHNICAL.md`, `docs/runtime.md`, `factory/trace.py` теперь указывают на `docs/audit/handoff-1.0.9.md`; `docs/audit/README.md` описывает архив по конвенции каталога. `CHANGELOG.md` не тронут — историческая запись.
- **Flaky-тест `test_doctor_ignores_a_foreign_project_version` теперь стабильно зелёный** — падал только при полном прогоне из-за загрязнения CWD другими тестами; в чистом окружении и при полном прогоне 409/409 passed.

---

## [1.0.9] — 2026-11-04

### Что стало лучше
- **Контекст не обрезается втихую** — перед `executor.execute()` рантайм считает размер промпта (собственные артефакты + RAG-фрагменты) и сравнивает с окном модели из `factory/models.yaml` (`max_tokens × 4 символа × 0.75`). При превышении прогон останавливается с `context_exceeded`, а не подаёт обрезанное вслепую сообщение. Роль никогда не получает обрезанный контекст: задача требует дробить на подзадачи.
- **Документация** — раздел в `README.md`, принцип №8 в `DESIGN.md`, техническое описание §5.3.1 в `docs/runtime.md` с лимитами по тирам (tier-1: 24 576, tier-2: 98 304, tier-3: 393 216 символов).
- **Регрессионный тест** — `gate_reject` при `rework_to == stage_id` возвращает продюсеру `produce` с `feedback` верификатора вместо эскалации (`tests/test_v012_regressions.py`).

---

## [1.0.8] — 2026-11-03

### Что стало лучше
- **Тема оформления: тёмная, светлая или как в системе** — кнопка в шапке дашборда, выбор запоминается, при загрузке страница не мигает чужой темой.
- **Управление с клавиатуры** — `j`/`k` листают события журнала и строки прогонов, `Enter` открывает выбранное, `/` — поиск, `?` — справка по всем клавишам, `g g`/`G` — в начало и конец журнала, `r` — обновить данные.
- **Журнал не тормозит на длинных прогонах** — события догружаются по SSE, а рисуется только видимое окно: 10 000 событий открываются так же быстро, как сто. Текстовый фильтр журнала ищет по всему журналу, а не по последним 50 строкам.

### Исправлено
- Фильтры журнала и переключатель бакета в time-series падали: вызывали несуществующий `loadRun()`.
- README честно говорит: фабрика не полностью автономная — решение на каждом гейте принимает человек.
- `TECHNICAL.md` попал в sdist (ссылка из README.en.md больше не бьётся), версия пакета в `pyproject.toml` догнала реальную.

---

## [1.0.7] — 2026-11-02

**M4: Artifact diff viewer + drawer fix**

### What's better for you
- **Compare artifacts side by side** — open `?compare=<a>,<b>&artifact=spec` to see a line-by-line diff of `spec.md`, `acceptance.md`, or `plan.md` between two runs. Three tabs, color-coded (red for removed, green for added), honest "not created" message when an artifact doesn't exist. State survives reload.
- **Drawer actually closes** — the ✕ button and clicking the backdrop now work reliably. Esc key still works too.

### Technical details
- New endpoint `GET /api/compare/artifact` with `difflib`-based diff, ETag caching, full validation
- Panel cp6 "Артефакты (A vs B)" on compare page with three-tab selector
- Fixed drawer close wiring (moved into `DOMContentLoaded` so elements exist)

### Quality
- Page size: 113 KB (well under 250 KB limit)
- `factory doctor` — OK
- 398 tests pass (390 baseline + 8 new)
- CI: 14/14 jobs green on ubuntu/macos/windows × Python 3.10–3.13

---

## [1.0.6] — 2026-11-02

**M3: Time-series metrics on the dashboard**

### What's better for you
- **See spend over time** — new "Time-series" panel (P9) with four charts: Tokens, Seconds, USD, API calls. No external libraries, pure canvas/SVG. Switch metrics via tabs, choose bucket (stage / event / minute), state saved in URL.
- **Compare runs visually** — time-series data available for comparison view too.

### Technical details
- New endpoint `GET /api/runs/<id>/timeseries` with metrics, bucket params, ETag by journal stamp + bucket
- 9 new tests in `tests/test_ui.py`: endpoint validation, ETag/304, panel presence, URL params, error cases
- Page size ≤ 250 KB (actually ~101 KB), zero external URLs
- "Audit" preset now includes Time-series panel

### Fixed
- 400/404 errors return JSON with `error` field and allowed values list

---

## [1.0.5] — 2026-11-02

**The dashboard finally feels complete**

### What was wrong
The journal on screen was just "last 50 events" with no filters, no counter. Run list couldn't search or sort. Clicking a stage/task/event gave no details. CSV export ignored screen filters. Version 1.0.4 added the metrics panel but left these gaps.

### What's better for you
- **Journal filters that actually work** — filter by verdict, stage, event_type, limit (10–200). State lives in URL (`?verdict=&stage=&event_type=&limit=`), survives reload, passes to polling. Honest counter: "shown / total / filtered". Reset button returns defaults.
- **Find runs fast** — client-side search (`q`) and sort by column headers (`?q=&sort=&dir=`) without page reload.
- **See details instantly** — click any stage, task, or event to open a 420px right-side drawer with full details. Deep link `?event=<seq>` restores it. Esc, ✕, backdrop click close and return focus.
- **Export what you see** — `journal.csv` export now respects the same filters. Format: UTF-8 BOM, `;`, CRLF so Excel in Russian locale opens Cyrillic correctly.
- **Event API** — `GET /api/runs/<id>/events/<seq>` returns full event (validation, 404 if missing), cached by journal stamp.
- **Richer payload** — stages now include `gate_evidence`, `gate_decided_by`, `gate_decided_at`, `gate_approver`; tasks include `artifacts`.

### Technical details
- 41 tests in `tests/test_ui.py`, including `test_journal_filter_hides_fail_outside_the_tail` (117 events, FAIL outside the 50-event tail)
- ETag now includes filter hash (`_filters_hash`)
- Cache key: `(blackbox.jsonl stamp, filters, limit)`

---

## [1.0.4] — 2026-11-01

**Metrics panel + run list that doesn't break the page**

### What was wrong
Run metrics were computed on every cold request but only visible as a snippet in the traceability footer. Both READMEs claimed otherwise. Meanwhile the run list table was 1384px wide on a 1280px viewport, forcing horizontal scroll on the entire page.

### What's better for you
- **Dedicated Metrics panel (P8)** — nine values from `compute_metrics`: first pass rate, rework rate, escaped defects, cycle time p50/p90, artifact cost, top reject reasons (up to 3 "code × N" pairs), verification coverage, traceability coverage. Percentages without decimals, minutes with one decimal, USD with two; `null` = "not measured", calc error = warnbox. Visible in "Audit" preset, in "Operator" and "FinOps" — in catalog but hidden; default size 6×3.
- **"See a run" block in README** — install, `factory ui --runs-dir runs --open`, address `http://127.0.0.1:8420`, one line explaining the dashboard is read-only. Repo description updated, added `dashboard` topic.

### Fixed
- **Run list no longer stretches the page** — table lives in `overflow-x:auto` container; `body` doesn't demand more width than viewport. At 1280px no page-level horizontal scroll, columns don't hide or truncate.
- **`factory ui --runs-dir` actually works** — `cmd_ui` was passing `_runs_root(args)` but `ui` command has no such field; flag was silently ignored and server opened `./runs`.
- **Old layout doesn't auto-open new panel** — `sanitizeLayout` appends missing IDs to end and marks them `hidden`; layout reset returns preset.
- **READMEs no longer claim `compute_metrics` isn't called** — it runs in every cold payload, cached by `state.json`, journal, spec, acceptance, traceability, tests; panel shows all nine values. Panel count — eight including header.

### Measurements

| What | Before | After |
|---|---|---|
| `documentElement.scrollWidth` at 1280 | 1408 | 1270 = `clientWidth`; with hidden scrollbars 1280/1280 |
| Table / container width | 1384 / 1232 | 1384 / 1222, scrolls inside |
| `compute_metrics`, 117 events | 338–359 ms | 315–346 ms (code unchanged) |
| Cold payload | 685–807 ms | 680–748 ms |
| `index.html` size | 62,838 B | 65,414 B, zero external URLs |

- `pytest` — 357 passed
- `factory doctor` — OK
- `factory verify`, `trace`, `metrics`, `log verify` on `examples/nano-todo` — OK

---

## [1.0.3] — 2026-11-01

**Run screen + data export**

### What's new
- **`factory ui` — local read-only dashboard** — `ThreadingHTTPServer` from stdlib, `GET` only: `POST`/`PUT`/`DELETE` get `405`. Run list reads `state.json` and `idea.md` without bundle; run screen builds seven panels (header, 13 stages with real gate IDs, "now", tasks, spend, `REQ` traceability, journal last 50 events) from core read functions. Single file in package: 12-column grid, drag by header, resize handle, panel catalog, presets "Operator / FinOps / Audit / Custom", layout in `localStorage`. Polling every 2s on visible tab, `revision` and `304`; `verify_chain` only by button with cache by `(mtime, size)`. Listens on `127.0.0.1`, not exposed to network, doesn't read `FACTORY_APPROVAL_KEY`, writes zero bytes to `runs/`.

- **Export M1: CSV and `.xlsx`** — "Export ▾" menu in header serves `journal.csv` (full journal, not 50-event screen tail), `tasks.csv`, `usage.csv?by=stage|role|task`, `trace.csv`, book `<run_id>-<date>.xlsx` with six sheets ("Run", "Stages", "Tasks", "Journal", "Spend", "Traceability") and `runs.csv` for all runs. Rows built by same read functions as panels, so file can't diverge from screen. URLs: `/api/export/runs.csv`, `/api/runs/<id>/export/{journal,tasks,usage,trace}.csv`, `/api/runs/<id>/export.xlsx`.

- **CSV format for Russian Excel** — UTF-8 with BOM, `;` delimiter, CRLF. Without BOM and CRLF Excel shows Cyrillic as mojibake; `null` = empty cell, `0` stays `0`. RFC 4180 with comma rejected deliberately.

- **`openpyxl` as extra only** — `pip install autonomous-sw-factory[xlsx]`; lazy import, without it book endpoint returns `501` with hint, doesn't crash. Runtime dependencies unchanged.

### Fixed
- **Stage rejection reopens its tasks** — `gate_reject` at `rework_to == stage_id` returns `s4_build` tasks from `accepted`, `review`, `in_progress` to `pending` and clears lease; `rework_to != stage_id` branch (`s5_acceptance`, `s6_security` → `s4_build`) unchanged.
- **Stage doesn't go to gate with stale verdict** — in `review` next step is verifier if report written before last producer submit (`_report_is_current`).
- **`FAIL` reason reaches producer** — `_feedback` reads last `VERIFY FAIL` by task and stage from journal, order carries `feedback`, `build_prompt` prints it in "Previous review notes" section.
- **Test names from `traceability.json` reach build** (`declared_tests`), and `s3_plan` rejects plan where declared test not in `Allowed Files` of any task.

### Measurements (1 Nov 2026, `runs/nano-todo-verify-role-real`, 117 events)

| What | Size | Time (warm server) |
|---|---|---|
| `journal.csv`, 117 rows | 29,288 B | 11–12 ms |
| `tasks.csv`, 2 rows | 449 B | 11 ms |
| `usage.csv`, 8 stages | 629 B | 9 ms |
| `trace.csv`, 3 REQ | 431 B | 4 ms |
| `runs.csv`, 87 runs | 15,263 B | 36 ms |
| `<run_id>.xlsx`, 6 sheets | 22,386 B | 105–130 ms |

100,000 events (synthetic 45 MB journal): table 3.18s, CSV 3.33s / 25.7 MiB, book 52.6s / 4.1 MiB, peak RSS 595 MiB — book on hundred-thousand rows is expensive due to openpyxl, CSV remains cheap path.

Header at 1280px stays single line: 1260px used, `scrollWidth == clientWidth`, no wrap (measured in headless Chromium, not by eye).

### Update
```bash
pip install -U autonomous-sw-factory            # dashboard without .xlsx
pip install -U "autonomous-sw-factory[xlsx]"   # plus .xlsx book
```

`VERSION` 1.0.3, tag `v1.0.3`. Tests: 353 (was 341): 12 new in `tests/test_ui.py`, no existing test weakened. CI — 14/14 on Ubuntu/macOS/Windows × Python 3.10–3.13, including sdist build and install in clean env without `openpyxl`.

---

## [1.0.2] — 2026-11-01

**Independent verifier becomes a real runtime step, requirements become code**

### What's new
- **Independent verifier — real step, not a signature** — role `02-independent-verifier` now executes before each of seven role-gates (`s15_quarantine`, `s2_architecture`, `s3_plan`, `s4_build`, `s5_acceptance`, `s6_security`, `s8_docs`) and before every `TASK_DONE`. Report `artifacts/verification/<stage>/verification.md` (or `tasks/<task_id>/verification/verification.md`) must contain three adversarial checks with command and actual output, and one `PASS`/`FAIL` verdict; template choice `PASS | FAIL` counts as invalid. `VERIFY` event written only by `02`, `VERIFY` enters `ARTIFACT_RECORDS`, and `factory verify` requires last `VERIFY PASS` before `GATE_DECISION` and `TASK_DONE`. `FAIL` verdict returns stage or task to rework.

- **`verification_policy: "role"` in `state.json`** — new runs enable the check; schema field optional. Historical runs including `examples/nano-todo` not rewritten and pass `factory verify` as before. For them verification report artifact doesn't exist (`requires_policy`).

- **`--fake-scenario verification-fail`** — first report per item gets `FAIL`, then `PASS`: rework cycle test without model.

- **`--fake-scenario verification-invalid`** — first report per item leaves template choice `PASS | FAIL`, then `PASS`: step repeats, run converges, `runtime/evidence/` gets `report_invalid` record.

- **Evidence `report_invalid` for invalid report** — `_complete_verification` returns validation errors, runtime writes step `{"status": "report_invalid", "errors": [...], "report": "<path>"}` via same mechanism as other steps. Before, retry after invalid report was only visible in executor transcript, not in audit. Valid report still writes nothing extra.

- **`factory usage --run-id ID [--by stage|task|role] [--json]`** — read-only report from `runs/<id>/runtime/usage.jsonl`. Fields: `steps`, `api_calls`, `prompt_chars`, `tokens_in`, `cache_read`, `tokens_out`, `tokens`, `tokens_billable`, `seconds`, `usd`. Stages in `pipeline.yaml` order, `--by task` puts taskless rows in `-`, `--by role` sorts roles. `null` doesn't become zero: if record lacks field, field total is `null`. Broken JSONL line skipped; empty or missing ledger gives zeros and stderr warning; unknown `run_id` → exit `1`. `usd` at `0.0`/`null` labeled "usd not measured", not presented as price. Docs — `docs/runtime.md` §5.8 and `docs/metrics.md`.

### Changed
- **"Deterministic check" in stage report becomes code rule** — `validate_verification_report(require_deterministic_check=True)` requires at least one `### Check N` block with `- Command:` containing `verify`, i.e. report relies on `factory verify --run-id <id> --stage <stage>`; otherwise report invalid and step repeats. Before this was only a prompt paragraph. For task report rule doesn't apply (`require_deterministic_check=False`): `factory verify` has no task scope, deterministic proof there is check command from `plan.md`. Measured on nine real reports `runs/nano-todo-verify-role-clean`: `factory verify` present in 7/9 (7/7 stage, 0/2 task), after change valid 9/9; stage report without such check invalid, test exists.

- **`s5_acceptance` prompt requires literal `PASS`** — both real runs got rework on `s5_acceptance`: producer wrote "passed by test", `validate_acceptance` rejected stage. Now stage prompt has "Acceptance matrix rules" block: in `Status` column — literal `PASS`, template intentionally leaves `PENDING`, pre-filling `PASS` before verification forbidden.

- **Verifier prompt requires single word in verdict** — two of eleven steps in green run were retries after invalid report: model left template choice `PASS | FAIL`. Verifier assignment (stage and task) now says: in `- Verdict:` line — exactly one word, `PASS` or `FAIL`; leftover choice invalid and step repeats. `verification_verdict` unchanged — it already rejected ambiguity.

- **Task verifier assignment no longer promises `factory verify`** — command has no task scope, so "one check is `factory verify` for stage" replaced with "task check command from `plan.md`": nine real reports showed verifier worked exactly that way.

- **`task_accept` no longer writes stub `verification.md` in new runs** — without valid report with `PASS` it fails (`VerificationError`), and CLI `factory task accept` too. Legacy stub remains only for runs without `verification_policy`.

- **`gate_approve` from role-approver checks report and writes `VERIFY` before `GATE_DECISION`; `FAIL` → reject, gate doesn't close.**

- **Fake reference regenerated:** `nano-todo` 13/33 → 22/48, `standard-mini` 15/35 → 24/50 (steps/events). `eval/baseline.json` updated after green fake run; `eval/baseline-hermes.json` untouched.

- **Budgets `nano`, `standard`, `brownfield` remeasured after verifier** — rule: worst green real measurement × 1.25, round up. `nano`: 5.2M → **7.8M** (6.2M, `nano-product-mini`, 22 steps); `standard`: 2.9M → **9.5M** (7.6M, `product-mini`, 28 steps); `brownfield`: 4.5M → **9.1M** tokens and 30 → **60** min (7.3M, 28 steps, 44.2 min). `minutes` for `nano` and `standard` unchanged: calculated 50 and 75 min below active 120. `enterprise` not measured (no case) and unchanged — now 6M below measured `standard` need. `usd` nowhere changed: provider doesn't return price (`usd` = 0.0, "not measured").

- **`eval/baseline-hermes.json` not rewritten: real run of five cases gave 2 green of 5.** `brownfield-proxy-mini` (28 steps / 53 events / 7.3M) and `product-mini` (28 / 50 / 7.6M) green; `nano-todo` and `nano-product-mini` failed `REQ` coverage in `traceability.json` (references tests not in declared files), `standard-mini` escalated on `s4_build`: verifier found tests don't run with case's canonical command without `PYTHONPATH`, and `gate_reject` at `rework_to == stage_id` doesn't reopen tasks — three checks of same files and escalation. `write_baseline` refuses to write baseline from failing cases, so file stayed same.

- **Brownfield `fake2` → `real2` delta recorded.** `factory compare`: steps 17/17, events 38/38, artifacts 173 same / 44 changed / 1 only left / 175 only right. Cost breakdown by stages and tasks, top-5 drivers, three cost-reduction candidates — in `handoff.md`, "Brownfield cost" section. Prompts in this session unchanged: cheapening effect without real run not proven.

### Verified
- CI run `41282195622` on `d9f75e5` — 14/14 jobs (ubuntu/macOS/Windows × Python 3.10–3.13, eval, build). CI on release commit — also 14/14; run number in `v1.0.2` release description.
- Locally: 311 tests, `factory doctor` — OK, `tests/test_docs.py` — 9 passed, fake eval `nano-todo` 22/48 and `standard-mini` 24/50 — `match`, `examples/nano-todo`: `VERIFY OK`, `trace` 3/3, `log verify` CHAIN OK, product tests 3 OK.
- Real run of five eval cases on Hermes: 2 of 5 green (`brownfield-proxy-mini`, `product-mini`); `nano-todo` and `nano-product-mini` red by `REQ` coverage in `traceability.json`, `standard-mini` escalated on `s4_build`. `eval/baseline-hermes.json` therefore not rewritten — recorded, not covered up.

---

## [1.0.1] — 2026-10-31

- `traceability` checks test function name, not just file
- Legal `re-record` of accepted artifact (`ARTIFACT_RERECORD`, §5.7 `docs/runtime.md`)
- New human-readable factory description in `README.md`/`README.en.md`
- CI run `41221412801` (14/14)

---

## [1.0.0] — 2026-10-31

First stable release. Full `nano`, `standard`, `brownfield` on real product (`nano-product-mini`, `product-mini`, `brownfield-proxy-mini`), five records in `eval/baseline-hermes.json`, mode ceilings from measurements, green CI on ubuntu/macOS/Windows (run `41215608643`, 14/14).

---

## Older Versions

- `0.3.0` — Signed checkpoints, eval set, run comparison
- `0.2.1` — CI fix: sandbox tests on runners
- `0.2.0` — Executable runtime: work orders in, verified artifacts out
- `0.1.2` — Audit fixes: transactional core and artifact integrity
- `0.1.1` — Audit fixes
- `0.1.0-preview` — Initial preview