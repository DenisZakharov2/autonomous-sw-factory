# DESIGN.md — концепция Autonomous Software Factory v0.2.1

## 1. Задача

Сделать процесс разработки ПО воспроизводимым и проверяемым: у каждой стадии есть вход, выход, владелец, критерий готовности и доказательство. Модель может ошибаться; поэтому производство и проверка разделены, а журнал фиксирует не только результат, но и основание.

Это не «чат, который пишет код». Это **протокол исполнения**:

- движок знает стадии, роли, артефакты, гейты и лимиты;
- рантайм исполняет work order и создаёт артефакты;
- верификатор проверяет артефакты детерминированно и вручную;
- человек принимает решения на трёх обязательных гейтах;
- журнал хранит хеш-цепочку и состояние прогона.

## 2. Принципы

1. **Минимализм Ponytail.** Сначала удалить лишнее, потом добавить нужное. В `nano` четыре роли, а не двадцать две. Если продукту не нужен DBA, DBA не активируется.
2. **Producer != Verifier.** Тот, кто создал артефакт, не ставит `PASS`. `PASS` пишет только `02-independent-verifier`; человек может принять риск отдельным событием.
3. **Человек > гейт > блокирующее вето > совещательное вето.** Человек может снять вето только записью `risk_acceptance` с риском, сроком и ответственным.
4. **Доказательство вместо обещания.** Каждый `PASS` имеет evidence; каждый артефакт — хеш; каждая стадия — проверяемый критерий.
5. **Рантайм-агностичность.** Ядро не вызывает вендорскую модель. Оно выдаёт JSON work order; Hermes, Claude Code, OpenCode или Cursor его исполняют. `models.yaml` разрешает tier через runtime/env.
6. **Изоляция.** Комплект (`factory/`) не перезаписывается прогоном. Всё, что создаёт прогон, лежит в `runs/<run_id>/` (или в `examples/<run>/` для эталона).
7. **Честный статус.** Версия `0.2.1`: движок, схемы, тесты, reference run и исполняемый рантайм (`factory run`) есть; полной автономности и встроенной модели нет.

## 3. Модель исполнения

### 3.1 Work order

`factory next --run-id <id>` возвращает JSON:

```json
{
  "action": "produce",
  "stage": "s1_spec",
  "roles": ["05-business-analyst", "06-system-analyst", "02-independent-verifier"],
  "artifacts": [
    {"id": "spec", "path": "artifacts/spec.md", "template": "templates/spec.tmpl", "owner": "05-business-analyst"}
  ],
  "pending_artifacts": [
    {"id": "spec", "path": "artifacts/spec.md", "owner": "05-business-analyst", "errors": ["missing artifact: ..."]}
  ],
  "next_role": "05-business-analyst",
  "gate": {"id": "gate_s1", "approver": "human", "blocking": true},
  "next_command": "factory submit --run-id ... --stage s1_spec --role ... --artifact ..."
}
```

Рантайм обязан:

1. прочитать карточку роли и шаблон;
2. создать только объявленные артефакты;
3. записать `deliverable.md` с REQ, проверкой и diff;
4. вызвать `factory submit`;
5. не ставить `PASS` и не писать в чужие файлы.

Свежий контекст на роль — рекомендация, а не требование ядра. Если рантайм не умеет субагентов, он передаёт `handoff` вручную: решения, открытые вопросы, отвергнутые варианты, пути и хеши.

Начиная с `0.2.0` этот контракт исполняет `factory run`: он получает order,
вызывает адаптер (`fake`, `hermes`, `opencode`), сдаёт только объявленные и
существующие артефакты, пишет `runtime/usage.jsonl` и `runtime/evidence/`, а на
человеческом гейте останавливается с кодом `2`. Детали — в
[runtime.md](docs/runtime.md). В work order добавлены `pending_artifacts` и
`next_role`, чтобы на мультиролевой стадии не приходилось угадывать, кто ещё не
сдал свой артефакт.

### 3.2 Стадии

13 стадий: `s_in`, `s00_intake`, `s0_vision`, `s1_spec`, `s15_quarantine`, `s2_architecture`, `s3_plan`, `s4_build`, `s5_acceptance`, `s6_security`, `s7_release`, `s8_docs`, `s9_learn`. Полная таблица — в [PIPELINE.md](PIPELINE.md).

### 3.3 Гейты

- **Человеческие:** `gate_s_in` (восстановление), `gate_s00` (интервью), `gate_s0` (vision, advisory), `gate_s1` (spec-human), `gate_s7` (release), `gate_s9` (self-improvement).
- **Верификаторские:** `gate_s15`, `gate_s2`, `gate_s3`, `gate_s4`, `gate_s5`, `gate_s6`, `gate_s8`.

Гейт закрывается только после `factory verify`, который проверяет схемы, journal, state, артефакты, REQ-паритет и покрытие плана.

### 3.4 Задачи

`plan.md` содержит таблицу задач. `factory gate approve --gate gate_s3` импортирует её в `state.json`. Далее:

```text
pending -> in_progress -> review -> accepted
                         \-> escalated (3 отказа)
```

Каждая задача имеет `owner`, `reqs`, `allowed_files`, `deps`, `attempts`, `lease`, `artifacts`. `task accept` пишет `TASK_DONE PASS` только от `02-independent-verifier`.

