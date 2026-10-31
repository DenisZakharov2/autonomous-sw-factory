## Рамки кейса

- Ровно три требования: `REQ-001` — поле `consecutive_failures` с
  `serde(default)` и чтение старого JSON; `REQ-002` — `due()` с
  экспоненциальным backoff `interval * 2^min(failures, 4)`, потолок 24 часа;
  `REQ-003` — `mark_failure`/`mark_success` обновляют счётчик и метки времени.
  Других `REQ` не заводи.
- План `s4_build` состоит ровно из двух задач: `TASK-001` — поле, serde,
  `mark_failure`/`mark_success` и тесты; `TASK-002` — backoff в `due`, потолок
  24 часа и тесты. Владелец обеих — `12-fullstack-developer`; `TASK-002`
  зависит от `TASK-001`.
- `s1_spec` сдают две роли раздельно: `05-business-analyst` — `spec.md`,
  `spec-human.md`, `traceability.json`; `06-system-analyst` — `domain-model.md`,
  `access-matrix.md`.
- Матрица `traceability.json` ссылается только на `TASK-001` и `TASK-002`;
  пути тестов — относительно каталога прогона, например
  `product/src/subscription.rs::tests::backoff_doubles_the_interval`.

## Продукт

- Исходники крейта лежат в `product/` (`product/Cargo.toml`,
  `product/src/subscription.rs`). Не пересоздавай их и не копируй в другие
  каталоги.
- Тесты: `cargo test --lib --manifest-path product/Cargo.toml`, минимум
  218 passed; новые тесты — в `subscription.rs`.
- Сборка тяжёлая: не запускай `cargo clean` и не пересобирай без нужды.
