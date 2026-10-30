# Metrics — восемь чисел и их источник

`factory metrics --run-id <id>` печатает JSON; `--write` кладёт его в `artifacts/metrics.json` по схеме `metrics.schema.json`.

| Метрика | Формула | Источник | `null` когда |
| :--- | :--- | :--- | :--- |
| `first_pass_rate` | accepted tasks with attempts=0 / accepted | `state.tasks` | нет принятых задач |
| `rework_rate` | (`REWORK` + `VETO` + `GATE_REJECTED`) / (tasks + stages) | journal | пустой прогон |
| `escaped_defect_rate` | defects after s5 / all defects | acceptance + journal | нет данных о дефектах |
| `cycle_time_p50_minutes` | median stage duration: previous gate approval (or INIT) → this gate approval | timestamps | меньше двух закрытых гейтов |
| `cycle_time_p90_minutes` | p90 stage duration (same bracket) | timestamps | меньше двух закрытых гейтов |
| `cost_per_artifact_usd` | tokens × price / artifacts | `model` event | рантайм не пишет токены/цену |
| `top_reject_reasons` | top-5 `reason_code` of FAIL | journal | нет FAIL |
| `verification_coverage` | approved gates / applicable stages (`s_in` excluded in greenfield) | state | нет применимых стадий |
| `traceability_coverage` | REQ with task+test+acceptance / REQ | traceability | нет traceability |

## Почему `null`, а не 0

`0` — это измеренное значение. `null` — «данных нет». Смешивать их значит врать в отчёте. `cost_per_artifact_usd` остаётся `null`, пока рантайм не передаст `tokens_in`, `tokens_out`, `cost_usd`; в `0.1.x` этих полей в событии нет.

## Как улучшать

- сначала `traceability_coverage = 1.0`;
- потом `first_pass_rate`;
- `rework_rate` снижать причинами, а не запретом отказов;
- `cycle_time` сравнивать на одинаковых режимах;
- `cost` не оптимизировать без данных.

## Пример

```json
{
  "first_pass_rate": 1.0,
  "rework_rate": 0.0,
  "escaped_defect_rate": null,
  "cycle_time_p50_minutes": 0.02,
  "cycle_time_p90_minutes": 0.03,
  "cost_per_artifact_usd": null,
  "top_reject_reasons": [],
  "verification_coverage": 1.0,
  "traceability_coverage": 1.0
}
```
