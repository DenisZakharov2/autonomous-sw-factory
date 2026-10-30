# AUDIT.md — что изменилось после аудита

Этот документ фиксирует, какие блокеры аудита закрыты в `0.1.0`, а какие
остались ограничениями. Это не независимая сертификация.

## Закрытые блокеры

| Было | Стало | Проверка |
| :--- | :--- | :--- |
| Документация — заглушки | README (RU/EN), DESIGN, PIPELINE, ROSTER, AGENTS, 4 протокола, glossary, docs | `wc -l`, ревью |
| `pipeline.yaml` без стадий | 13 стадий, 4 режима, гейты, владельцы | `factory doctor` |
| `Nano`/`Standard` без автора spec | `01` пишет spec в `nano`; `standard` содержит `05` и `04` | `validate_pipeline` |
| `verify` — no-op без флага | проверка схем, state, journal, ролей, шаблонов, артефактов | `factory verify` |
| Пустой/усечённый журнал проходит | ошибка на пустом; state anchor ловит усечение | `test_empty_journal_is_invalid`, `test_tail_truncation_detected` |
| Конкурентная запись ломает цепочку | `fcntl.flock` | `test_concurrent_appends_keep_chain` |
| `PASS` можно подделать | проверка роли и evidence при записи и проверке | `test_verify_chain_rejects_forged_pass` |
| `--run-id` traversal | строгий regex + resolved path | `test_no_absolute_or_traversal_write` |
| Повторный `init` стирает state | `--force` архивирует в `.archive/` | `test_init_force_archives_previous_run` |
| Карантин не реализован | SkillSpector-адаптер, fail-closed, хеш-лок | `tests/test_quarantine.py` |
| Схемы принимают мусор | 9 полноценных JSON-схем | `test_garbage_state_is_rejected` |
| 21 из 22 ролей нарушают scope | `active_stages`, scopes по обязанностям, журнал только через CLI | `validate_role_card` |
| LICENSE без MIT-текста | полный MIT + отдельный NOTICE | `factory doctor` |
| `.gitignore` без `runs/` | `runs/`, `.env`, ключи, quarantine | ревью |
| Шаблоны ставят `PASS` | `PENDING`/`[ТРЕБУЕТ ПРОВЕРКИ]` | `validate_templates` |
| Битые ссылки на P2 | удалены, заменены контрактами | ревью |
| `advisory` «блокирует» | формулировки исправлены | ревью |
| Версии 1.0/v3.0/3.0.0 | `0.1.0` во всех источниках | `factory doctor` |
| Мёртвый код и тест-тавтология | код подключён или удалён; тесты на инварианты | `pytest` |
| Нет примера | `examples/nano-todo/`, 33 события | `factory verify` |

## Независимый аудит 29.10.2026 (релиз 0.1.1)

Второй аудит проверил `bbc9b0a`. Заявлено 10 подтверждённых дефектов — все
десять воспроизвелись probe-скриптом на этом HEAD и исправлены. Семь пунктов
описывали другую ревизию, из них пять не подтвердились полностью.

| ID | Вердикт | Доказательство |
| :--- | :--- | :--- |
| L-01 | подтверждён, исправлен | `_check_gate_approver` вызывается в `gate approve` и `gate reject`; `test_gate_reject_rejects_foreign_approver` |
| L-02 | подтверждён, исправлен | `submit_task` проверяет `task_id`, owner и `allowed_files`; `submit_work` — владельца артефакта стадии |
| L-03 | подтверждён, исправлен | `gate reject` вызывает `_reset_rework_chain`; `test_gate_reject_returns_to_rework_stage` |
| L-04 | подтверждён, исправлен | `next` возвращает `action: blocked`, CLI — код 1; `test_next_exits_one_when_the_stage_is_escalated` |
| L-06 | подтверждён, исправлен | `validate_acceptance` требует `PASS` по каждому `REQ`; `check_sections` ищет строку-заголовок |
| M-01 | подтверждён, исправлен | `load_budget()` подключён; лимиты в `state.reject_task` и `gate_reject`; `factory budget` |
| M-02 | подтверждён, исправлен | `task`/`gate` схемы синхронизированы с `state.$defs`; work order валидируется; `test_standalone_schemas_match_the_state_records` |
| M-03 | подтверждён, исправлен | `validate_stage` требует файл в каждом directory-артефакте |
| M-04 | подтверждён, исправлен | `metrics.json` эталона пересчитан после `gate_s9`: `verification_coverage = 1.0` |
| M-05 | подтверждён, исправлен | единый дефолт `auto:skillspector`; `quarantine scan --run-id` пишет `SKILL_INSTALL` |
| M-06 | частично, не блокер | `cycle_time` = `max-min` внутри стадии; приближение задокументировано |
| D-02 | частично | верен только `pipeline.yaml` (`gate_s3`): команда исправлена на `factory task import --run-id <id>` |
| D-04 | частично | верен только `docs/quickstart.md`: добавлен `--run-id <id>` |
| L-05 | не подтверждён | дефисы уже принимаются: `TASK_ID_RE` и `^TASK-[A-Z0-9-]+$` |
| D-01 | не подтверждён | в `AGENTS.md` нет `factory quarantine --skill`; `21-technical-writer` указан верно |
| D-03 | не подтверждён | README называет `traceability.json`, `plan.md`, `scanned_at`; дерево корректно |
| D-05 | не подтверждён | в `test_docs.py` `group(1)` — URL; обход расширен на все `.md`. Дефекта нет |
| D-06 | не подтверждён | «Найденное» написано правильно в `artifacts.yaml` и шаблоне |

