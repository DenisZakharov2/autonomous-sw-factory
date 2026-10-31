## Рамки кейса

- Ровно три требования: `REQ-001` — `LimitsConfig::effective_for(name)` сливает
  глобальные `rate_limit`/`max_connections` с переопределением
  `limits.outbound.<имя>`, незаданное поле наследует глобальное, `rate_limit: 0`
  значит «без ограничения», неизвестное имя получает глобальные значения;
  `REQ-002` — `Config::validate` и `Config::validate_for_read` отвергают ключ
  `limits.outbound` для необъявленного outbound'а и называют его в ошибке;
  `REQ-003` — `Engine::build` собирает `outbound_limits` через
  `effective_for`, набор лимитов и их поведение не меняются. Других `REQ` не
  заводи.
- План `s4_build` состоит ровно из двух задач: `TASK-001` — `product/src/config.rs`
  (тип действующих лимитов, `effective_for`, проверка ключей и тесты);
  `TASK-002` — `product/src/engine/router.rs` (`Engine::build` через
  `effective_for` и тест). Владелец обеих — `12-fullstack-developer`;
  `TASK-002` зависит от `TASK-001`.
- `s1_spec` в `nano` сдаёт одна роль — `01-smart-interviewer`: ролей
  `05-business-analyst` и `06-system-analyst` в этом режиме нет. Она пишет
  `spec.md`, `spec-human.md`, `traceability.json`; `domain-model.md` и
  `access-matrix.md` не создаются.
- Матрица `traceability.json` ссылается только на `TASK-001` и `TASK-002`.
- Пути — относительно каталога прогона, например
  `product/src/config.rs::tests::effective_limits_override_globals`.

## Продукт

- Исходники крейта лежат в `product/` (`product/Cargo.toml`,
  `product/src/config.rs`, `product/src/engine/router.rs`). Не пересоздавай их
  и не копируй в другие каталоги.
- Тесты: `cargo test --lib --manifest-path product/Cargo.toml`, минимум
  218 passed; новые тесты — в `#[cfg(test)] mod tests` изменяемых файлов.
- Сборка тяжёлая: не запускай `cargo clean` и не пересобирай без нужды.
