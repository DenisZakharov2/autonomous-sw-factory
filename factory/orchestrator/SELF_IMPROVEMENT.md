# SELF_IMPROVEMENT.md — контролируемое самообучение на `s9_learn`

## 1. Что считается

| Метрика | Формула | Источник |
| :--- | :--- | :--- |
| `first_pass_rate` | принятые задачи с `attempts == 0` / принятые | `state.tasks` |
| `rework_rate` | события `REWORK` / задачи | `blackbox.jsonl` |
| `escaped_defect_rate` | дефекты после `s5_acceptance` / все дефекты | acceptance + журнал |
| `cycle_time_p50/p90` | время между событиями стадии | timestamps |
| `cost_per_artifact_usd` | токены × цена / артефакты | `model` события; пока `null` |
| `top_reject_reasons` | распределение `reason_code` у `FAIL` | журнал |
| `verification_coverage` | закрытые гейты / стадии | state |
| `traceability_coverage` | REQ с task+test+acceptance / все REQ | traceability |

`factory metrics --run-id <id> --write` пишет `artifacts/metrics.json`.

## 2. Что можно улучшать

- формулировки шаблонов;
- тексты ролей (кроме инвариантов);
- пороги и бюджеты;
- тесты и eval-набор.

## 3. Что нельзя

- ослаблять `Producer != Verifier`;
- отключать `--fail-on-incomplete`;
- убирать человеческие гейты `s1_spec`, `s7_release`, `s9_learn`;
- менять `factory/pipeline.yaml` или `roles/` без человеческого коммита;
- применять патч, который ломает инвариантный тест.

## 4. Процесс

1. `factory metrics --run-id <id> --write`.
2. Собрать 3–5 архивных прогонов как eval-базу.
3. Предложить патч в `artifacts/patches/<id>.diff`.
4. Прогнать `pytest`, `factory doctor`, `factory verify` на архивах.
5. Человек читает diff, метрики до/после и принимает решение.
6. Только после `gate_s9` патч попадает в отдельный коммит с записью в `CHANGELOG.md`.

## 5. Критерий отказа

Патч отклоняется, если:

- падает хотя бы один инвариантный тест;
- `verification_coverage` или `traceability_coverage` ниже базы;
- `first_pass_rate` падает без объяснения;
- патч трогает `pipeline.yaml`, `roles/` или инварианты без отдельного human-коммита;
- нет метрик до/после.

Самообучение без eval — это переписывание промптов с ускорением. Поэтому `0.1.2` считает метрики, но не применяет патчи автоматически.
