# Changelog

All notable changes are recorded here. The project follows a simple
`0.MINOR.PATCH` scheme until `1.0.0`.

## [Unreleased]

### Added

- **подписанные контрольные точки журнала:** `factory sign --run-id ID
  --key-file KEY` считает HMAC-SHA256 от головы цепочки (run_id, `key_id`,
  `event_count`, `head_hash`, `created_at`) и кладёт якорь в store вне `runs/`
  (`~/.local/state/factory/checkpoints` или `FACTORY_CHECKPOINT_DIR`), а копию —
  в `runs/<id>/checkpoint.json`. `factory verify` различает `signed`, `stale`
  (новые события после подписи — не ошибка), `rolled_back` (журнал усечён до
  старой подписи) и `diverged` (цепочка переписана); ключ и store внутри
  прогона отклоняются, сломанную цепочку подписывать нельзя. Контракт и
  границы — `docs/checkpoints.md`.

- **внешний eval-набор:** `factory eval --cases eval/cases` прогоняет каждый
  кейс в отдельном `runs/<id>-<stamp>/`, проходит человеческие гейты от имени
  `human:eval`, проверяет инварианты из `expected.json` (артефакты, паритет
  `REQ`, задачи `s4_build`, закрытые гейты, сдавшие роли, тесты продукта и
  необязательные `checks/*.py`) и сравнивает числа с `eval/baseline.json`:
  шаги не растут, события не падают, токены и usd не растут больше чем на
  10 %. `--update-baseline` отказывается записывать эталон из падающего кейса.
  Два кейса: `nano-todo` (4 роли) и `standard-mini` (мультиролевая `s1_spec`).
  Схемы кейса, ожиданий и baseline валидирует `factory doctor`; формат —
  `docs/eval.md`.
- **сравнение прогонов:** `factory compare --left ID --right ID` показывает
  дельту по стадиям (шаги, события, секунды, токены, задачи), итоги и хеши
  артефактов: `changed`, `only_left`, `only_right`, `same`.
- **проверка схем дешевле:** `validate_instance` больше не прогоняет
  `jsonschema.validate`, который заново валидирует саму схему на каждом
  вызове; результат `check_schema` кешируется по содержимому (только успехи),
  а `_check_recorded_artifacts` загружает реестр артефактов один раз вместо
  одного раза на запись. Два кейса eval укладываются в 45 с на загруженной
  машине и в секунды на CI.

### Verified

- 215 тестов; `tests/test_eval.py` прогоняет оба кейса против
  закоммиченного baseline, проверяет порог шагов, отказ записывать эталон из
  падающего кейса, обязательность `checks/`, схемы кейса/baseline и дельту
  `factory compare`;
- `factory eval --cases eval/cases` на `fake`: `nano-todo` — 14 рабочих шагов,
  33 события, 2 задачи, 3 теста продукта; `standard-mini` — 16 шагов,
  35 событий, `s1_spec` сдана двумя ролями (`05`, `06`); `EVAL OK`;
- smoke `factory compare --left nano-todo-t2 --right standard-mini-t2`:
  дельта по стадиям и 41 совпавший артефакт, 2 изменённых, 1 только слева;
- `tests/test_checkpoint.py` пересчитывает всю цепочку, усекает
  журнал до подписанной головы, подставляет чужой ключ и правит копию
  чекпоинта в прогоне — `verify` ловит каждый случай;
- smoke на реальном CLI: `factory sign` → `factory verify` OK, после пересчёта
  цепочки с чужим `why` — `journal head matches no signed checkpoint`.

## [0.2.2] — 2026-10-30

### Added

- **стоимость шага измерена и учтена:** `runtime/usage.jsonl` теперь пишет
  `step` (уникальный на весь прогон), `loop_step`, `tokens_in`,
  `tokens_out`, `cache_read`, `api_calls` и `model`; итог отдельно показывает
  `tokens_billable` без кеша. Причина: `total_tokens` CLI повторяет
  кешированный префикс на каждом API-вызове, и бюджет 400k кончался не на
  «размере промпта», а на числе вызовов;
- **`--executor-tools auto`:** Hermes получает `file` на всех стадиях, кроме
  `s4_build` (`file,terminal`). Реальный `s00_intake` сократился с 79 208 до
  15 760 токенов (5.0×) при `verify` OK; `--executor-tools all` возвращает
  полный набор для отладки;