## Независимый аудит 30.10.2026 (релиз 0.1.1 → 0.1.2)

Третий аудит (`Argon2audit_report_v0_1_1.md`) заявил 22 дефекта. Все 22
воспроизвелись на `e37f8c6` и закрыты в `0.1.2`; для каждого есть регрессионный
тест или проверка в `verify`/`doctor`.

| Блок | Что было | Что стало |
| :--- | :--- | :--- |
| Целостность | `submit` без артефактов, хеши не сверялись | непустой `--artifact`, хеши в submit/gate/task, `verify` перехеширует диск |
| Транзакции | `state.json` писался до журнала, гонки | один `run_lock`, журнал → state, без гонок |
| Артефакты | `root`/`directory` невозможно сдать | поддержаны; каталоги хешируются манифестом, симлинки запрещены |
| Автомат | гейты закрывались вне очереди, reject зацикливался | проверка текущей стадии, `review` перед гейтом, reject → produce |
| Задачи | `deps` игнорировались, `gate_s4` без accepted | приоритет review → active → ready, все задачи accepted |
| Тупики | эскалация навсегда | `accept-risk`, `stage reset`, `task reset`, `veto` |
| Валидация | шаблоны и пустые секции проходили, REQ-1 ≡ REQ-10 | плейсхолдеры, таблицы приёмки, границы REQ, реальные ссылки trace |
| Карантин | без `verify_lock`, симлинки, нет purge | `verify_lock` в стадии, запрет симлинков, purge и имя из отчёта |
| Метрики | coverage 1/13, cycle time submit→approve | 0 после init, gate-to-gate, отказы гейтов в rework |
| Роли | рассинхрон карт и pipeline, tier игнорировался | двусторонняя проверка, tier/effort из карточки |
| Упаковка | sdist без conftest, roles 0600, нет glossary | `MANIFEST.in`, `package-data`, `0644`, CI-тест sdist |
| Документация | 0.1.0 в DESIGN/docs | версия синхронизирована, тест расширен |

## Остаточные ограничения

- журнал не подписан; для сильной модели угроз нужна внешняя контрольная точка;
- встроенной модели нет; роли исполняет рантайм;
- `--role` и `--approver` — строки CLI: проверки owner/approver/`allowed_files`
  держат дисциплину протокола, но не являются аутентификацией или RBAC;
  человеческий гейт можно усилить HMAC-токеном (`FACTORY_APPROVAL_KEY`), порог
  доверия задаёт эксплуатация;
- стоимость `null`, пока рантайм не пишет токены и цену; лимиты токенов,
  минут и USD из `pipeline.yaml` проверяет рантайм, движок проверяет только
  попытки и вето (`factory budget`);
- `cycle_time` измеряется от закрытия предыдущего гейта до закрытия текущего:
  это длительность стадии, но она включает ожидание человека;
- `s4_build` парсит только таблицу `plan.md`;
- `s9_learn` не применяет патчи автоматически.

## Как проверить

```bash
factory doctor
pytest -q
python examples/build_nano_todo.py
FACTORY_ROOT="$PWD/examples/nano-todo" FACTORY_RUNS_ROOT="$PWD/examples" \
  factory verify --run-id nano-todo --stage s9_learn --require-gate
```

Ожидаемо: `DOCTOR OK`, все тесты зелёные, `EXAMPLE OK`, `VERIFY OK`.

