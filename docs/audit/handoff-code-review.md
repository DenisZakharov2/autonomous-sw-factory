# Handoff: результаты независимого код-ревью (Code Review)

**Дата:** 16 ноября 2026  
**Репозиторий:** /home/anony/Documents/Личное/Проект фабрика программного обеспечения/autonomous-sw-factory  
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

```
Продолжаем работу над фабрикой программного обеспечения:
/home/anony/Documents/Личное/Проект фабрика программного обеспечения/autonomous-sw-factory

Сначала прочитай:
  AGENTS.md                          — правила проекта (девять законов, границы ролей)
  docs/audit/handoff-code-review.md  — этот файл: состояние код-ревью и план работ
  docs/audit/fix-plan-1.0.14.md      — архив: закрытый план аудита рантайма (11 пунктов)
  docs/threat-model.md               — модель угроз: что какая граница закрывает

Задача: начать закрывать результаты независимого код-ревью (9 категорий).

Порядок работ (по приоритету аудитора):
1. ~~**mypy + ruff в CI**~~ — **закрыто.** Джоб `lint` (ruff `E4/E7/E9/F`,
   mypy strict на четырёх чистых модулях) гейтит `build`. Следующий шаг
   для mypy — расширять `files` на `checkpoint.py`, `journal.py`,
   `pipeline.py`, затем `engine`/`verify`.
2. **Разбить engine.py / verify.py** — главная боль поддерживаемости,
   следующая работа
   - engine.py (2405 строк) → factory/engine/ пакет
   - verify.py (820 строк) → factory/verify/ пакет
   - Публичный API `factory.engine` / `factory.verify` сохранить
     реэкспортом в `__init__.py`: тесты и рантайм импортируют оттуда.
   - Начать с кластера проверки (`verification_report` … `_task_deliverable_errors`):
     он уже вынесен в `factory/engine/verification.py`. Следующие срезы —
     `gates.py` (approval tokens, `gate_approve/reject`, `accept_risk`, `veto`)
     и `verify/doctor.py` (границы машины и `doctor_report`).
   - Добавить radon проверку в CI

3. **Структурированное логирование** — нужен для production-диагностики
   - Единый логгер, JSON формат, контекст run_id/stage/role/task_id

4. **Поднять покрытие security-модулей** — integration тесты уже есть
   - isolation.py, sandbox.py, root_helper.py, retrieval.py → ≥70%

5. **Bandit baseline + nosec** — убрать шум из CI

Работай атомарными коммитами, по одному на пункт, с тестом.
Перед коммитом — `pytest -q --timeout=180`.
mypy и ruff в CI уже зелёные — рефакторинг engine.py можно начинать.
```

---

## Грабли (из предыдущих циклов, актуальны и здесь)

1. **mypy на engine/verify выдаст сотни ошибок** — начинай с чистых модулей, включай постепенно
2. **Разбиение engine.py требует аккуратности с циклическими импортами** — планируй границы пакета заранее
3. **ruff --fix может сломать форматирование** — проверяй дифф перед коммитом
4. **Bandit baseline нужно коммитить** — иначе CI будет падать на известных шумовых находках
5. **Покрытие isolation/sandbox требует живой машины** — unit-тесты с моками только для логики, настоящие проверки в job integration

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