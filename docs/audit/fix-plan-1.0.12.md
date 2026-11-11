# План исправления по независимому аудиту Grok (1.0.11 → 1.0.12)

**Дата:** 10 ноября 2026
**Репозиторий:** https://github.com/DenisZakharov2/autonomous-sw-factory
**Локально:** `/home/anony/Documents/Личное/Проект фабрика программного обеспечения/autonomous-sw-factory`
**HEAD:** `d40148d` (совпадает с `origin/main`; аудит сделан по этой же ревизии)
**Материалы аудита:** `Grok/GrokAudit1.md` (35 строк), `Grok/Patch/autonomous-sw-factory-security.patch` (1558 строк),
`Grok/Patch/autonomous-sw-factory-security.md` (тот же diff + шапка; `/home/anony/Downloads/autonomous-sw-factory-security.md` — байт-в-байт тот же файл)

**Подход:** Ponytail. Чужие правки не переписываются без нужды: из патча Grok берём то, что корректно, и
переписываем то, что переоткрывает дыру или ломает функциональность. Каждый шаг — атомарный коммит + проверка.

---

## 0. Что уже проверено (методика)

| Проверка | Результат |
| :--- | :--- |
| `git apply --check` патча на `d40148d` | проходит без конфликтов (20 файлов) |
| `pytest -q` на пропатченной копии | 418 тестов, 1 падение — **предсуществующее**, не связано с патчем (см. D-12) |
| 10 новых тестов патча (`test_root_helper`, `test_checkpoint`, `test_journal`, `test_eval_seed`, `test_v012_regressions`) | зелёные |
| PoC «чужой checkpoint» | **воспроизведён**: на 1.0.11 `status='signed'`, `verify errors=[]`; на патче `status='foreign'` |
| PoC «токен гейта» | **воспроизведён**: на 1.0.11 approve-токен принимается для `gate reject`, ключ 8 байт принимается |
| PoC «имя pid-файла» | **воспроизведён**: `prepare(['stop', 'factory-executor', '/run/factory-executor/run-01-s00_intake.pid'])` → `HelperError` |

Копия с патчем для проверки: `/tmp/opencode/aswf-patch-test` (репозиторий не изменялся).

---

## 1. Вердикт по отчёту аудита

Отчёт **корректен**: ни одного ложного утверждения не найдено. Все пять пунктов таблицы «Сначала поправить»
и все семь «мелочей» подтверждаются кодом. Серьёзность оценена адекватно, PoC для polkit воспроизводим
по существу (`systemd-run --system` без `--unit` даёт `run-*.service`, а правило разрешает `manage-units`
для `run-*`/`factory-*` → `polkit.Result.YES` → root без пароля любому процессу оператора).

| ID | Находка | Подтверждена | Уточнение |
| :--- | :--- | :--- | :--- |
| G-1 | polkit-правило = root без пароля | да | высокая серьёзность обоснована |
| G-2 | подпись checkpoint не привязана к каталогу прогона | да | PoC: копия дерева прогона + store → `signed`, ошибок нет |
| G-3 | токен гейта бессрочный и не различает решение | да | PoC: approve-токен принят для reject; минимум длины ключа не проверяется |
| G-4 | сиды на 3.10/3.11 без `filter="data"` | **частично** | `TypeError` только на 3.10.0–3.10.11 и 3.11.0–3.11.3 (бэкпорт CVE-2007-4559 вышел в 3.10.12/3.11.4). CI гоняет последние патчи 3.10/3.11 → дыру не поймает. Формулировку в CHANGELOG надо уточнить |
| G-5 | фильтр «стадия» в дашборде заполнен типами событий | да | `factory/ui/static/index.html:1278` |
| G-6 | журнал без `O_NOFOLLOW` | да | `factory/journal.py:348` |
| G-7 | `grant_run_tree` даёт rwX на `state.json` | да | `factory/runtime/isolation.py:166` |
| G-8 | bwrap не отбирает сеть, агент под uid оператора | да | `factory/runtime/sandbox.py:136-150` (нет `--unshare-net`) |
| G-9 | `docs/runtime.md` говорит про 1.0.9 | да | `docs/runtime.md:3` |

**Чего отчёт не делает** (и это ценно): он не утверждает, что эксплойт кого-то реально атаковал, и честно
называет остаточные риски (сеть, rwX на control plane, секреты 0644) «задокументированными, но основными».
Отчёт читается как отчёт человека, который понимает модель угроз, а не как список правок.

---

## 2. Вердикт по патчу

