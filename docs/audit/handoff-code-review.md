# Handoff: результаты независимого код-ревью (Code Review)

**Дата:** 16 ноября 2026  
**Репозиторий:** <factory-repo>  
**GitHub:** https://github.com/DenisZakharov2/autonomous-sw-factory  
**Версия на момент ревью:** 1.0.14  
**Текущая ветка:** main (чистое дерево, синхронизировано с origin/main)

---

## Контекст

Независимый технический аудит (файл `code-review-autonomous-sw-factory (1).md`) выявил **9 категорий структурных и качественных проблем**. Они **не пересекаются** с закрытым планом `fix-plan-1.0.14.md` (тот закрывал дыры безопасности рантайма: TOCTOU, E2BIG, контекстный лимит, D-Bus, AST-разбор, лимиты в doctor).

Этот handoff — точка входа для следующей сессии: **начать работу над качеством кода и поддерживаемостью**.

---

## Состояние репозитория

| Показатель | Значение |
|------------|----------|
| Версия | 1.0.14 (релиз выпущен, тег v1.0.14) |
| Тесты | 481 passed (pytest -q --timeout=180) |
| CI | 16 job'ов: Ubuntu/macOS/Windows × 3.10–3.13, lint, eval, integration, build |
| Покрытие | ~77% общие, но security-модули 24–38% |
| engine | пакет: `__init__.py` 1926 строк, `verification.py` 501, `common.py` 32 |
| verify.py | 820 строк |

**Прогресс после ревью:** пункты 2 (mypy в CI) и 4 (ruff в CI) закрыты;
пункт 1 (разбиение) начат — кластер проверки вынесен из `engine.py` в
`factory/engine/verification.py`. Следующий срез — `gates.py` или
`verify/doctor.py`. Подробности — в разделе «Что уже сделано после ревью».

---

## Что НЕ сделано (все 9 пунктов код-ревью)

### 1. Высокая сложность модулей — **Приоритет: ВЫСОКИЙ**
- `factory/engine.py` → разбить на пакет `factory/engine/` (init, next, submit, gates, tasks, verification)
- `factory/verify.py` → разбить на пакет `factory/verify/` (integrity, policy, report, role_card)
- Добавить в CI проверку radon: `radon cc factory/ -nc -a | grep -E ' [DEF]' && exit 1`
- Цель: ни одна публичная функция > 150–200 строк, CC ≤ 15

### 2. Статическая типизация в CI — **Приоритет: ВЫСОКИЙ**
- Добавить `mypy>=1.10` в `pyproject.toml` `[project.optional-dependencies.dev]`
- Создать `mypy.ini` / `[tool.mypy]` с `strict = True`, `files = factory`
- Добавить job в `.github/workflows/ci.yml`: `mypy factory/`
- Начать с `paths.py`, `errors.py`, `state.py`, `config.py` — постепенно включать `engine`, `verify`

### 3. Структурированное логирование — **Приоритет: СРЕДНИЙ**
- Единый логгер, уровни, конфигурация через env:
  - `FACTORY_LOG_LEVEL=INFO`
  - `FACTORY_LOG_FORMAT=json`
- Логировать: journal writes (seq, event_type), gate decisions, sandbox start/stop, quarantine decisions, context/budget exceeded
- Runtime context: `run_id`, `stage`, `role`, `task_id`

### 4. Ruff в CI — **Приоритет: СРЕДНИЙ**
- Исправить найденное:
  - `factory/cli.py`: неиспользуемый `INDEX_DIR`
  - `factory/config.py`: неиспользуемый `import jsonschema`
  - `factory/engine.py`: неиспользуемый `policy_allows`
- `ruff check --fix factory/ tests/`
- Добавить job в CI + секцию `[tool.ruff]` в `pyproject.toml`

### 5. Bandit (безопасность) — **Приоритет: НИЗКИЙ**
- `factory/ui/data.py`: SHA1 → `hashlib.sha1(data, usedforsecurity=False).hexdigest()` или `blake2s`/`sha256`
- `factory/runtime/root_helper.py`: `chmod 0o750` → `# nosec B103`
- `factory/runtime/sandbox.py`: `--tmpfs /tmp` → `# nosec B108`
- Добавить `bandit -r factory/ -ll` в CI с baseline