- **`standard` проверен end-to-end:** полный прогон на `FakeExecutor` —
  16 производительных шагов, 35 событий, `verify --require-gate` OK;
  добавлены тесты на мультиролевую `s1_spec` и rework с `s5_acceptance`;
- **параллельные задачи `s4_build` в git worktree:** `--parallel-tasks N`,
  один worktree на задачу, merge с проверкой хеша базы, откат при конфликте
  или падении, отклонение пересекающихся `Allowed Files` до запуска;
  `state.json` и журнал остаются single-writer.

### Changed

- `FakeExecutor` пишет только артефакты своей роли (как реальный исполнитель),
  иначе мультиролевая стадия закрывалась одним шагом и маскировала dispatch;
- `_submit_order` сдаёт только артефакты действующей роли, а не весь план
  стадии: чужие файлы больше не пересдаются от имени роли, которая не
  работала;
- `RunContext` получил `control_dir` и `task` для worktree-воркера; промпт
  принимает `workspace`.

### Fixed

- `step` в `usage.jsonl` сбрасывался на 1 после каждого человеческого гейта,
  потому что CLI запускается заново; теперь он durable;
- повторная сдача артефактов других ролей на мультиролевой стадии раздувала
  журнал и приписывала авторство не той роли.

### Security

- **граница доверия стала границей, а не детектором:** `hermes` и `opencode`
  запускаются в `bwrap` по умолчанию; `--no-sandbox` — явный отладочный флаг
  с предупреждением. Отсутствие `bwrap` — `RuntimeFailure`, а не тихий запуск
  без изоляции;
- **управляющая плоскость read-only:** каталог прогона монтируется
  `--ro-bind`; на запись открыты только объявленные ордером каталоги
  артефактов и `runtime/agent/` для usage-файла Hermes. `state.json`,
  `blackbox.jsonl`, `blackbox.lock`, `idea.md`, `runtime/usage.jsonl` и
  `runtime/evidence/` агенту недоступны. `factory gate approve` внутри
  песочницы падает на `blackbox.lock` с `EROFS`, состояние и журнал не
  меняются;
- **root-артефакт больше не открывает весь проект:** `s15` монтирует один
  `skills-lock.json`, а не `project_root`; путь, содержащий `factory/`,
  отклоняется и в `--sandbox-rw`;
- **очистка окружения:** потомок не получает `FACTORY_*` и `PYTHONPATH`;
- **symlink-safe запись рантайма:** `safe_write_text` использует `O_NOFOLLOW`,
  `path_in_runtime` не пишет через симлинк и не выходит из прогона;
- **бюджет и evidence:** `runtime/usage.jsonl` добавлен в tripwire; `main`
  печатает `OSError` (включая `EROFS` из песочницы) без traceback.

### Changed

- `factory run` для реальных исполнителей по умолчанию включает песочницу;
  `--sandbox` больше не обязателен, `--no-sandbox` выключает границу;
- `HermesExecutor` пишет usage в `runtime/agent/usage-<seq>.json`;
- `RunContext` получил `path_in_executor`; `extra_read_write` теперь содержит
  вычисленные пути, а не корень проекта.

### Verified

- 193 теста, включая `tests/test_runtime_trust_boundary.py` на реальном
  `bwrap`: запись в управляющую плоскость и `factory gate approve` изнутри
  песочницы блокируются, объявленные артефакты пишутся;
- реальный шаг Hermes `s00_intake` в песочнице: 49.5 с, 79 208 токенов с
  полным набором инструментов; 24.9 с, 15 760 токенов с `--executor-tools
  file`; `factory verify --stage s00_intake` — OK в обоих случаях;
- полный `nano` на `FakeExecutor` — 33 события; полный `standard` — 16 шагов,
  35 событий, `verify` OK;
- две задачи `s4_build` в отдельных worktree слиты, `verify --stage s4_build`
  OK; пересечение файлов отклонено, падение одной задачи не оставило
  частичного state.

## [0.2.1] — 2026-10-30

### Fixed

- **CI:** sandbox-тесты помечены `skipif`, когда `bwrap` не установлен, а сам CI
  ставит `bubblewrap`; на GitHub runners матрица падала с
  `bubblewrap is not installed`, хотя локально всё проходило. Функциональность
  `0.2.0` не менялась.