### 2.1 Что в патче сделано правильно — принять как есть

| Патч | Почему годится |
| :--- | :--- |
| `factory/checkpoint.py` → статус `foreign` | PoC-подтверждено: чужой anchor больше не выглядит подписанным; подписываются поля записи, а не обёртка; обратная совместимость со старыми store сохранена (`entry.get("run_id", document.get("run_id"))`) |
| `factory/engine.py` → токен `exp.HMAC(key, run\|subject\|approver\|action\|exp)`, `MIN_APPROVAL_KEY_BYTES = 32` | Закрывает G-3 полностью; действие, срок и минимальная длина ключа присутствуют; `factory approval-token` — удобный минтер |
| `factory/journal.py` → `O_NOFOLLOW` | Закрывает G-6; TOCTOU закрыт самим `O_NOFOLLOW`, а не проверкой `is_symlink()` |
| `factory/ui/static/index.html` → фильтр стадий | Закрывает G-5 |
| `factory/eval.py` → `_extract_members` / `_extract_members_legacy` | Закрывает G-4 консервативно: без `filter="data"` — отказ ссылок и спецфайлов, режим 0644/0755, uid/gid 0 |
| `factory/runtime/root_helper.py` как **фиксированный список подкоманд** | Верная модель привилегии: без shell, без произвольного argv, `uid 0` запрещён, пути ограничены `ENV_ROOT`, отказ прав проверяется после `setuid` |

### 2.2 Дефекты патча — обязательная доработка

Все дефекты найдены **моим независимым разбором**; в отчёте Grok их нет.

| ID | Severity | Дефект | Где |
| :--- | :--- | :--- | :--- |
| D-1 | **BLOCKER** | **Helper — Python-скрипт с shebang оператора.** `helper.write_text(f"#!{sys.executable}\n{HELPER_BODY}")`, а `HELPER_BODY` делает `from factory.runtime.root_helper import main`. Root исполняет интерпретатор и код из каталога пользователя. В этом репозитории `sys.executable` → `/home/anony/.../.venv/bin/python` → `/home/anony/.hermes/tools/python-3.14.7+.../bin/python3.14` — каталог пользователя. Оператор (или агент вне песочницы) правит интерпретатор/пакет → **root без пароля возвращается**: ровно тот класс дыры, который аудит закрывает | `factory/runtime/isolation.py:348` + `HELPER_BODY` |
| D-2 | **BLOCKER** | **`factory setup-executor` больше не устанавливает helper.** `setup_executor_user()` бросает `ExecutorUserError`, если helper не установлен; подкоманды `install-helper` у helper нет; polkit разрешает только `/usr/local/libexec/factory-root-helper`. На чистой машине «одноразовая настройка» падает и требует ручного `sudo` (команда есть только в тексте ошибки) | `factory/runtime/isolation.py:352-353` |
| D-3 | **HIGH** | **Имя pid-файла не проходит собственную валидацию helper'а.** `PID_NAME_RE = ^[a-z0-9][a-z0-9-]{0,80}\.pid$`, а `pid_file_path` даёт `_UNIT_SAFE_RE.sub("-", f"{run_id}-{seq}")`, где `seq = context.sequence or context.step` → `s00_intake` → `run-01-s00_intake.pid` → `HelperError: refusing env path name`. Шаг падает при `sequence == 0` (fallback на `step`) | `factory/runtime/isolation.py` (`pid_file_path`) + `factory/runtime/root_helper.py` (`PID_NAME_RE`) |
| D-4 | **HIGH** | **Тесты границы доверия проверяют старый механизм.** `tests/test_runtime_trust_boundary.py:363` (`_systemd_run_as`) вызывает `systemd-run` напрямую; после патча продукт идёт через `pkexec` + helper. Три теста (uid, чтение 0600-секрета, system-команда) проверяют путь, которого в продукте больше нет, и пропускаются по `systemd_run_available()` — функции, которая в патче стала мёртвой | `tests/test_runtime_trust_boundary.py` |
| D-5 | MEDIUM | **Личность оператора утекает в окружение исполнителя.** `sandbox_env()` копирует `os.environ` → `USER`/`LOGNAME` оператора; helper их не фильтрует. В варианте с systemd эти переменные задавал systemd от имени исполнителя | `factory/runtime/root_helper.py` (`_child_env`) |
| D-6 | MEDIUM | **Не задана явная идентичность исполнителя.** `USER`, `LOGNAME`, `HOME` берутся из env-файла оператора. Раньше `HOME` приходил из `EnvironmentFile` — поведение совпало случайно, а не по замыслу. Для аудита важно, чтобы было записано: чей HOME и почему | `factory/runtime/root_helper.py` (`_child_env`) |
| D-7 | MEDIUM | **Остатки из самого отчёта не закрыты и не зафиксированы:** `grant_run_tree` даёт rwX на `state.json` и журнал; bwrap не отбирает сеть; `docs/runtime.md` всё ещё 1.0.9; `docs/threat-model.md` всё ещё говорит «отдельный Unix-пользователь не покрыт»; в CHANGELOG нет записи 1.0.11 | `factory/runtime/isolation.py:166`, `factory/runtime/sandbox.py`, `docs/runtime.md:3`, `docs/threat-model.md:31`, `CHANGELOG.md` |
| D-8 | LOW | **Мёртвый код и рассинхрон ссылок:** `systemd_run_available()` осталась, `unit_name` удалён; `docs/audit/handoff-1.0.9.md` и `NEXT_SESSION_PROMPT.md` описывают старую схему `systemd-run` | `factory/runtime/isolation.py`, `docs/audit/*` |
| D-9 | LOW | **pid-файлы не удаляются** по завершении прогона (`remove_env_file` убирает только `.env`) → мусор в `/run/factory-executor` | `factory/runtime/isolation.py` |
| D-10 | LOW | **Край `_decode_secret`:** 32-символьная hex-строка → 16 байт → отказ «нужно 32», хотя оператор посчитал её 32-байтной. Плюс разделитель `\|` в токене допускает неоднозначность, если в `approver` есть `\|` | `factory/engine.py` (`_decode_secret`, `make_approval_token`) |
| D-11 | LOW | **Нет ни одного теста на живую границу root:** ни `install-env`/`remove-env`/`stop`/`ensure-user` с настоящим helper'ом, ни на имя pid-файла, ни на «`setup-executor` печатает инструкцию». 4 новых теста проверяют только `prepare()` | `tests/test_root_helper.py` |
| D-12 | LOW | **Предсуществующее падение CI на `main`:** `tests/test_docs.py::test_readme_does_not_claim_full_autonomy` падает после рерайта README (коммит `9cdf324`) — патч это не чинит и не ломает | `tests/test_docs.py:69` |

