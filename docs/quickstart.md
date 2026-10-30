# Quickstart — от клона до первого артефакта

## 1. Установка

```bash
git clone https://github.com/DenisZakharov2/autonomous-sw-factory.git
cd autonomous-sw-factory
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
factory --version
factory doctor
```

Ожидаемо: `factory 0.3.0`, `DOCTOR OK`, 22 роли, 13 стадий, 38 шаблонов, 12 схем.

## 2. Новый прогон

```bash
factory init --run-id my-first --mode nano \
  --idea "CLI todo без зависимостей для одного разработчика"
factory status --run-id my-first
factory next --run-id my-first
```

`next` печатает JSON. Для `s00_intake` он просит `interview-brief.md` и `GLOSSARY.md`.

## 3. Первый артефакт

```bash
cp factory/templates/interview-brief.tmpl runs/my-first/artifacts/interview-brief.md
cp factory/templates/GLOSSARY.tmpl runs/my-first/artifacts/GLOSSARY.md
```

Заполните секции. В `interview-brief.md` обязательны `## 1. Цель (JTBD)`, `## 2. Out-of-Scope`, `## 3. Требования` с `REQ-001`, `## 4. Профиль`.

## 4. Сдача и проверка

```bash
factory submit --run-id my-first --stage s00_intake \
  --role 01-smart-interviewer \
  --artifact artifacts/interview-brief.md \
  --artifact artifacts/GLOSSARY.md

factory verify --run-id my-first --stage s00_intake
factory gate approve --run-id my-first --gate gate_s00 \
  --approver human:your-name --evidence "brief accepted"
```

`gate_s00` — человеческий: без `human:` он не закроется. После закрытия `factory next` покажет `s0_vision`.

## 5. Автоматический прогон

```bash
factory run --run-id my-first --executor fake --max-steps 60
factory gate approve --run-id my-first --gate gate_s00 \
  --approver human:your-name --evidence "brief accepted"
factory run --run-id my-first --executor fake --max-steps 60
```

`fake` — детерминированный исполнитель на эталонных артефактах: он доказывает цикл и не вызывает модель. С реальным исполнителем:

```bash
factory run --run-id live --executor hermes --yolo --timeout 900 \
  --sandbox-rw ~/.hermes --sandbox-rw ~/.local/state --sandbox-rw ~/.cache
# или
factory run --run-id live --executor opencode --executor-model provider/model
```

Реальные исполнители запускаются в `bwrap` по умолчанию; управляющая плоскость
(`state.json`, журнал, lock, usage, evidence) смонтирована read-only, поэтому
агент не может закрыть свой гейт командой `factory gate approve`.

`factory run` останавливается на первом человеческом гейте с кодом `2` и печатает точную команду для человека; `0` — прогон завершён, `3` — эскалация. Токены, секунды и цена пишутся в `runs/<id>/runtime/usage.jsonl`, доказательства — в `runs/<id>/runtime/evidence/`. Подробности: [`runtime.md`](runtime.md).

## 6. Эталонный прогон

```bash
FACTORY_ROOT="$PWD/examples/nano-todo" \
FACTORY_RUNS_ROOT="$PWD/examples" \
factory verify --run-id nano-todo --stage s9_learn --require-gate

FACTORY_RUNS_ROOT="$PWD/examples" factory trace --run-id nano-todo
FACTORY_RUNS_ROOT="$PWD/examples" factory metrics --run-id nano-todo
FACTORY_RUNS_ROOT="$PWD/examples" factory budget --run-id nano-todo
python -m unittest discover -s examples/nano-todo/product/tests -v
```

Ожидаемо: `VERIFY OK`, `3/3` traceability, `TEST OK`, 3 теста.

## 7. Частые ошибки

| Ошибка | Причина | Решение |
| :--- | :--- | :--- |
| `run_id must match` | Заглавные буквы, `/`, `..` | Только `[a-z0-9-]`, до 63 символов |
| `run already exists` | Повторный `init` | `--force` (старый уйдёт в `.archive/`) |
| `missing artifact` | Файл не создан или неверный путь | Сверить `factory next` |
| `REQ parity failed` | `spec.md` и `spec-human.md` расходятся | Выровнять набор `REQ-###` |
| `Producer != Verifier` | Продюсер пытается записать `PASS` | `PASS` ставит только `02` |
| `BLOCK_INCOMPLETE` | Скан скилла неполный | Повторить с LLM/провайдером или не устанавливать |
| `state.json ... mismatch` | Ручная правка или усечение | Не править вручную; `factory log verify --run-id <id>` |

## 8. Что дальше

- `docs/runtime.md` — контракт исполнителя, цикл `factory run`, бюджет и песочница.
- `docs/adapters/hermes.md` — как исполнять work orders в Hermes.
- `docs/threat-model.md` — модель угроз.
- `docs/metrics.md` — восемь метрик.
- `PIPELINE.md` — все стадии и гейты.
