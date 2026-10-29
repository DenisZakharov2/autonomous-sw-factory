# FACTORY_GLOSSARY.md — словарь терминов Фабрики

Продуктовый словарь создаётся в `runs/<run_id>/artifacts/GLOSSARY.md` и не перезаписывает этот файл.

| Термин | Определение |
| :--- | :--- |
| `run` | Один изолированный прогон в `runs/<run_id>/` со своим state, журналом и артефактами |
| `stage` | Стадия конвейера с id, порядком, ролями, артефактами и гейтом |
| `gate` | Точка решения: человек или владелец роли проверяет evidence и закрывает/отклоняет стадию |
| `work order` | JSON от `factory next`: что производить, кому, по какому шаблону и что дальше |
| `artifact` | Файл-результат стадии, зарегистрированный в `factory/artifacts.yaml` |
| `template` | Стартовая форма артефакта; не ставит `PASS` |
| `schema` | JSON Schema для JSON-артефактов и событий |
| `role card` | Карточка роли с восемью разделами и frontmatter |
| `producer` | Роль, создающая артефакт |
| `verifier` | `02-independent-verifier`; единственный, кто пишет `PASS` |
| `veto` | `blocking` (остановка) или `advisory` (рекомендация) |
| `evidence` | Ссылка на файл, команду, вывод или хеш, подтверждающая вердикт |
| `REQ-###` | Сквозной идентификатор требования |
| `traceability` | Матрица `REQ → spec → task → test → acceptance` |
| `runbook` | Регламент деплоя или эксплуатации с проверенными командами |
| `rollback drill` | Фактический прогон отката с записанным выводом и RTO |
| `restore drill` | Фактическое восстановление Brownfield-бэкапа |
| `Untouchable Paths` | Glob-список путей, которые нельзя менять |
| `SkillSpector` | Внешний сканер скиллов; вердикты `SAFE`/`CAUTION`/`DO_NOT_INSTALL` |
| `quarantine` | Карантин скиллов до вердикта; fail-closed |
| `lock` | `skills-lock.json` с `commit`, `sha256`, `license`, `scanner` |
| `blackbox` | `blackbox.jsonl`, хеш-цепочка событий |
| `state anchor` | Поля `last_event_hash` и `event_count`, обнаруживающие усечение |
| `Nano` | Режим 4 ролей: 00, 01, 12, 02 |
| `Standard` | Режим 12 ролей |
| `Enterprise` | Все 22 роли |
| `Brownfield` | Существующий git-репозиторий; обязателен backup/restore drill |
| `PASS` | Вердикт верификатора; не ставится продюсером |
| `RISK_ACCEPTANCE` | Событие человека о принятии риска с сроком и ответственным |