---

## 3. План работ (7 коммитов)

### Коммит 1 — P0: helper самодостаточный и с root-owned интерпретатором (D-1)

**Файлы:** `factory/runtime/root_helper.py`, `factory/runtime/isolation.py`, `tests/test_root_helper.py`

1. `factory/runtime/root_helper.py` уже не импортирует ничего из пакета `factory` (только `os`, `re`, `sys`, `pathlib`)
   → сделать его **самодостаточным скриптом**: при установке писать в `/usr/local/libexec/factory-root-helper`
   исходник модуля целиком + `if __name__ == "__main__": raise SystemExit(main())`, без `import factory`.
2. Shebang — **системный** интерпретатор из root-owned каталога: `/usr/bin/python3` (проверить `os.stat().st_uid == 0`
   и что путь не внутри `$HOME`); `sys.executable` оператора использовать **запрещено**.
3. `HELPER_BODY` в `isolation.py` удалить; заменить на генерацию скрипта из исходника модуля.
4. Тесты: в собранном helper нет строк `import factory` / `from factory`; shebang указывает на root-owned путь;
   helper не падает при `import` без пакета в `sys.path` (запуск в чистом окружении).

**Почему:** иначе «фиксированный список подкоманд» не имеет никакой силы — root исполняет то, что может
переписать оператор.

**Проверка:** `pytest tests/test_root_helper.py -q`; вручную — `sudo install` собранного helper'а и
`pkexec /usr/local/libexec/factory-root-helper ping` не должен давать root при подмене пакета `factory`.

---

### Коммит 2 — P0: работоспособная установка без ручного sudo-ковыряния (D-2)

**Файлы:** `factory/runtime/root_helper.py`, `factory/runtime/isolation.py`, `factory/cli.py`, `docs/runtime.md`

1. Добавить подкоманду helper'а `install-policy OPERATOR` — она **сама** (от root) пишет
   `/etc/polkit-1/rules.d/49-factory-root-helper.rules` и снимает legacy-правило `50-factory-transient-units.rules`
   (проверка содержимого перед удалением уже есть в `_retire_legacy`).
