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