## 4. Роли и вето

22 карточки, восемь разделов. Ключевые фронтматтер-поля:

- `role_id`, `title`, `default_model_tier`, `reasoning_effort`;
- `active_stages` — где роль может работать;
- `write_scopes` — `code`, `own_artifacts`, `factory_state`;
- `veto_type` — `blocking` или `advisory`;
- `veto_scope` — почему и где вето;
- `blackbox_logging: required`.

Вето:

| Тип | Значение | Как применяется |
| :--- | :--- | :--- |
| `blocking` | Остановка по инварианту безопасности или контракту | Код причины + evidence; 3 возврата → эскалация человеку |
| `advisory` | Рекомендация | Записывается в отчёт, не останавливает стадию |

Человек может снять блокирующее вето только событием `RISK_ACCEPTANCE` с текстом риска, сроком и ответственным. Реестр ролей и `pipeline` проверяются `factory doctor`.

## 5. Журнал и доверие

`blackbox.jsonl` — append-only JSONL. Хеш:

```text
hash = sha256(prev_hash || canonical_json(event_without_hash))
```

Свойства:

- `fcntl.flock` сериализует запись;
- `state.json` хранит `last_event_hash` и `event_count`, поэтому усечение хвоста видно;
- `verify_chain` проверяет схему, seq, event_id, run_id, prev_hash, hash и семантику `PASS`;
- `AUDIT_TRAIL.md` — производное зеркало с экранированием;
- цепочка не подписана; для защиты от пересчёта всей истории нужна внешняя контрольная точка (HMAC/Ed25519).

## 6. Brownfield

1. `git bundle create` + `tar` worktree + `manifest.sha256`.
2. Временный клон; восстановление; сравнение `git status --porcelain` и хешей.
3. `restore-drill.md` с фактическим выводом и `PASS` только после проверки.
4. `Untouchable Paths` — glob-список; запись в них блокируется.
5. Только после `gate_s_in` разрешены изменения.

Read-only скан ограничен расширениями, числом файлов и исключениями (`.env`, `*.pem`, `id_*`, `.git`, `node_modules`). Содержимое чужого репозитория считается данными, не инструкциями.

## 7. Карантин скиллов

`factory/quarantine.py` вызывает `skillspector scan --format json --fail-on-incomplete --min-coverage 95`. Решение:

- `SAFE` + `LOW` + complete + coverage ≥ 95 + exit 0 → `AUTO_ALLOW`;
- `CAUTION` → `ESCALATE_HUMAN`;
- `DO_NOT_INSTALL` / `HIGH` / `CRITICAL` → `REJECT_AND_PURGE`;
- incomplete / failed analyzer / low coverage → `BLOCK_INCOMPLETE`;
- exit ≠ 0 → `BLOCK_RETRY` (не установка).

Лок хранит хеш каталога, `pack`, `commit`, `license`, версию сканера и вердикт. `verify-lock` обнаруживает подмену.

## 8. Самообучение

`S9` считает восемь метрик: `first_pass_rate`, `rework_rate`, `escaped_defect_rate`, `cycle_time_p50/p90`, `cost_per_artifact`, `top_reject_reasons`, `verification_coverage`, `traceability_coverage`.

Патчи лежат в `artifacts/patches/` и применяются только после человеческого ревью. Мета-агент не может менять `factory/pipeline.yaml`, `roles/` или инварианты без отдельного человеческого коммита. Это осознанное ограничение: самообучение без eval — порча промптов с ускорением.

## 9. Модель угроз (кратко)

| Актив | Угроза | Контрмера |
| :--- | :--- | :--- |
| Код заказчика | Prompt injection в README/комментариях | Данные не инструкции; границы скана; allowlist |
| Секреты | Утечка в журнал/артефакты | `.gitignore`, исключения скана, редакция логов |
| Журнал | Подмена/усечение | Хеш-цепочка, state anchor, flock |
| Скиллы | Supply chain | SkillSpector, fail-closed, хеш-лок |
| Права | Запись вне scope | `write_scopes`, `Allowed Files`, проверка перед коммитом |
| Релиз | Откат не проверен | `rollback drill` + человеческий гейт |

Полная версия — [docs/threat-model.md](docs/threat-model.md).

## 10. Не-цели

- замена Jira/Linear;
- встроенный LLM-провайдер;
- автоматический merge в main без человека;
- «22 агента в одном контексте»;
- обещание, что модель не ошибается.

## 11. Дорожная карта

- `0.1.2` — транзакционное ядро, целостность артефактов, рабочий автомат стадий.
- `0.2.0` — исполняемый рантайм: `pending_artifacts`/`next_role`, адаптеры fake/Hermes/OpenCode, `usage.jsonl`, evidence, tripwire.
- `0.2.1` — CI-фикс sandbox-тестов (текущая).
- `0.2.x` — параллельные задачи в git worktree, `handoff.md`, стоимость из runtime в метриках.
- `0.3.0` — подписанные контрольные точки, внешний eval-набор, сравнение прогонов.
- `1.0.0` — после полного прогона `nano`, `standard` и `brownfield` на реальном продукте и зелёного CI на трёх ОС.