## [0.2.0] — 2026-10-30

### Added

- **исполняемый рантайм `factory/runtime/`**: протокол `Executor`
  (`execute(order, context) -> ExecutionResult`), `run_loop` и команда
  `factory run --run-id ID --executor fake|hermes|opencode`. Коды выхода:
  `0` — прогон завершён, `2` — нужен человек, `3` — эскалация;
- **`pending_artifacts` и `next_role` в work order**: каждый ордер теперь
  говорит, какой именно артефакт отсутствует или невалиден, кто его владелец
  и кто действует следующим. Для `s4_build` скоуп сужается до одной задачи;
- **`validate_artifact`** — проверка одного объявленного артефакта, на которой
  построены и `pending_artifacts`, и `validate_stage`;
- **`work_order_submit_plan/paths`**: сдаются только объявленные артефакты,
  существующие на диске; для задачи — только её `Allowed Files` и
  `deliverable.md`. Отчёт исполнителя о «созданных файлах» больше не является
  списком для submit;
- **учёт бюджета**: `runs/<id>/runtime/usage.jsonl` (шаг, стадия, роль,
  секунды, токены, usd, exit code) и остановка при превышении бюджета режима;
  неизвестные числа остаются `null`, а не нулём;
- **evidence**: `runs/<id>/runtime/evidence/*.json` с результатом `verify`,
  хешем `state.json`, головой журнала и хешами сданных артефактов;
- **tripwires `factory/runtime/guard.py`**: хеши `state.json`,
  `blackbox.jsonl`, `blackbox.lock`, `idea.md` и список файлов в каталоге
  прогонов сверяются до и после шага; подмена — остановка `tampered`;
- **`FakeExecutor`** на эталонных артефактах `examples/nano-todo`: полный
  `nano` без модели и сети, плюс сценарии `break-artifact`, `write-state`,
  `escape`, `slow`;
- **адаптеры**: `OpenCodeExecutor` (`opencode run --format json`, жёсткий
  таймаут, разбор usage), `HermesExecutor` (`hermes -z ... --in ...
  --usage-file`, реальные токены, `failed: true` → ненулевой код),
  `sandbox_command` (`bwrap`: read-only корень, запись только в каталог
  прогона и явные `--sandbox-rw`); `systemd-run --user` не принимает
  `ProtectHome`/`ProtectSystem`, поэтому backend заменён;
- **вход стадии intake**: `factory init --idea "..."` пишет
  `runs/<id>/idea.md`, промпт включает его как единственный источник истины о
  продукте и защищает tripwire-ом;
- **обратная связь по стадии**: если артефакты на месте, а `validate_stage`
  не проходит, промпт получает список ошибок и разрешение перезаписать
  объявленные файлы;
- **страж `no_progress`**: если объявленные артефакты не изменились за два шага
  подряд, прогон останавливается с кодом `3`, а не жжёт бюджет на пустых
  повторах;
- **`docs/runtime.md`** — контракт, цикл, безопасность, тесты и состояние
  адаптеров.

### Fixed

- root-артефакты (`skills-lock.json`) всегда разрешаются от корня проекта: копия
  внутри каталога прогона больше не подменяет файл, который хеширует журнал;
- `s15_quarantine` получает в промпте явную область сканирования (`skills/`
  проекта, а не библиотеки хост-агента) и требование к путям в локе;
- `s3_plan` получает список ролей, активных на `s4_build`: колонка роли в
  `plan.md` больше не заполняется ролью автора плана;
- неудачный `gate_approve` роли превращается в `gate_reject` с текстом ошибки,
  а не в остановку `gate_failed`.

### Changed

- `factory init` принимает `--idea`; `factory run` создаёт отсутствующий прогон
  сам (`--no-init` это запрещает);
- work order schema: `pending_artifacts` и `next_role` обязательны;
- `init_run` пишет `idea.md`, если идея передана.

### Verified

- полный `nano` на `FakeExecutor`: 33 события, `verify --require-gate` без
  ошибок, `trace` 100%, `log verify` OK — и в исходном дереве, и из
  установленного колеса в чистом venv;
