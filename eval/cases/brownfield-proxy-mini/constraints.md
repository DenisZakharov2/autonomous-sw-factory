## Рамки кейса

- Ровно три требования: `REQ-001` — предупреждение о точном дубле правила
  (`W_RULE_DUPLICATE`); `REQ-002` — предупреждение о правиле после `MATCH`
  (`W_RULE_UNREACHABLE`); `REQ-003` — стабильный публичный API `Config::warnings()`
  с номером правила, кодом и сортировкой по номеру. Других `REQ` не заводи.
- План `s4_build` состоит ровно из двух задач: `TASK-001` — структура
  `ConfigWarning`, сбор дублей и тесты; `TASK-002` — недостижимые после `MATCH`,
  сортировка, документация API и тесты. Владелец обеих задач —
  `12-fullstack-developer`; `TASK-002` зависит от `TASK-001`.
- `s1_spec` сдают две роли раздельно: `05-business-analyst` — `spec.md`,
  `spec-human.md`, `traceability.json`; `06-system-analyst` — `domain-model.md`,
  `access-matrix.md`.
- Матрица `traceability.json` ссылается только на `TASK-001` и `TASK-002`;
  пути тестов — относительно каталога прогона, например
  `app/src-tauri/src/config.rs::tests::warns_on_duplicate`.

## Стадия `s_in` (до любых правок кода)

- В каталоге прогона лежит настоящий клон `proxy-for-ubuntu` с `.git`
  (commit `dfe54336fed20cbed9ba12e6ff8be1f7877346f0`). Не пушь и не меняй
  историю; `.git` читать можно.
- Создай `backups/pre-factory.bundle` командой
  `git bundle create backups/pre-factory.bundle --all`.
- Создай `backups/dirty-worktree.tar.gz` из отслеживаемых файлов
  (`git ls-files -z | tar --null -T - -czf ...`), без `.git` и без файлов
  управляющей плоскости фабрики, и `backups/manifest.sha256` с sha256 обоих
  файлов.
- Восстанови дерево из бандла в `backups/restore-check` (временный клон внутри
  прогона), сверь `git rev-parse HEAD` с указанным коммитом и
  `git status --porcelain --untracked-files=no` с исходным. Неотслеживаемые
  файлы фабрики (`state.json`, `artifacts/` и т. п.) в сверку не входят.
  Пустой каталог вместо дерева — не PASS.
- В `artifacts/restore-drill.md` замени `PENDING` на `PASS`, приведи реальные
  команды, код возврата и результат сверки. `brownfield-audit.md` — границы
  скана, найденные точки входа/тесты, риски; секреты и `.env` не читай.

## Тесты продукта

- Команда: `cargo test --lib --manifest-path app/src-tauri/Cargo.toml`.
- Минимум 218 passed; новые тесты добавляй в `config.rs`, не удаляй старые.
- Сборка крейта тяжёлая: не запускай `cargo clean` и не пересобирай без нужды.