### 6. Покрытие тестами security-модулей — **Приоритет: СРЕДНИЙ**
| Модуль | Покрытие | Цель |
|--------|----------|------|
| `runtime/isolation.py` | ~25% | ≥70% |
| `runtime/sandbox.py` | ~30% | ≥70% |
| `runtime/root_helper.py` | ~38% | ≥70% |
| `retrieval.py` | ~24% | ≥70% |
- Расширить integration-тесты (job `integration` уже есть bwrap + ACL)
- Добавить unit-тесты с моками для root_helper/isolation
- Документировать в `SECURITY.md` / `docs/runtime.md`, что проверяется только на живой машине

### 7. Широкие `except Exception` — **Приоритет: СРЕДНИЙ**
- Сузить до конкретных: `FactoryError`, `OSError`, `json.JSONDecodeError`, `subprocess.TimeoutExpired` и т.д.
- Где broad except осознанный — логировать + `# noqa: BLE001` с пояснением + тест
- Места: `engine.py`, `runtime/loop.py`, `runtime/evidence.py`, `runtime/worktree.py`, другие

### 8. Документация — **Приоритет: НИЗКИЙ**
- Добавить `docs/index.md` или `mkdocs.yml` — единый индекс
- В `CONTRIBUTING.md` зафиксировать политику языка: код/комментарии — английский, пользовательская документация — оба языка
- Добавить проверку битых ссылок в CI (`lychee` или `markdown-link-check`)

### 9. Дополнительно — **Приоритет: НИЗКИЙ**
- Lockfile: `uv.lock` или `pip-tools`
- Команда `factory migrate` для `state.json` при изменении схем

---

## Что УЖЕ сделано (пересечение с fix-plan-1.0.14)

| Пункт | Что исправлено |
|-------|----------------|
| 1.6 → UI cache | LRU с лимитом 128 записей в `factory/ui/data.py` |
| 1.5 → AST разбор | `ast.parse` вместо регулярок в `factory/trace.py` |
| 1.7 → Doctor limits | Проверки роли ≤ 4500, шаблоны ≤ 2200 в `factory/verify.py` |

## Что уже сделано после ревью

| Пункт ревью | Что сделано |
|-------------|-------------|
| 2. mypy в CI | `mypy>=1.10` в `[project.optional-dependencies.dev]`; `[tool.mypy]` со `strict = true` и `files` из четырёх чистых модулей (`paths.py`, `errors.py`, `state.py`, `config.py`); `follow_imports = "silent"`. Джоб `lint` в CI. Строгий режим нашёл переиспользование имени `key` в `config.py` и возвраты `Any` в `state.py` — оба исправлены. |
| 4. ruff в CI | `ruff>=0.6` в dev-зависимостях; `[tool.ruff]` с `E4/E7/E9/F`. Убраны пять мёртвых импортов (`INDEX_DIR`, `jsonschema`, `policy_allows`, `sandbox_env`, `FilterError`), четыре мёртвые переменные, три строки с `;` в fixture-продукте. `lint` гейтит `build`. |
| 1. разбиение engine | `factory/engine.py` → `factory/engine/`; кластер проверки (`verification_report`, `verification_is_stale`, `record_verification`, `_latest_judgement`, `_verdict_is_fresh` и ещё 12 функций) вынесен в `verification.py`, общие константы и `_model_for` — в `common.py`. Публичный API пакета сохранён реэкспортом; `tests/test_engine_layout.py` это фиксирует. |

Проверки после этих правок: `pytest` — 481 passed, `ruff check factory/ tests/` — чисто,
`mypy` — чисто. CI-ран 16 job'ов зелёный.

---

## Промпт для следующей сессии

Скопировать целиком в новую сессию:

````text
Продолжаем работу над фабрикой программного обеспечения:
<factory-repo>

Сначала прочитай в этом порядке:
  1. AGENTS.md — девять железных законов, границы ролей, запреты.
  2. docs/audit/handoff-code-review.md — этот файл: 9 категорий ревью,
     что уже сделано, что осталось, грабли.
  3. docs/audit/fix-plan-1.0.14.md — архив закрытого плана рантайма:
     его пункты 1.1–1.11 уже закрыты, переделывать нечего.
  4. docs/threat-model.md — что какая граница закрывает и что нет.
  5. pyproject.toml — [tool.ruff], [tool.mypy], dev-зависимости.
  6. .github/workflows/ci.yml — джоб lint и матрица тестов.

