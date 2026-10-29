# SKILL_INSPECTOR_PROTOCOL.md — карантин SkillSpector 2.12.0

## 1. Команда

```bash
factory quarantine scan ./skills/<name> \
  --name <name> --pack <pack> --commit <40-hex|local> --license <SPDX> \
  --lock skills-lock.json --min-coverage 95
```

Внутри вызывается:

```bash
skillspector scan <path> --format json --output report.json \
  --fail-on-incomplete --min-coverage 95 [--no-llm]
```

Содержимое скилла не вставляется в промпт. Скан идёт подпроцессом; модель видит только отчёт.

## 2. Что читается из отчёта

| Поле отчёта | Использование |
| :--- | :--- |
| `risk_assessment.recommendation` | `SAFE` / `CAUTION` / `DO_NOT_INSTALL` |
| `risk_assessment.severity` | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL` |
| `analysis_completeness.is_complete` | Полнота анализа |
| `analysis_completeness.coverage_percent` | Порог 95% |
| `analysis_completeness.analyzer_statuses[].status` | `failed`/`partial` → блок |
| `metadata.skillspector_version` | Версия в локе |

## 3. Таблица решений

| `recommendation` | `is_complete` | coverage | exit | Решение |
| :--- | :--- | :--- | :--- | :--- |
| `SAFE` | `true` | ≥ 95 | 0 | `AUTO_ALLOW`, запись лока |
| `CAUTION` | `true` | ≥ 95 | 0 | `ESCALATE_HUMAN` |
| `DO_NOT_INSTALL` | любое | любое | 0 | `REJECT_AND_PURGE` |
| `SAFE`/`CAUTION` | `false` | любое | любой | `BLOCK_INCOMPLETE` |
| любое | `true` | < 95 | 0 | `BLOCK_INCOMPLETE` |
| любое | `true` | ≥ 95 | ≠ 0 | `BLOCK_RETRY` |
| `SAFE` | `true` | ≥ 95 | 0, severity `MEDIUM+` | `REJECT_AND_PURGE` |

Fail-closed: если поле отсутствует, считается `DO_NOT_INSTALL`/`CRITICAL`/`false`.

## 4. Лок

```json
{
  "schema_version": "1.0.0",
  "skills": [
    {
      "name": "grilling",
      "pack": "mattpocock/skills",
      "commit": "<40-hex>",
      "path": "skills/grilling",
      "sha256": "<64-hex>",
      "license": "MIT",
      "model_invocable": true,
      "scanner": {
        "name": "skillspector",
        "version": "2.12.0",
        "severity": "LOW",
        "recommendation": "SAFE",
        "is_complete": true,
        "coverage_percent": 100.0,
        "scanned_at": "2026-10-29T00:00:00Z"
      },
      "approved_by": "human:name"
    }
  ]
}
```

`sha256` — хеш каталога: относительные пути + байты файлов в детерминированном порядке. Проверка:

```bash
factory quarantine verify-lock --lock skills-lock.json --skills-root .
```

Подмена любого файла даёт `sha256 mismatch`.

## 5. Запреты

- не устанавливать скилл до `AUTO_ALLOW` или человеческого одобрения;
- не читать `SKILL.md` в контекст до вердикта;
- не принимать `is_complete: false` за безопасность;
- не считать exit code 0 доказательством полноты;
- не писать в лок запись без `commit`, `sha256`, `license`, `scanner`.

## 6. Ограничения

`skillspector 2.12.0` может падать на семантических анализаторах без LLM-ключа. Это не «безопасно»: это `BLOCK_INCOMPLETE`. Запускайте с `--no-llm` для быстрой статической проверки или настройте провайдера; в обоих случаях решение принимает таблица, а не текст отчёта.