2. `setup_executor_user()`: если helper не установлен — напечатать **ровно одну** команду
   `sudo install -m 0755 -o root -g root <staging>/factory-root-helper /usr/local/libexec/factory-root-helper`
   и выйти с понятным кодом; если установлен — `privileged(["install-policy", operator])`,
   `privileged(["retire-legacy-polkit"])`, `privileged(["ensure-user", user])`, гранты.
3. Никаких других root-привилегий у оператора не остаётся: пароль без пароля — только запуск
   `/usr/local/libexec/factory-root-helper` с фиксированным списком подкоманд.
4. `factory setup-executor --print-install` — печатает инструкцию без установки (для документации и CI).

**Проверка:** на чистой машине `factory setup-executor` даёт одну команду `sudo`, после неё — полный
список действий; `factory doctor` не ругается.

---

### Коммит 3 — P0: pid-файл и имя шага (D-3, D-9)

**Файлы:** `factory/runtime/isolation.py`, `factory/runtime/root_helper.py`, `factory/runtime/hermes.py`, `factory/runtime/opencode.py`

1. Имя pid-файла — из **числового** номера шага: `seq` приводится к `int`, если это не int — к
   `sha256(step)[:12]`. Итоговое имя: `f"{run_id}-{seq_token}.pid"`, где `seq_token` — `[0-9a-f-]`.
2. `PID_NAME_RE` / `ENV_NAME_RE` оставить строгими (`[0-9a-z-]`), но приводить имя к канону **до** вызова helper'а.
3. Удаление pid-файла — в `finally` прогона рядом с `remove_env_file` (D-9).
4. `_child_env`: убрать `USER`/`LOGNAME`/`SHELL` оператора, выставить `USER`/`LOGNAME` = uid исполнителя,
   `HOME` — как решим (D-5, D-6): явно `HOME` оператора (текущее поведение) с комментарием, что это нужно
   CLI агента (`~/.hermes`), и `USER`/`LOGNAME` исполнителя.

**Проверка:** `prepare(['stop', 'factory-executor', '/run/factory-executor/run-01-s00_intake.pid'])` → OK;
тест на `seq` = int и = str.

---

### Коммит 4 — P0: тесты границы доверия под новый механизм (D-4, D-11)

**Файлы:** `tests/test_runtime_trust_boundary.py`, `tests/test_root_helper.py`

1. Переписать `_systemd_run_as` → `_run_as_executor` через `isolation.executor_command` + helper;
   skip — по `isolation.helper_installed()`, а не по `systemd_run_available()`.
2. Добавить негативные тесты helper'а: неизвестная подкоманда; `root` как пользователь; путь вне
   `/run/factory-executor`; `..` в имени; попытка `run-as` с `--` внутри команды; ссылка вместо env-файла.
3. Тест на то, что `POLKIT_RULE` не содержит `manage-units` (уже есть в патче — сохранить).
4. Тест на `setup_executor_user` в режиме «helper не установлен» → возвращается инструкция, а не traceback.

**Проверка:** `pytest tests/test_runtime_trust_boundary.py tests/test_root_helper.py -q`.

---

### Коммит 5 — P1: принять корректные исправления патча Grok (G-2, G-3, G-5, G-6, G-4)

**Файлы:** `factory/checkpoint.py`, `factory/engine.py`, `factory/cli.py`, `factory/journal.py`,
`factory/ui/static/index.html`, `factory/eval.py` + соответствующие тесты

Берём из патча без изменений: `foreign`-статус checkpoint; токен гейта с действием и сроком;
`O_NOFOLLOW` в журнале; фильтр стадий; legacy-распаковка сидов.

Правки к патчу:
1. `_decode_secret` — принимать hex **или** raw и требовать ≥32 байт **после** декодирования; в сообщении
   показывать исходную длину (D-10).
2. Формулировка про 3.10/3.11 — «на 3.10.0–3.10.11 и 3.11.0–3.11.3, где нет бэкпорта `filter="data"`» (G-4).
3. `docs/checkpoints.md` — добавить строку `foreign` (уже в патче) и пример в `docs/runtime.md`.

**Проверка:** `pytest tests/test_checkpoint.py tests/test_v012_regressions.py tests/test_journal.py tests/test_eval_seed.py -q`.

---

### Коммит 6 — P1: остаточная граница (G-7, G-8, D-7)

**Файлы:** `factory/runtime/isolation.py`, `factory/runtime/sandbox.py`, `docs/threat-model.md`, `SECURITY.md`