СОСТОЯНИЕ НА 17 НОЯБРЯ 2026 (проверено):
  - Версия 1.0.14, тег v1.0.14, релиз опубликован 16 ноября.
  - main = origin/main, рабочее дерево чистое, HEAD 425364b.
  - 481 тест проходит (pytest --timeout=180), ~5 минут.
  - CI: 16 job'ов зелёные — Ubuntu/macOS/Windows × 3.10–3.13, lint,
    eval, integration (живой bwrap + ACL + root-helper), build.
  - ruff и mypy уже в CI. Mypy strict на четырёх модулях: paths.py,
    errors.py, state.py, config.py (follow_imports = "silent").
  - engine.py стал пакетом factory/engine/: __init__.py 1926 строк,
    verification.py 501, common.py 32.

ЧТО УЖЕ СДЕЛАНО (не переделывать):
  - Пункт 2 ревью (mypy) и пункт 4 (ruff) закрыты; джоб lint гейтит build.
  - Пункт 1 начат: кластер проверки вынесен в engine/verification.py,
    общие константы и _model_for — в engine/common.py. Публичный API
    сохранён реэкспортом, tests/test_engine_layout.py это фиксирует.
  - 15 тестов к fix-plan-1.0.14 добавлены (пункты 1.1–1.9).
  - Исправлен импорт дашборда на Python 3.10/3.11 (f-string PEP 701).
  - Подробности — в таблицах «Что уже сделано» этого файла.

ЗАДАЧА ЭТОЙ СЕССИИ — продолжить пункт 1 (разбиение) и закрыть следующие
по приоритету аудитора. Один пункт = один атомарный коммит с тестом.

1. ВЫНЕСТИ GATES ИЗ factory/engine/__init__.py В
   factory/engine/gates.py.
   Inventory (сверить с текущим файлом, имена могли сдвинуться):
     approval_key_bytes, make_approval_token, _check_human_approval,
     _check_gate_approver, _check_stage_artifact_owners, gate_approve,
     gate_reject, accept_risk, stage_reset, task_reset, veto.
   - Из .common брать VERIFIER, _model_for, verification_policy; из
     .verification — verification_report, _record_verification_locked,
     _verdict_is_fresh, stage_declares_verification, _latest_judgement,
     _judged_entries, _write_verification.
   - Функции, которые остаются в __init__.py, но нужны gates
     (_require_current, _require_stage_status, _root_paths, _stage_entries,
     _reopen_stage_tasks, _reset_rework_chain, _blocked_reason — проверить
     анализатором), импортировать лениво внутри функции. Образец:
     _judged_entries в verification.py импортирует _stage_entries из
     пакета внутри себя — иначе пакет и модуль импортируют друг друга.
   - В __init__.py оставить реэкспорт каждого имени, которое импортируют
     factory/cli.py, factory/eval.py, factory/runtime/loop.py,
     factory/runtime/worktree.py и тесты. Источник истины:
       grep -rn "from ..engine import" factory/
       grep -rn "from factory.engine import" tests/
     Приватные имена, которые импортируются снаружи (_task_deliverable_errors),
     тоже реэкспортировать. Для ruff F401 пометить реэкспорт как
     "from .gates import name as name" или вести __all__.
   - tests/test_engine_layout.py расширить: gates-кластер — отдельный
     модуль, публичные имена на месте.
   - Проверка: полный pytest, ruff, mypy, python3.11 -m py_compile.

