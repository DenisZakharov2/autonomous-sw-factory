# Metrics Log

`factory metrics --run-id <id> --write` writes `runs/<id>/artifacts/metrics.json`.
Append a one-line summary per run here after `s9_learn`:

```text
<run_id> | first_pass | rework | cycle_p50 | verification | traceability | cost
```