- реальный прогон Hermes дошёл до `s4_build`: `s00_intake`, `s0_vision`,
  `s1_spec` (после реального rework на REQ-паритете), `s15_quarantine`,
  `s2_architecture`, `s3_plan` (после rework на владельце задачи); 5 задач
  импортированы. Прогон остановлен tripwire-ом: агент сам выполнил
  `factory gate approve --gate gate_s3`, потому что ордер говорил «then submit
  them», а CLI не отличает рантайм от агента. Это зафиксировано как граница
  доверия, а не как успешный полный прогон;
- OpenCode-адаптер проверен на stub-бинарнике; end-to-end прогон невозможен,
  пока в окружении не выбран провайдер (`opencode run` без провайдера висит и
  возвращает `Transport: socket closed`).

## [0.1.2] — 2026-10-30

### Fixed

- **transactional core:** every mutation runs under one `run_lock`; the journal
  line is written before `state.json`, so a failed append can no longer leave a
  mutated state without an event (audit BUG-05);
- **artifact integrity:** `submit` and `task submit` require at least one
  artifact, hash every file, and `GATE_DECISION` / `TASK_DONE` record the
  approved hashes; `verify` re-hashes the disk and reports tampering (BUG-01,
  BUG-02, BUG-21);
- **root and directory artifacts:** `skills-lock.json` (`root: true`) and
  `product/src/`, `product/tests/`, `artifacts/screens/`, `artifacts/patches/`
  (`directory: true`) can be submitted; directories are hashed as a manifest
  and symlinks are refused (BUG-03, BUG-04);
- **stage machine:** `submit`, `gate approve/reject`, `task *` and `veto`
  require the current stage; gates close only from `review`; the flags
  `human_spec_approved` and `restore_drill_passed` can no longer be set by
  closing a future gate out of order (BUG-06);
- **work orders:** a rejected gate returns to `produce`, the verify command no
  longer carries a failing `--require-gate`, partial multi-role submits stay
  `in_progress`, `s4_build` dispatches `review` → `in_progress` → ready
  `pending` (deps honoured), and every task must be `accepted` before
  `gate_s4` (BUG-07, BUG-08, BUG-09, BUG-17);
- **escalation:** `factory gate accept-risk`, `factory stage reset`,
  `factory task reset` and `factory veto` close the human-in-the-loop deadlock
  and record `RISK_ACCEPTANCE`, `STAGE_RESET`, `TASK_RESET`, `VETO` (BUG-10,
  BUG-20);
- **validation:** raw `{{...}}` templates and empty sections fail; acceptance
  is parsed per table cell (ordinary words like "fail" no longer reject a
  report) and open Critical/High defects block the gate; `REQ-1` is no longer
  covered by `REQ-10`; traceability checks that tasks, test files and PASS rows
  exist (BUG-13, BUG-14, BUG-15);
- **quarantine:** `s15_quarantine` calls `verify_lock`, symlinks inside a skill
  are refused, a missing `--name` falls back to the scanner report, and
  `REJECT_AND_PURGE` removes the lock entry and the quarantined directory
  (BUG-18);
- **metrics:** `verification_coverage` excludes the inapplicable `s_in` in
  greenfield and is `0.0` after init; gate rejections count as rework;
  `cycle_time` is measured gate-to-gate instead of submit-to-approve (BUG-19);
- **roles:** `09-ai-engineer` is allowed on `s5`/`s6`, `12` is no longer
  assigned to nano `s_in`, `doctor` validates `pipeline.yaml` against
  `active_stages` in both directions, events use the model tier and effort
  declared by the role card, `task accept` writes the `verification.md` report
  required by the verifier card, and unused event types are gone (BUG-20);
- **packaging:** `FACTORY_GLOSSARY.md` and `orchestrator/*.md` ship in the
  wheel; `MANIFEST.in` includes `tests/conftest.py`, `VERSION`, `NOTICE`,
  `examples/` and `docs/`; role files are `0644`; CI installs and tests the
  built sdist (BUG-11);
- **doctor:** repository checks (`VERSION`, `LICENSE`) only run inside the
  factory checkout, so `factory doctor` works in a client project (BUG-12);
- **docs:** version references are updated across `DESIGN.md`, `docs/` and
  `SELF_IMPROVEMENT.md` (BUG-22).

### Added

- optional HMAC approval for human gates: when `FACTORY_APPROVAL_KEY` is set,
  a `human:<id>` decision requires `--approval-token`; without the key the
  decision is journaled as `approval: unauthenticated`;
- 28 regression tests covering every fixed invariant;
- the reference run submits real artifacts and records hashes for all 13 gates.