2. ВЫНЕСТИ DOCTOR ИЗ factory/verify.py В ПАКЕТ factory/verify/.
   Начать с самого замкнутого кластера — doctor.py:
     _transient_units_allowed, _has_passwordless_sudo,
     _legacy_rule_installed, _executor_boundary_errors,
     _executor_boundary_warnings, _sandbox_boundary_warnings,
     _run_root_acl_warnings, ROLE_CARD_LIMIT, TEMPLATE_LIMIT,
     _check_resource_limits, doctor_report.
   - factory/verify/__init__.py реэкспортирует verify_run, parse_role,
     validate_roles, doctor_report и остальные имена, которые уже
     импортируются снаружи. Тесты и factory/cli.py не должны меняться.
   - ГРАБЛИ: tests/test_verify.py monkeypatch-ит factory.verify.PACKAGE_ROOT,
     а _check_resource_limits читает его. Если функция переедет в
     verify/doctor.py, патч перестанет влиять и тест станет ложно-зелёным.
     Реши явно и напиши в коммите: либо оставь _check_resource_limits в
     __init__.py, либо обнови тесты на factory.verify.doctor.PACKAGE_ROOT
     и проверь, что тест падает без патча.
   - Дальше остальные кластеры verify.py (820 строк): roles.py,
     schemas.py, integrity.py, policy.py, run.py. Каждый срез — отдельный
     коммит; публичный API не ломать.
   - Тест на структуру, аналогичный tests/test_engine_layout.py.

3. ПУНКТ 3 РЕВЬЮ — СТРУКТУРИРОВАННОЕ ЛОГИРОВАНИЕ.
   - Один логгер; уровни через FACTORY_LOG_LEVEL, формат через
     FACTORY_LOG_FORMAT=json (по умолчанию человекочитаемый).
   - Контекст: run_id, stage, role, task_id — в каждой записи.
   - Логировать: записи журнала (seq, event_type), решения гейтов,
     старт/стоп песочницы, решения карантина, превышение контекста/бюджета.
   - Тест: JSON-строка парсится, поля на месте, уровень фильтруется,
     секреты (env, токены) в лог не попадают.

4. ПУНКТ 6 РЕВЬЮ — ПОКРЫТИЕ SECURITY-МОДУЛЕЙ ДО ≥70%.
   isolation.py, sandbox.py, root_helper.py, retrieval.py.
   - Unit-тесты с моками только для логики; живые проверки остаются в
     джобе integration, где есть bwrap, ACL и отдельный uid.
   - Мерить: python3 -m pytest --cov=factory.runtime.isolation
     --cov=factory.runtime.sandbox --cov=factory.runtime.root_helper
     --cov=factory.retrieval --cov-report=term-missing.
   - Не «поднимать покрытие» пустыми assert: каждый тест должен падать,
     если убрать проверяемую ветку.

5. ПУНКТ 5 РЕВЬЮ — BANDIT BASELINE + NOSEC.
   - bandit -r factory/ -ll, baseline в репозитории; known findings с
     обоснованным # nosec BXXX; джоб в CI.
   - SHA1 в ui/data.py заменить на usedforsecurity=False или sha256.

6. ПУНКТ 7 РЕВЬЮ — СУЗИТЬ except Exception.
   - engine/__init__.py, runtime/loop.py, runtime/evidence.py,
     runtime/worktree.py и другие места из отчёта.
   - Где broad except осознанный — # noqa: BLE001 и комментарий, что
     именно ловится и почему; тест на это поведение.

ПРАВИЛА РАБОТЫ:
  - Перед каждым коммитом: python3 -m pytest --timeout=180 (полный прогон,
    ~5 минут); python3 -m ruff check factory/ tests/; python3 -m mypy.
  - Python 3.10 — минимальная поддерживаемая версия: без PEP 701
    f-strings и синтаксиса 3.11+. Быстрая проверка:
    python3.11 -m py_compile <изменённые файлы>.
  - После пуша: gh run list --limit 2, затем
    gh run watch <run-id> --exit-status. Красный CI чинить до новой работы.
  - Журнал — только через factory CLI; state.json вручную не править;
    PASS пишет только 02-independent-verifier (AGENTS.md).
  - Не трогать исторические audit-записи; обновлять только
    docs/audit/handoff-code-review.md (этот файл — активный handoff).
  - pytest addopts уже содержит -q: второй -q съедает строку summary.

КОМАНДЫ ПРОВЕРКИ:
  python3 -m pytest --timeout=180 -rs | tail -3
  python3 -m ruff check factory/ tests/
  python3 -m mypy
  factory doctor
  gh run list --limit 2 && gh run watch <run-id> --exit-status

