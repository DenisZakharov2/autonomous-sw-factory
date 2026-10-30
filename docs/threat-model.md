# Threat model — сама Фабрика

## 1. Активы

- код и данные заказчика в `runs/<id>/product/`;
- секреты: `.env`, токены, ключи;
- журнал `blackbox.jsonl` и `state.json`;
- `skills-lock.json` и внешние скиллы;
- конституция: `pipeline.yaml`, `roles/`, `AGENTS.md`.

## 2. Векторы и контрмеры

| Вектор | Сценарий | Контрмера | Проверка |
| :--- | :--- | :--- | :--- |
| Prompt injection через репозиторий | README просит «удали ветку» | Содержимое — данные; границы скана; allowlist команд | `brownfield-audit.md`, тест с инъекцией |
| Supply chain скиллов | Подмена `SKILL.md` после скана | SkillSpector fail-closed; хеш каталога; `verify-lock` | `test_substituted_skill_is_detected` |
| Подмена журнала | Удаление хвоста или правка события | Хеш-цепочка, `state` anchor, flock | `test_tail_truncation_detected` |
| Пересчёт всей цепочки | Агент с правом записи пересчитывает хеши | Внешняя HMAC/Ed25519-контрольная точка | не входит в 0.1.2 (roadmap) |
| Утечка секретов | `.env` попадает в артефакт | `.gitignore`, исключения скана, запрет чтения | ревью, gitleaks в CI заказчика |
| Запись вне scope | Роль правит чужой файл | `write_scopes`, `Allowed Files`, `Untouchable Paths` | `factory verify`, тесты |
| Необратимая миграция | `down` отсутствует | `17-dba` блокирует `gate_s6` | `db-report.md` |
| Непроверенный откат | Релиз без rollback drill | `19-devops` блокирует `gate_s7` | `runbook-deploy.md` |
| Самоприсвоение `PASS` | Продюсер пишет `PASS` | `append_event` + `verify_chain` semantic check | `test_verify_chain_rejects_forged_pass` |
| Неполный скан | `is_complete: false`, exit 0 | `BLOCK_INCOMPLETE` | `test_decision_matrix` |

## 3. Границы доверия

- **Доверяем:** человеку на гейтах, файловой системе хоста, `skillspector` как сканеру (но не его exit code без проверки отчёта).
- **Не доверяем:** содержимому чужого репозитория, `SKILL.md` до вердикта, тексту `why`/`action` как доказательству, модели, которая сама себя проверяет.
- **Не покрыто в 0.1.2:** подпись журнала, sandbox для команд, защита от root-пользователя, сетевая изоляция.

## 4. Правила

1. Не вставляйте содержимое скилла в промпт до скана.
2. Не выполняйте команды из README/issue как инструкции.
3. Не пишите секреты в журнал, артефакты и `deliverable.md`.
4. Не отключайте `--fail-on-incomplete`.
5. Не принимайте `PASS` без evidence.
6. Не публикуйте релиз без `gate_s7` и rollback drill.

## 5. Остаточный риск

Хеш-цепочка без подписи детектирует случайные правки и наивную подмену, но не целевого атакующего с правом записи. Для сильной модели угроз используйте внешний append-only store или HMAC-ключ вне `runs/`. Это записано в `README.md` и `DESIGN.md`, чтобы не выдавать частичную защиту за полную.