## Самоаудит рантайма 0.2.0 (30.10.2026)

Проверялся `factory/runtime/`: цикл, контракт, защита, адаптеры и упаковка.
Каждый пункт воспроизводился на реальном прогоне, а не выводился из чтения кода.

| Находка | Как проявилась | Исправление |
| :--- | :--- | :--- |
| Фикстуры попадали в `__pycache__` после установки колеса | `pip install` компилирует `fixtures/nano/product/src/todo.py`; `FakeExecutor` копировал `.pyc` как текст → `UnicodeDecodeError` на `s4_build` | `fake.py` пропускает `__pycache__`, `.pyc`, `.pyo`; регрессионный тест `test_fixture_copy_ignores_installed_bytecode` |
| `systemd-run --user` не умеет песочницу | `-p ProtectHome=read-only`, `ProtectSystem=strict`, `PrivateTmp`, `NoNewPrivileges` → `Unknown assignment` на пользовательском scope | backend заменён на `bwrap`; проверено реальным вызовом Hermes внутри песочницы |
| Без входа стадия `s00_intake` писала чужой проект | первый реальный прогон Hermes описал `proxy-for-ubuntu` из памяти агента, хотя в прогоне не было идеи | `factory init --idea` → `runs/<id>/idea.md`; промпт объявляет файл единственным источником истины; tripwire защищает его |
| Бюджет `nano` не покрывает реальную модель | Hermes тратит ~162k токенов на шаг `s00_intake`; 400k хватает на ~2 шага | задокументировано; `factory run` принимает `--budget-tokens/--budget-minutes/--budget-usd` |
| OpenCode без провайдера висит | `opencode run` печатает `Transport: socket closed` и не завершается | адаптер всегда с жёстким таймаутом; `{"type":"error"}` → ненулевой код; end-to-end не проверен, пока не выбран провайдер |
| Отчёт исполнителя мог стать заявкой на сдачу | исполнитель может назвать любой путь в `artifacts` | `work_order_submit_plan` берёт только объявленные артефакты, существующие на диске; для задачи — только `Allowed Files` |
| Стадия без прогресса жгла токены | `s1_spec` крутился несколько шагов по 200–300k токенов: артефакты существовали, `validate_stage` падал на REQ-паритете, а промпт говорил «всё на месте» и не давал путей для правки | промпт получает ошибки `validate_stage` и право перезаписать объявленные артефакты; добавлен страж `no_progress`: два одинаковых digest подряд → остановка |
| Root-артефакт недоступен в песочнице | `s15_quarantine` не мог записать `skills-lock.json` в корень проекта: bwrap делал корень read-only | loop добавляет `project_root()` в список записи песочницы, если ордер объявляет `root: true` артефакт |
| Root-артефакт подменялся копией в прогоне | модель записала `skills-lock.json` и в каталог прогона, и в корень проекта; `submit` падал с «not declared», а integrity-проверка могла захешировать не тот файл | `collect_artifact_entries` и `resolve_recorded_path` сначала проверяют объявленные root-пути; тест `test_root_artifact_ignores_a_stray_copy_inside_the_run` |
| План ссылался на неактивную роль | `s3_plan` записал `TASK-001` за `01-smart-interviewer`, а `s4_build` в nano исполняет `12`; `gate_approve` падал, рантайм останавливался | промпт `s3_plan` перечисляет роли, допустимые в колонке задачи; неудачный `gate_approve` роли теперь превращается в `gate_reject` с текстом ошибки |
| Агент закрыл верификаторский гейт сам | на реальном прогоне Hermes выполнил `factory gate approve --gate gate_s3`, потому что ордер говорил «then submit them», а CLI не отличает рантайм от агента; tripwire остановил прогон с `tampered`, но гейт уже был закрыт | промпт больше не повторяет «submit» и запрещает `factory ...`; в документации зафиксировано, что это детектор, а не граница безопасности; настоящая граница — отдельный пользователь/сервис |

## Граница доверия 30.10.2026 (после 0.2.1)

Проверялось поведение, а не текст: `bwrap 0.11.1`, реальный CLI
`factory gate approve` внутри песочницы и реальный шаг Hermes.