РЕЛИЗ 1.0.15 — только когда разбиение (аудит-пункт 1) и пункты 3, 5, 6, 7
закрыты или сознательно отложены с записью в handoff.
Конвенция (handoff-1.0.13.md, раздел 3.1):
  1. Обновить VERSION, factory/__init__.py, README.md, README.en.md,
     DESIGN.md, docs/runtime.md, docs/quickstart.md, tests/test_docs.py —
     все места, где стоит версия.
  2. Переименовать [Unreleased] в [1.0.15] — <дата>; проверить, что каждая
     запись описывает «было/стало», а не только «сделано».
  3. Коммит "Release 1.0.15: ...", пуш, тег v1.0.15,
     gh release create v1.0.15 --target main --title ... --notes ...
  4. Без явного решения человека релиз не публиковать.

ГРАБЛИ (проверены на практике):
  - Циклические импорты: пакет __init__ импортирует подмодули; подмодули
    импортируют пакет только внутри функций.
  - monkeypatch путей: tests/test_engine.py:323 патчит
    factory.engine._next_action. Если _next_action переедет, патч должен
    указывать на новый модуль — иначе тест пройдёт, ничего не проверив.
  - verification_is_stale и _task_deliverable_errors импортируются снаружи,
    record_verification — публичное имя; все три обязаны остаться на
    factory.engine.
  - ruff F401 в __init__.py: реэкспорт помечать "as name" или __all__,
    иначе lint покраснеет.
  - factory/runtime/fixtures/nano/product/src/todo.py — данные эталонного
    прогона, не код Фабрики; менять только если сломан сам fixture.
  - mypy strict нельзя включать на engine/verify целиком: сотни ошибок.
    Расширять files по одному модулю, начиная с checkpoint.py, journal.py,
    pipeline.py.
  - Тесты, которым нужна живая машина, пропускаются через _helper_ready();
    не превращать skip в pass.

DEFINITION OF DONE:
  - Полный pytest зелёный; ruff и mypy чисты; CI 16 job'ов зелёный.
  - Каждый коммит — один пункт, с тестом или с объяснением, почему тест
    структурный (реэкспорт, разбиение).
  - handoff-code-review.md обновлён: что закрыто, что осталось, новый HEAD,
    точное число тестов.
  - Рабочее дерево чистое, main = origin/main.
````

---

## Грабли (из предыдущих циклов, актуальны и здесь)

1. **mypy на engine/verify выдаст сотни ошибок** — начинай с чистых модулей, включай постепенно: `checkpoint.py`, `journal.py`, `pipeline.py`, затем `engine`/`verify`.
2. **Разбиение engine.py требует аккуратности с циклическими импортами** — пакет `__init__` импортирует подмодули, подмодули импортируют пакет только внутри функций. Образец — `_judged_entries` в `factory/engine/verification.py`.
3. **monkeypatch не прощает переездов** — `tests/test_engine.py:323` подменяет `factory.engine._next_action`, `tests/test_verify.py` подменяет `factory.verify.PACKAGE_ROOT`. Если функция или константа переехала, патч должен указывать на новый модуль; иначе тест зелёный, а проверка мертва.
4. **ruff --fix может сломать форматирование** — проверяй дифф перед коммитом; в `__init__.py` реэкспорт помечай `as name` или `__all__`, иначе F401.
5. **Bandit baseline нужно коммитить** — иначе CI будет падать на известных шумовых находках.
6. **Покрытие isolation/sandbox требует живой машины** — unit-тесты с моками только для логики, настоящие проверки в job integration.
7. **Python 3.10 — минимум** — PEP 701 f-strings (кавычки внутри выражения) и синтаксис 3.11+ ломают шесть матричных job'ов; перед коммитом `python3.11 -m py_compile` изменённых файлов.
8. **Тест, который «проходит» на сломанном коде, хуже отсутствующего** — после ослабления патча или переезда функции проверь, что тест падает без исправления.

---

## Файлы-ориентиры

| Файл | Назначение |
|------|------------|
| `code-review-autonomous-sw-factory (1).md` | Полный отчёт аудитора (в корне проекта / вложен) |
| `docs/audit/handoff-code-review.md` | Этот файл |
| `docs/audit/fix-plan-1.0.14.md` | Архив закрытого плана аудита рантайма |
| `AGENTS.md` | Правила проекта (читать перед работой) |
| `docs/threat-model.md` | Модель угроз |
| `SECURITY.md` | Безопасность (дописать про покрытие) |