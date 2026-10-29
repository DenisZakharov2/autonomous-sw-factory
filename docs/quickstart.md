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

Ожидаемо: `factory 0.1.0`, `DOCTOR OK`, 22 роли, 13 стадий, 38 шаблонов, 9 схем.

## 2. Новый прогон

```bash
factory init --run-id my-first --mode nano
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

## 5. Эталонный прогон

```bash
FACTORY_ROOT="$PWD/examples/nano-todo" \
FACTORY_RUNS_ROOT="$PWD/examples" \
factory verify --run-id nano-todo --stage s9_learn --require-gate

FACTORY_RUNS_ROOT="$PWD/examples" factory trace --run-id nano-todo
FACTORY_RUNS_ROOT="$PWD/examples" factory metrics --run-id nano-todo
python -m unittest discover -s examples/nano-todo/product/tests -v
```

Ожидаемо: `VERIFY OK`, `3/3` traceability, `TEST OK`, 3 теста.

## 6. Частые ошибки

| Ошибка | Причина | Решение |
| :--- | :--- | :--- |
| `run_id must match` | Заглавные буквы, `/`, `..` | Только `[a-z0-9-]`, до 63 символов |
| `run already exists` | Повторный `init` | `--force` (старый уйдёт в `.archive/`) |
| `missing artifact` | Файл не создан или неверный путь | Сверить `factory next` |
| `REQ parity failed` | `spec.md` и `spec-human.md` расходятся | Выровнять набор `REQ-###` |
| `Producer != Verifier` | Продюсер пытается записать `PASS` | `PASS` ставит только `02` |
| `BLOCK_INCOMPLETE` | Скан скилла неполный | Повторить с LLM/провайдером или не устанавливать |
| `state.json ... mismatch` | Ручная правка или усечение | Не править вручную; `factory log verify` |

## 7. Что дальше

- `docs/adapters/hermes.md` — как исполнять work orders в Hermes.
- `docs/threat-model.md` — модель угроз.
- `docs/metrics.md` — восемь метрик.
- `PIPELINE.md` — все стадии и гейты.