1. `grant_run_tree` — **не** выдавать rwX на control plane: `state.json`, `blackbox.jsonl`, `checkpoint.json`,
   `run.lock` исключаются из ACL-гранта (как сейчас, но это перестанет быть «тонкой границей» только за счёт ro-биндов).
2. `bwrap` — добавить `--unshare-net` в режиме отдельного пользователя (в обычном режиме сеть нужна агенту:
   оставить как есть и задокументировать).
3. `docs/threat-model.md` — привести «не покрыто» в соответствие с реальностью: отдельный пользователь
   **есть**, сеть **не** отрезана в обычном режиме, секреты 0644 **читаются** агентом.
4. `SECURITY.md` — описать новую границу: одна привилегированная программа, фиксированные подкоманды,
   root-owned интерпретатор, токен гейта с действием и сроком.

**Проверка:** `pytest -q`; ручной прогон `factory run --executor-user factory-executor` на машине с `pkexec`.

---

### Коммит 7 — P2: документация, версия, релиз (D-7, D-8, D-12)

**Файлы:** `CHANGELOG.md`, `README.md`, `docs/runtime.md`, `docs/threat-model.md`, `SECURITY.md`,
`docs/quickstart.md`, `docs/audit/handoff-1.0.9.md`, `tests/test_docs.py`, `VERSION`

1. `docs/runtime.md:3` — «версии `1.0.9`» → актуальная (D-7).
2. `CHANGELOG.md` — добавить **пропущенную** запись 1.0.11 и запись 1.0.12 с честным описанием
   (что закрыто, что осталось риском).
3. Починить `tests/test_docs.py::test_readme_does_not_claim_full_autonomy` — вернуть в README формулировку
   «не полностью автономная» (D-12): **CI на `main` сейчас красный**, это надо закрыть до релиза.
4. Убрать мёртвый `systemd_run_available()` (или оставить с пометкой, что он только для диагностики) —
   иначе тесты пропускают себя по несуществующему механизму (D-8).
5. `VERSION` → `1.0.12`; `factory.__version__`, бейджи в README/README.en, `DESIGN.md`, `docs/quickstart.md` — синхронно.
6. Тег `v1.0.12`, push в `origin`, запись в `docs/audit/` (этот план + отчёт Grok как evidence).

**Проверка:** `pytest -q` (все зелёные), `factory doctor`, `pip install dist/*.whl` в чистом venv + `factory doctor`.

---

## 4. Итоговый чек-лист перед релизом

- [ ] `pytest -q` — 0 падений (включая `test_readme_does_not_claim_full_autonomy`)
- [ ] `factory doctor` — зелёный; `factory setup-executor` проходит end-to-end на машине с `pkexec`
- [ ] helper: shebang root-owned, в собранном файле нет `import factory`
- [ ] `POLKIT_RULE` не содержит `manage-units`; legacy-правило снято
- [ ] PoC-регресс: скопированный anchor → `status='foreign'`; approve-токен не подходит для reject;
      ключ 8 байт отвергается; `run-01-s00_intake.pid` принимается
- [ ] `factory verify --run-id <id> --stage s9_learn --require-gate` на `examples/nano-todo` — зелёный
- [ ] `docs/runtime.md`, `docs/threat-model.md`, `SECURITY.md`, `CHANGELOG.md` — без расхождений с кодом
- [ ] `VERSION` = `1.0.12`, тег `v1.0.12`

## 5. Риски, о которых пользователь должен знать

- **Переезд с `systemd-run` на `pkexec` — самое рискованное место.** Прежний механизм давал cgroup-изоляцию и
  гарантированный `stop` юнита; helper даёт только pid-файл. Таймаут-шага теперь гасится по pid —
  это надо проверить вживую (`SIGKILL` по pid-файлу с проверкой uid), иначе возможен «зависший» исполнитель.
- **`pkexec` требует polkit-демон и агента.** На headless-машине без polkit-агента авторизация по правилу
  `Result.YES` проходит, но при сбое polkit оператор получит запрос пароля — установку надо проверять на целевой машине.
- **Установка helper'а требует однократного `sudo`.** Это осознанный компромисс: у того, у кого есть sudo,
  root и так есть; закрываем именно агента под uid оператора.
- **`grant_run_tree` без rwX на control plane (Коммит 6)** может сломать `factory` внутри прогона, если
  агент пишет в `state.json` мимо CLI — нужно проверить на реальном прогоне, а не только на тестах.
- **Оценка G-4 в отчёте завышена по охвату версий** (не все 3.10/3.11 затронуты) — в CHANGELOG это надо
  сформулировать точно, иначе получим ложное обещание.