### Changed

- `factory submit` and `factory task submit` reject an empty `--artifact` list;
- `factory next` proposes `gate` directly when a submitted stage validates, and
  `verify` otherwise;
- the reference run computes metrics before `gate_s9` and never rewrites
  `metrics.json` after its hash is recorded.

## [0.1.1] — 2026-10-29

### Fixed

- gate reject now enforces the declared approver, so a foreign role cannot
  reject a human gate (audit L-01);
- task submit validates the task id, the task owner and every artifact against
  `allowed_files`; stage submit checks the artifact owner (L-02);
- gate reject executes `rework_to`: the target stage and every intermediate
  stage are reopened and `current_stage` is recomputed (L-03);
- `next` returns `action: blocked` for an escalated or blocked stage/task and
  the CLI exits 1 (L-04);
- acceptance requires an explicit `PASS` row per `REQ` and rejects `PENDING`,
  `[ТРЕБУЕТ ПРОВЕРКИ]`, `FAIL` and `BLOCKED`; required sections must be whole
  heading lines (L-06);
- `budget.yaml` is loaded: attempt limits and the veto cap are enforced by the
  engine instead of a hardcoded 3 (M-01);
- `task` and `gate` schemas are synced with `state.$defs`, doctor fails on
  drift, work orders are validated against their schema (M-02);
- every directory artifact must contain a file, including `screens/` and
  `patches/` (M-03);
- the reference run recomputes metrics after `gate_s9`; verification coverage
  is 1.0 instead of 0.923 (M-04);
- one `approved_by` default (`auto:skillspector`), and `quarantine scan
  --run-id` writes `SKILL_INSTALL` to the journal (M-05).

### Added

- `factory budget` prints attempt limits, mode budgets and attempts used;
- regression tests for foreign approver, foreign owner, out-of-scope file,
  rework, escalation, blocked work orders, schema drift and `SKILL_INSTALL`;
- the docs test now walks every Markdown file and checks that documented
  `factory` subcommands exist in the CLI.

### Changed

- `SECURITY.md` states explicitly that role and approver strings are protocol
  discipline, not authentication.

## [0.1.0] — 2026-10-29

### Added

- Executable Python engine: `init`, `next`, `status`, `submit`, `verify`,
  `gate`, `task`, `quarantine`, `trace`, `metrics`, `doctor`, `artifacts`,
  `models`, `log`.
- 13-stage pipeline with human and verifier gates; four modes (`nano`,
  `standard`, `enterprise`, `brownfield`).
- 22 role cards with `active_stages`, `write_scopes`, explicit veto type and
  eight required sections; uniqueness and contract checks.
- 38 artifact templates and 9 JSON schemas; structural Markdown validation.
- SHA-256 hash-chained journal with `fcntl.flock`, state anchor and semantic
  `PASS` checks; truncated tails and forged verifier events are rejected.
- SkillSpector 2.12.0 adapter with fail-closed decision table, directory hash
  lock and substitution detection.
- `REQ-###` traceability matrix, task lifecycle with three attempts and
  escalation, eight health metrics.
- Reference run `examples/nano-todo/` (33 events, 2 tasks, 3 REQ) and CI.
- Full documentation set: README (RU/EN), DESIGN, PIPELINE, ROSTER, AGENTS,
  four protocols, glossary, threat model, metrics, runtime adapters.

### Changed from the audited 1.0.0 kit

- Documentation is no longer a set of one-line stubs.
- `pipeline.yaml` is a real graph and passes its own schema.
- `nano` and `standard` have an explicit owner for spec, spec-human and plan.
- `verify` is no longer a no-op without a flag; empty and truncated journals
  fail.
- `--run-id` is validated; traversal, absolute paths and destructive re-init
  are rejected.
- `advisor` vetoes no longer claim to block; role scopes match duties.
- Remediation IDs from the old audit were replaced with current contracts.
- `LICENSE` contains the full MIT text; `NOTICE` is separate.
- `runs/` is gitignored.

### Known limitations

- The journal is not signed; a determined writer can recompute the whole chain.
- No embedded model or runtime; a host runtime executes roles.
- Cost metrics are `null` until the runtime records tokens and price.
- Brownfield supports git repositories only.
- `s4_build` imports tasks from a Markdown table, not arbitrary plans.