| Находка | Как проявлялась | Исправление |
| :--- | :--- | :--- |
| Каталог прогона монтировался на запись целиком | агент мог переписать `state.json`, `blackbox.jsonl`, `runtime/usage.jsonl` и evidence без всякого CLI | `--ro-bind run_dir`; точечно открыты только объявленные каталоги артефактов и `runtime/agent/`; тесты на каждый защищённый файл |
| Root-артефакт открывал весь `project_root` | на `s15_quarantine` агент мог переписать `factory/`, `pipeline.yaml` и тесты, а не только `skills-lock.json` | root-артефакт монтируется одним файлом (или объявленным каталогом); путь, содержащий пакет `factory/`, отклоняется |
| `factory gate approve` агента доходил до записи | тот самый инцидент: гейт закрывался, tripwire фиксировал `tampered` уже после | `run_lock` внутри песочницы падает на `blackbox.lock` с `EROFS`; состояние и журнал не меняются; `main` ловит `OSError` и печатает ошибку без traceback |
| Песочница была необязательным флагом | по умолчанию реальный исполнитель работал без границы | `hermes`/`opencode` по умолчанию в `bwrap`; `--no-sandbox` — явный флаг с предупреждением; отсутствие `bwrap` — `RuntimeFailure`, а не тихий запуск |
| `runtime/usage.jsonl` не входил в tripwire | агент мог обнулить бюджет, а evidence — подделать | ledger и `runtime/evidence/` read-only в песочнице; `usage.jsonl` добавлен в `guard.PROTECTED_FILES` |
| Родитель писал по симлинкам агента | агент мог заранее положить `runtime/ev-*.log` или `prompt.md` симлинкой на файл вне прогона, и `write_text` рантайма писал бы туда | `safe_write_text` с `O_NOFOLLOW`; `path_in_runtime` проверяет, что каталог не симлинк и не выходит из прогона; usage-файл пишется в `runtime/agent/` |
| Окружение наследовало `FACTORY_*` | случайный или намеренный `factory ...` получал корень и каталог прогонов | `sandbox_env()` удаляет все `FACTORY_*` и `PYTHONPATH` у потомка |
| `--sandbox-rw` мог отменить границу | путь `~` или корень проекта повторно открывал управляющую плоскость на запись | bind отклоняется, если путь содержит каталог прогона или пакет `factory/`, либо совпадает с защищённым файлом |

Проверка:

```bash
.venv/bin/python -m pytest -q                         # 193 теста
.venv/bin/python -m pytest -q tests/test_runtime_trust_boundary.py
```

Реальный шаг Hermes в песочнице: `s00_intake`, 49.5 с, 79 208 токенов,
артефакты валидны, `factory verify --stage s00_intake` — OK. Попытка
`factory gate approve` внутри песочницы: exit 1, `Read-only file system:
.../blackbox.lock`, хеш `state.json` не изменился.

## Подпись журнала 30.10.2026 (после 0.2.2)

Проверялось поведение, а не текст: `factory sign` на реальном прогоне, затем
семь способов обойти журнал или сорвать подпись.

| Сценарий | Что сделано | Что увидел `factory verify` |
| :--- | :--- | :--- |
| Пересчёт всей цепочки | изменён `why` у события, все `hash`/`prev_hash` пересчитаны, `state.json` выправлен | `journal head matches no signed checkpoint: the chain was rewritten after signing` |
| Усечение журнала | две подписи, затем журнал обрезан до головы первой | `journal head matches checkpoint #1 of 2: the journal was truncated after signing` |
| Чужой ключ | подпись ключом A, проверка ключом B | `checkpoint #1 was signed with key <A>, the supplied key is <B>` |
| Правка копии в прогоне | изменён `event_count` в `runs/<id>/checkpoint.json` | `checkpoint.json inside the run differs from the checkpoint store copy` |
| Новые события после подписи | `stale`, не ошибка | `checkpoint: stale (signed N events, journal has M) - re-sign` |
| Ключ внутри прогона | `--key-file runs/<id>/journal.key` | `ConfigError`: ключ должен лежать вне каталога прогона |
| Подпись сломанной цепочки | правка события без пересчёта | `RunError: refusing to sign a journal that does not verify` |

```bash
.venv/bin/python -m pytest tests/test_checkpoint.py -q   # 12 тестов
.venv/bin/python -m pytest -q                            # 205 тестов
factory sign   --run-id demo --key-file ~/.config/factory/journal.key
factory verify --run-id demo --key-file ~/.config/factory/journal.key
```

Остаточный риск записан в `docs/checkpoints.md`: HMAC — симметричная подпись
(проверяющему нужен ключ), store не append-only (удаление файла выглядит как
`unsigned`), root и запись в store не покрыты, автоподписи в `factory run` нет.
