# Handoff — 1.0.0

Состояние на 31 октября 2026. Ветка `main`, версия `1.0.0`, 235 тестов.
Eval-набор зелёный на реальном Hermes: `eval/baseline-hermes.json` хранит
пять записей — `nano-todo` (`runs/nano-todo-20261030-191409`),
`standard-mini` (`runs/standard-mini-standard-real`),
`brownfield-proxy-mini` (`runs/brownfield-proxy-mini-brownfield-real2`),
`product-mini` (`runs/product-mini-product-real`) и `nano-product-mini`
(`runs/nano-product-mini-nano-product-real`). Fake-эталон CI
(`eval/baseline.json`) перегенерирован 31.10 и проходит.

## Что вошло в 1.0.0

- **`nano` на реальном продукте:** кейс `nano-product-mini` на крейте `pfu`
  (seed 252 КБ, commit `dfe54336…`), 13 шагов, 33 события, 2 436 448 total /
  390 496 billable, 224 product tests, запись в `eval/baseline-hermes.json`.
- **Промпт режима стал универсальным:** todo-правила `nano` переехали в
  `eval/cases/nano-todo/constraints.md`; страж —
  `test_nano_prompt_carries_no_case_specific_rules`.
- **Потолок `nano` пересчитан:** 2.5M → 3.1M токенов (худший зелёный прогон
  + 25 %), минуты те же 120.
- **Fake-эталон перегенерирован** для двух CI-кейсов: 13/33 и 15/35.
- **CI на трёх ОС зелёный** на `5326616` (run `41215608643`, 14/14); тест
  минутного бюджета больше не флейкает на windows-latest 3.11.
- **Найдено ограничение:** `traceability` проверяет существование файла
  теста, а не имя функции — закрыто в 1.0.1 (см. «Известные ограничения», п. 9).

## Что вошло в 0.3.0

- **Подписанные контрольные точки (`factory sign`).** HMAC-SHA256 от головы
  журнала, якорь в store вне `runs/`, `verify` различает `signed` / `stale` /
  `rolled_back` / `diverged`.
- **Внешний eval-набор (`factory eval`).** Два кейса, инварианты из
  `expected.json`, human-гейты от `human:eval`, отдельные baseline для fake и
  для реального исполнителя.
- **Сравнение прогонов (`factory compare`).** Дельта по стадиям, итоги и хеши
  артефактов вне control plane.
- **Реальный прогон `nano-todo` на Hermes.** Зелёный кейс, baseline
  `eval/baseline-hermes.json`, разбор цены по шагам — ниже.
- **Два исправления рантайма и движка, без которых зелёного прогона не было**
  (подробно в разделе «Что исправлено»).

## Зелёный прогон на реальном Hermes

Конфигурация (принята на весь прогон, не менялась):

```text
executor        hermes
model           deepseek-ai/DeepSeek-V4.1-Flash-1017
reasoning       high            (medium провайдер отвергает HTTP 400)
tools           auto            (file; file,terminal на s4_build)
sandbox         bwrap, run dir writable + ro-bind поверх control plane
sandbox-rw      ~/.hermes/installs/<hash>, ~/.hermes/cache, ~/.hermes/logs
timeout         900 s на шаг, --yolo
budget          3 000 000 total / 120 минут (флаги замера;
                дефолт режима `nano` сейчас — 3.1M / 120 мин)
```

Команда:

```bash
export FACTORY_RUNS_ROOT="$PWD/runs"
factory eval --cases "$PWD/runs/hermes-cases" --executor hermes \
  --executor-model deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  --executor-tools auto --reasoning high --timeout 900 --yolo \
  --sandbox-rw /home/anony/.hermes/installs/74e85d3cd01c2ec7 \
  --sandbox-rw ~/.hermes/cache --sandbox-rw ~/.hermes/logs \
  --budget-tokens 3000000 --budget-minutes 120 \
  --update-baseline eval/baseline-hermes.json --json
```

Итог: `run_dir = runs/nano-todo-20261030-191409`, `verify` — OK,
`status=complete`, все 12 гейтов закрыты.

| Метрика | Значение |
| :--- | ---: |
| steps (рабочие шаги) | 13 |
| loop_steps | 29 |
| events | 33 |
| rework_events | 0 |
| tasks / accepted | 2 / 2 |
| pass@1 | 1.0 |
| verification_coverage | 1.0 |
| traceability_coverage | 1.0 |
| tokens total | 1 780 308 |
| tokens billable (in+out) | 336 980 |
| cache read | 1 443 328 (81 % total) |
| api_calls | 85 |
| секунды исполнителя | 605.7 |
| wall time кейса | 10 мин 29 с |
| prompt_chars | 108 156 |
| usd | 0.0 — **не цена** (см. ниже) |
| product tests | 14 passed (pytest, `product/tests`) |
| checks | 3 passed (`checks/test_nano_invariants.py`) |

### Цена по шагам

| step | stage | task | role | total | in | cache | out | billable | api | sec |
| ---: | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | s00_intake | — | 01-smart-interviewer | 21 997 | 7 263 | 13 056 | 1 678 | 8 941 | 2 | 21.8 |
| 2 | s0_vision | — | 01-smart-interviewer | 39 265 | 10 822 | 25 344 | 3 099 | 13 921 | 3 | 28.5 |
| 3 | s1_spec | — | 01-smart-interviewer | 86 560 | 14 334 | 66 048 | 6 178 | 20 512 | 5 | 44.6 |
| 4 | s15_quarantine | — | 00-orchestrator | 56 856 | 8 355 | 47 104 | 1 397 | 9 752 | 5 | 22.1 |
| 5 | s2_architecture | — | 12-fullstack-developer | 148 646 | 29 486 | 104 192 | 14 968 | 44 454 | 6 | 88.5 |
| 6 | s3_plan | — | 01-smart-interviewer | 88 175 | 14 470 | 70 400 | 3 305 | 17 775 | 6 | 30.2 |
| 7 | s4_build | TASK-001 | 12-fullstack-developer | 155 696 | 20 141 | 129 280 | 6 275 | 26 416 | 9 | 48.9 |
| 8 | s4_build | TASK-002 | 12-fullstack-developer | 170 633 | 19 656 | 144 128 | 6 849 | 26 505 | 8 | 51.4 |
| 9 | s5_acceptance | — | 12-fullstack-developer | 199 863 | 32 659 | 158 208 | 8 996 | 41 655 | 8 | 62.6 |
| 10 | s6_security | — | 12-fullstack-developer | 186 701 | 23 900 | 155 136 | 7 665 | 31 565 | 9 | 55.2 |
| 11 | s7_release | — | 12-fullstack-developer | 194 107 | 28 513 | 156 672 | 8 922 | 37 435 | 8 | 60.4 |
| 12 | s8_docs | — | 12-fullstack-developer | 50 053 | 7 816 | 40 192 | 2 045 | 9 861 | 4 | 23.5 |
| 13 | s9_learn | — | 00-orchestrator | 381 756 | 38 344 | 333 568 | 9 844 | 48 188 | 12 | 68.0 |

### Про `usd`

`runtime/usage.jsonl` пишет `usd` из `estimated_cost_usd` ответа Hermes.
Провайдер отдаёт `0.0` при `cost_status: unknown`, поэтому в baseline стоит
`usd: 0.0`. Это **не измеренная стоимость**: цена не считается, а бюджет
`--budget-usd` не проверяется. `cost_per_artifact_usd` в метриках — `null`.
Пока провайдер не отдаёт реальную цену, сравнивать прогоны по деньгам нельзя;
сравнивайте `tokens_billable` (336 980).

### Где дороже всего

В этом прогоне `s4_build` — не половина счёта: 326 329 total (18 %), по
155–170k на задачу. Дороже всех хвост: `s9_learn` 381 756 (21 %), затем
`s5_acceptance` 199 863, `s7_release` 194 107, `s6_security` 186 701.
Прежняя оценка «`s4_build` — 70–80 % расхода» верна для прогонов, где задача
стоила 400–800k; после правок промпта (запрет читать `factory/**`, правила
валидатора в тексте, две задачи в плане, подсказка `s15`) цена задачи упала
в 3–5 раз, и расход переехал в содержательные хвостовые стадии.

Разброс между прогонами одной модели и конфигурации честно большой:

| прогон | total | billable | s2_arch | s4_build | s9_learn | итог |
| :--- | ---: | ---: | ---: | ---: | ---: | :--- |
| 183641 | 1 962 231 | 366 583 | 157k | 524k | 309k | красный: 7 `REQ`, unittest |
| 185720 | 1 853 041 | 326 257 | 164k | 669k | 249k | красный: переписана `traceability` |
| 191409 | 1 780 308 | 336 980 | 149k | 326k | 382k | **зелёный** |

`tokens_billable` стабильнее total (±3 %), total — ±5 %; одна стадия может
различаться в 2.6 раза (`s9_learn` 249k → 382k). Один прогон не доказывает
улучшение; для сравнения нужны baseline и `factory compare`.

## standard-mini на реальном Hermes

Тот же профиль, что у `nano-todo`: одна модель, `--reasoning high`,
`--executor-tools auto`, `bwrap`, `--yolo`; бюджет замера 4M / 240 минут
(дефолт `standard` после пересчёта — 2.9M / 120 минут).

```bash
factory eval --cases "$PWD/runs/hermes-cases" --executor hermes \
  --executor-model deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  --executor-tools auto --reasoning high --timeout 900 --yolo \
  --budget-tokens 4000000 --budget-minutes 240 --stamp standard-real --json
```

Итог: `run_dir = runs/standard-mini-standard-real`, `status=complete`,
`verify` — OK, все 12 гейтов закрыты как `human:eval`.

| Метрика | Значение |
| :--- | ---: |
| steps / loop_steps / events | 15 / 31 / 35 |
| tasks / accepted | 2 / 2 |
| pass@1 | 1.0 |
| verification_coverage / traceability_coverage | 1.0 / 1.0 |
| tokens total | 1 978 597 |
| tokens billable | 363 237 |
| api_calls | 97 |
| секунды исполнителя / wall | 653 / 673 (11,2 мин) |
| prompt_chars | 139 753 |
| product tests | 11 passed |
| usd | 0.0 — не цена (`cost_status: unknown`) |

Расход: `s4_build` 748 589 (37,8 %, задачи 325k и 424k), `s6_security`
229 834 (две роли), `s9_learn` 168 982, `s7_release` 152 320,
`s5_acceptance` 146 650, `s2_architecture` 146 133.

Рамки `constraints.md` удержаны: в `spec.md` и `spec-human.md` ровно
`REQ-001/002/003`, в плане и `tasks/` ровно `TASK-001` и `TASK-002`,
`traceability.json` ссылается только на них, `s1_spec` сдали две роли
раздельно (`05` — spec/spec-human/traceability, `06` — domain-model/
access-matrix). Секция «Ограничения кейса» есть в каждом записанном промпте.

Пилот на трёх шагах (`runs/standard-mini-20261030-204101`) дал 2 модельных
шага: 61 101 total / 21 933 billable, 5 API-вызовов, 46 с. Экстраполяция по
профилю `nano` (137–151k на шаг) предсказала 2,05–2,26M на 15 шагов; факт —
1,98M.

`factory compare standard-mini-fake-compare standard-mini-standard-real`:
шаги и события совпадают постадийно (15/15, 35/35), токены только у реального
прогона — 363 237 billable. `only_left` в хешах (`product/src/todo.py`,
`screens/cli.md`) — фикстура `FakeExecutor` от `nano`, а не продукт `standard`.

Побочно: закоммиченный fake-эталон `eval/baseline.json` записан до фикса
«стадия без pending не зовёт модель» (`f1895da`) и хранит 14/33 и 16/35,
тогда как текущий fake даёт 13/33 и 15/35. Тест этого не ловит: падение шагов
не считается регрессией. Перегенерация — отдельная задача, fake-эталон здесь
не менялся.

## brownfield-proxy-mini на реальном Hermes

Первый brownfield-прогон на реальном продукте: публичный
`proxy-for-ubuntu` 0.4.0 (Rust/Tauri, commit `dfe54336…`, 12 МБ с `.git`),
seed-архив в `eval/cases/brownfield-proxy-mini/seed/`. Та же модель и
конфигурация, что у `nano-todo`: `deepseek-ai/DeepSeek-V4.1-Flash-1017`,
`--reasoning high`, `--executor-tools auto`, `bwrap`, `--yolo`.

```bash
export FACTORY_RUNS_ROOT="$PWD/runs"
export CARGO_TARGET_DIR="$PWD/runs/brownfield-src/proxy-for-ubuntu/app/src-tauri/target"
factory eval --cases "$PWD/runs/hermes-cases-brownfield" --executor hermes \
  --executor-model deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  --executor-tools auto --reasoning high --timeout 900 --yolo \
  --sandbox-rw /home/anony/.hermes/installs/74e85d3cd01c2ec7 \
  --sandbox-rw ~/.hermes/cache --sandbox-rw ~/.hermes/logs \
  --sandbox-rw "$CARGO_TARGET_DIR" \
  --budget-tokens 4500000 --budget-minutes 240 --stamp brownfield-real --json
```

Первая попытка шла с `--budget-tokens 3000000` и остановилась на `s7_release`
при **3 115 133 total**; потолок поднят не на ходу, а в продолжении того же
журнала (`factory run` с 4.5M). Команда выше — то, чем case нужно запускать
с нуля.

| Метрика | Значение |
| :--- | ---: |
| steps / loop_steps / events | 17 / 32 / 38 |
| rework_events | 0 |
| tasks / accepted | 2 / 2 |
| pass@1 | 1.0 |
| verification_coverage / traceability_coverage | 1.0 / 1.0 |
| tokens total | 3 560 791 |
| tokens billable | 528 727 |
| cache read | 3 032 064 |
| api_calls | 155 |
| секунды исполнителя / wall | 1033 / ~1290 |
| prompt_chars | 194 916 |
| product tests | 222 passed (`cargo test --lib`, +4 новых) |
| restore drill | 140/140 tracked files, HEAD/status/sha256 совпали |

### Цена по шагам

| step | stage | task | role | total | in | cache | out | billable | api | sec |
| ---: | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | s_in | — | 01-smart-interviewer | 123 404 | 18 268 | 100 352 | 4 784 | 23 052 | 8 | 43.8 |
| 2 | s_in | — | 08-software-architect | 206 822 | 26 541 | 174 592 | 5 689 | 32 230 | 10 | 46.4 |
| 3 | s00_intake | — | 01-smart-interviewer | 23 852 | 10 555 | 10 496 | 2 801 | 13 356 | 2 | 27.6 |
| 4 | s0_vision | — | 03-product-owner | 56 538 | 14 685 | 37 632 | 4 221 | 18 906 | 4 | 37.3 |
| 5 | s1_spec | — | 05-business-analyst | 51 978 | 10 449 | 37 632 | 3 897 | 14 346 | 4 | 33.6 |
| 6 | s1_spec | — | 06-system-analyst | 94 569 | 17 088 | 72 704 | 4 777 | 21 865 | 6 | 39.9 |
| 7 | s15_quarantine | — | 16-security-engineer | 61 101 | 11 214 | 48 128 | 1 759 | 12 973 | 5 | 27.0 |
| 8 | s2_architecture | — | 08-software-architect | 290 984 | 39 744 | 235 520 | 15 720 | 55 464 | 10 | 104.6 |
| 9 | s3_plan | — | 04-team-lead | 38 679 | 9 427 | 26 112 | 3 140 | 12 567 | 3 | 30.1 |
| 10 | s4_build | TASK-001 | 12-fullstack-developer | 488 901 | 40 175 | 438 272 | 10 454 | 50 629 | 17 | 135.6 |
| 11 | s4_build | TASK-002 | 12-fullstack-developer | 823 512 | 43 317 | 764 672 | 15 523 | 58 840 | 28 | 157.4 |
| 12 | s5_acceptance | — | 14-qa-engineer | 399 088 | 43 422 | 344 832 | 10 834 | 54 256 | 14 | 77.8 |
| 13 | s6_security | — | 16-security-engineer | 251 117 | 34 375 | 207 360 | 9 382 | 43 757 | 10 | 66.7 |
| 14 | s6_security | — | 12-fullstack-developer | 77 368 | 10 812 | 62 720 | 3 836 | 14 648 | 6 | 39.8 |
| 15 | s7_release | — | 19-devops-engineer | 127 220 | 23 072 | 96 256 | 7 892 | 30 964 | 7 | 59.1 |
| 16 | s8_docs | — | 12-fullstack-developer | 284 204 | 30 107 | 247 808 | 6 289 | 36 396 | 13 | 53.1 |
| 17 | s9_learn | — | 00-orchestrator | 161 454 | 27 708 | 126 976 | 6 770 | 34 478 | 8 | 53.0 |

`s4_build` — 1.31M (37 %), из них `TASK-002` 824k; `s2_architecture` 291k и
`s5_acceptance` 399k — следующие. Ровно две задачи, `TASK-002` зависит от
`TASK-001`, обе правят `app/src-tauri/src/config.rs`.

### Что дал seed и как прошёл drill

- `seed/repo.tar.gz` + `seed/seed.json`: 9,7 МБ, commit и sha256 пинятся,
  `doctor` проверяет hash/path traversal/`.git`. Распаковка идёт после
  `init_run` и до `_drive_run`, `HEAD` сверяется с пином.
- `s_in` выполнен ролью `01` с `file,terminal`: `git bundle create --all`,
  `git ls-files | tar`, `sha256sum -c`, `git clone` в `backups/restore-check`.
  Drill восстановил 140/140 отслеживаемых файлов, `HEAD` и
  `git status --porcelain --untracked-files=no` совпали; `restore-drill.md` —
  `PASS`. `checks/test_brownfield_evidence.py` подтвердил это после прогона.
- Brownfield-режим переопределяет `product_src`/`product_tests` на
  `app/src-tauri/{src,tests}`; без этого `s4_build` требовал пустой
  `product/src`, которого в реальном репозитории нет.

### Цена cargo

`cargo test --lib` на 4 ядрах: холодная сборка **567 с**, тёплая **54 с**,
218 тестов до изменений и **222** после. `timeout_seconds` в схеме — 600,
поэтому прогретый `CARGO_TARGET_DIR` передаётся и агенту (`--sandbox-rw`), и
`_product_tests`; `cargo clean` в задаче запрещён.

## product-mini на реальном Hermes

Второй standard-кейс на том же продукте: крейт `pfu` из `proxy-for-ubuntu`
(commit `dfe54336…`), разложенный в `product/` без `.git` (standard не делает
restore drill). Задача — backoff повторных попыток подписки в
`subscription.rs`; seed 252 КБ, `require_git: false`. Профиль тот же, что у
`standard-mini`; бюджет замера 3M / 120 минут.

Итог: `status=complete`, `verify` — OK, все 12 гейтов, 15 шагов, 35 событий,
2 задачи, **2 284 755 total / 430 803 billable**, 97 API-вызовов, 737 с
(12,3 мин), **228 product tests** (218 + 10 новых), coverage 1.0/1.0.
Запись — в `eval/baseline-hermes.json`.

| step | stage | task | role | total | api | sec |
| ---: | :--- | :--- | :--- | ---: | ---: | ---: |
| 1 | s00_intake | — | 01-smart-interviewer | 24 263 | 2 | 21.1 |
| 2 | s0_vision | — | 03-product-owner | 41 788 | 3 | 30.0 |
| 3 | s1_spec | — | 05-business-analyst | 58 574 | 4 | 31.1 |
| 4 | s1_spec | — | 06-system-analyst | 97 013 | 6 | 38.6 |
| 5 | s15_quarantine | — | 16-security-engineer | 35 027 | 3 | 19.3 |
| 6 | s2_architecture | — | 08-software-architect | 241 673 | 9 | 95.4 |
| 7 | s3_plan | — | 04-team-lead | 40 207 | 3 | 21.8 |
| 8 | s4_build | TASK-001 | 12-fullstack-developer | 199 599 | 8 | 80.3 |
| 9 | s4_build | TASK-002 | 12-fullstack-developer | 357 940 | 11 | 83.3 |
| 10 | s5_acceptance | — | 14-qa-engineer | 269 230 | 8 | 53.7 |
| 11 | s6_security | — | 16-security-engineer | 148 350 | 6 | 32.9 |
| 12 | s6_security | — | 12-fullstack-developer | 77 178 | 5 | 41.7 |
| 13 | s7_release | — | 19-devops-engineer | 221 359 | 9 | 83.0 |
| 14 | s8_docs | — | 12-fullstack-developer | 263 970 | 11 | 47.7 |
| 15 | s9_learn | — | 00-orchestrator | 208 584 | 9 | 57.3 |

`factory compare product-mini-product-fake product-mini-product-real`: шаги и
события совпадают постадийно (15/15, 35/35); токены только у реального —
430 803 billable. `only_left` — фикстуры `FakeExecutor` (`product/src/todo.py`,
`product/tests/test_todo.py`), `only_right` — реальные файлы кейса.

Потолок `standard` пересчитан по худшему из двух зелёных прогонов:
2 284 755 + 25 % → **2.9M / 120 минут** (было 2.5M). Время не является
узким местом: факт 12,3 минуты.

## nano-product-mini на реальном Hermes

Первый nano-кейс на реальном продукте и первый nano-кейс с seed: режим
`nano`, но продукт — крейт `pfu` из `proxy-for-ubuntu` (commit `dfe54336…`),
разложенный в `product/` без `.git` (seed 252 КБ — тот же архив, что у
`product-mini`; 9,7 МБ brownfield-репозитория не дублируется). Задача —
действующие лимиты канала: `LimitsConfig::effective_for` в `config.rs`,
отказ `validate`/`validate_for_read` на неизвестный ключ `limits.outbound`
и переход `Engine::build` на новый метод в `router.rs`.

Сначала todo-специфичные правила nano переехали из промпта режима в
`eval/cases/nano-todo/constraints.md`: промпт `nano` больше не знает про
`REQ-001 add` и «ровно две задачи», а рамки кейса знают. Fake `nano-todo`
после переноса не изменился — 13 шагов / 33 события.

Профиль тот же: одна модель `deepseek-ai/DeepSeek-V4.1-Flash-1017`,
`--reasoning high`, `--executor-tools auto`, `bwrap`, `--yolo`; бюджет
замера 2.5M / 120 минут; `CARGO_TARGET_DIR` — прогретый brownfield-target.

```bash
export FACTORY_RUNS_ROOT="$PWD/runs"
export CARGO_TARGET_DIR="$PWD/runs/brownfield-src/proxy-for-ubuntu/app/src-tauri/target"
factory eval --cases "$PWD/runs/hermes-cases-nano-product" --executor hermes \
  --executor-model deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  --executor-tools auto --reasoning high --timeout 900 --yolo \
  --sandbox-rw /home/anony/.hermes/installs/74e85d3cd01c2ec7 \
  --sandbox-rw ~/.hermes/cache --sandbox-rw ~/.hermes/logs \
  --sandbox-rw "$CARGO_TARGET_DIR" \
  --budget-tokens 2500000 --budget-minutes 120 --stamp nano-product-real --json
```

Итог: `status=complete`, `verify` — OK, все 12 гейтов, **13 шагов, 33
события, 2 задачи**, 2 436 448 total / 390 496 billable, 95 API-вызовов,
707 с (11,8 мин), **224 product tests** (`cargo test --lib`; 218 до
изменений, 6 новых), coverage 1.0/1.0. Прогон уложился в 2.5M на 97,5 % —
прежний потолок больше не годится, `nano` пересчитан по правилу «+25 % от
худшего зелёного»: **3.1M / 120 минут**.

| step | stage | task | role | total | in | cache | out | billable | api | sec |
| ---: | :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | s00_intake | — | 01-smart-interviewer | 24 732 | 12 576 | 10 240 | 1 916 | 14 492 | 2 | 24.1 |
| 2 | s0_vision | — | 01-smart-interviewer | 42 391 | 15 792 | 23 808 | 2 791 | 18 583 | 3 | 30.0 |
| 3 | s1_spec | — | 01-smart-interviewer | 106 000 | 15 459 | 83 968 | 6 573 | 22 032 | 6 | 47.3 |
| 4 | s15_quarantine | — | 00-orchestrator | 61 683 | 13 358 | 47 104 | 1 221 | 14 579 | 5 | 23.2 |
| 5 | s2_architecture | — | 12-fullstack-developer | 195 513 | 26 464 | 156 416 | 12 633 | 39 097 | 8 | 77.7 |
| 6 | s3_plan | — | 01-smart-interviewer | 57 921 | 9 134 | 46 080 | 2 707 | 11 841 | 4 | 24.7 |
| 7 | s4_build | TASK-001 | 12-fullstack-developer | 705 840 | 64 761 | 632 576 | 8 503 | 73 264 | 13 | 106.8 |
| 8 | s4_build | TASK-002 | 12-fullstack-developer | 401 727 | 28 607 | 367 872 | 5 248 | 33 855 | 15 | 102.1 |
| 9 | s5_acceptance | — | 12-fullstack-developer | 129 549 | 23 797 | 100 352 | 5 400 | 29 197 | 7 | 45.7 |
| 10 | s6_security | — | 12-fullstack-developer | 66 611 | 13 490 | 48 384 | 4 737 | 18 227 | 4 | 37.4 |
| 11 | s7_release | — | 12-fullstack-developer | 281 712 | 38 097 | 230 144 | 13 471 | 51 568 | 10 | 87.0 |
| 12 | s8_docs | — | 12-fullstack-developer | 180 273 | 23 310 | 151 552 | 5 411 | 28 721 | 9 | 45.4 |
| 13 | s9_learn | — | 00-orchestrator | 182 496 | 28 204 | 147 456 | 6 836 | 35 040 | 9 | 55.9 |

`s4_build` — 1 107 567 total (45 %): `TASK-001` 705 840 (13 вызовов, правка
2 078-строчного `config.rs` и 6 новых тестов), `TASK-002` 401 727 (15
вызовов, `router.rs`). Дороже всего после сборки — `s7_release` 281 712 и
`s2_architecture` 195 513. Дорогая `TASK-001` — плата за чтение большого
файла и `cargo`-цикл в терминале, а не за rework: `rework_events` = 0.

Рамки кейса удержаны буквально: в `spec.md`/`spec-human.md` ровно
`REQ-001..003`, в плане ровно `TASK-001`/`TASK-002` (`TASK-002` с
`depends_on: TASK-001`), `traceability.json` ссылается только на них,
`s1_spec` сдала одна роль `01-smart-interviewer` — в `nano` ролей `05`/`06`
нет.

Продукт изменён по идее, а не только по тестам: `effective_for` сливает
глобальные лимиты с переопределением (`0` в `rate_limit` — «без
ограничения», неизвестное имя получает глобальные значения),
`validate_inner` отвергает неизвестный ключ `limits.outbound` и называет его
в ошибке, `Engine::build` собирает `outbound_limits` через новый метод.

`factory compare nano-product-mini-casecheck nano-product-mini-nano-product-real`:
шаги и события совпадают постадийно (13/13, 33/33); токены только у реального
прогона — 390 496 billable. `only_left` — фикстуры `FakeExecutor`
(`product/src/todo.py`, `product/tests/test_todo.py`, `screens/cli.md`),
`only_right` — реальные артефакты кейса (`adr/ADR-002.md`, два патча,
`screens/config-limits.md`).

## Что исправлено, чтобы гейт `s4_build` проходил

### 1. `factory/verify.py`: последняя запись на путь, а не все исторические

`task submit` пишет sha256, а `_check_recorded_artifacts` сравнивал **все**
записи журнала по пути. Эталонный план `nano` делит `product/src/todo.py`
между `TASK-001` и зависимой `TASK-002`, поэтому гейт отвергал стадию с
«artifact tampered after submission/approval» на файле, который вторая задача
правила законно. `fake` проходил, потому что писал те же байты.

Теперь при проверке вычисляется последняя запись на путь среди проверяемых
записей (журнальный порядок), старые записи пропускаются. Подмена не
ослаблена: проверяется именно последняя запись, поэтому файл, изменённый
после последней сдачи, по-прежнему даёт ошибку.

Тесты — `tests/test_verify_task_lineage.py`:

- две задачи с `depends_on`, вторая перезаписывает общий файл → `verify_run(run, "s4_build") == []`;
- файл изменён после последней сдачи → `tampered ... product/src/todo.py`;
- версия отклонённой задачи на диске не маскирует принятую → `tampered`.

### 2. `factory/runtime/loop.py`: стадия без незакрытых артефактов не зовёт модель

После приёмки обеих задач стадия `s4_build` оставалась `pending`, и рантайм
запускал ещё один produce-шаг, у которого `pending_artifacts` пуст. Промпт
печатал «Все объявленные артефакты стадии на месте», список «Разрешено
писать» был пуст — и агент переписывал уже принятые файлы (`test_store.py`,
`tasks/TASK-002/deliverable.md`), после чего гейт честно ловил расхождение
хешей. Теперь такой шаг не запускается: рантайм фиксирует сдачу стадии и
идёт к проверке. Это и дешевле (шаг стоил ~50k), и убирает соблазн.

Тест — `tests/test_runtime_loop.py::test_stage_with_nothing_pending_does_not_call_the_executor`:
исполнитель не вызывается ни разу на `s4_build`, стадия становится `accepted`.

### 3. `factory/artifacts.py`: `validate_plan` проверяет ссылки `traceability`

Матрица пишется на `s1_spec`, когда плана ещё нет, поэтому её `tasks` —
предположение. Раньше ссылка на несуществующий `TASK-003` всплывала только
в финальном `verify_run(run)`, после оплаченной сборки: прогон 183641 прошёл
все гейты и упал на приёмке кейса. Теперь `validate_plan` (гейт `s3_plan`)
проверяет, что каждая ссылка указывает на задачу из плана.

### 4. Правила промпта, без которых кейс красный

- `s1_spec` в `nano`: ровно три `REQ` (`add`, `list`, `done`); хранение,
  зависимости и обработка ошибок — ограничения в тексте, а не отдельные
  `REQ`. Матрица ссылается только на `TASK-001`/`TASK-002`.
- `s3_plan`: `traceability.json` принят на `s1_spec` и заморожен — план
  следует матрице, а не наоборот. Попытка «согласовать» матрицу на `s3`
  переписывает артефакт, принятый на другом гейте, и это законно падает как
  tampering (прогон 185720).
- `s3_plan`: план состоит ровно из двух задач; распределение `REQ` берётся
  из матрицы.

### 5. `eval/cases/nano-todo/expected.json`: тесты продукта через pytest

Кейс запускал `python -m unittest discover`, а реальный агент пишет
pytest-функции: `unittest` находил 0 тестов и кейс падал на «NO TESTS RAN».
Команда заменена на `{python} -m pytest product/tests`; unittest-классы
pytest собирает тоже, поэтому fake-эталон не изменился (3 теста).
`-q` не добавляйте: в репозитории `addopts = "-q"`, двойной `-q` печатает
только точки, и eval не видит числа тестов.

## Известные ограничения

1. **Бюджет `nano` — 3.1M / 120 мин по худшему из двух кейсов.**
   `nano-todo` дал 1.78–1.96M total (327–367k billable), `nano-product-mini`
   — 2 436 448 (390 496 billable), это 97,5 % прежнего потолка 2.5M, поэтому
   потолок пересчитан: 2 436 448 + 25 % → 3.1M. Прежние 400k останавливали
   реальный прогон сразу после плана, до написания кода. Флаги
   `--budget-tokens/--budget-minutes` нужны, только если модель дороже
   измеренной. USD-потолок $6 номинальный: цена не измеряется.
2. **USD не измеряется.** `estimated_cost_usd = 0.0` при `cost_status:
   unknown`; в baseline стоит 0.0, а не `null`, но ценой это не является.
3. **`brownfield` прогнан на реальном Hermes 30.10.2026**, но с оговоркой:
   первый запуск остановился по потолку 3M на `s7_release` (3 115 133 total),
   продолжение того же журнала с 4.5M дало полные 17 шагов и `verify` OK.
   Живого ревью ролей `05`/`06`/`08` не было: гейты закрыты `human:eval`, как
   и в остальных кейсах. Отдельно: `s2_architecture` и `TASK-002` съели 1.11M
   из 3.56M — это следующий кандидат на удешевление промпта.
4. **Артефакт, принятый на гейте, можно обновить только явным `re-record`.**
   Гейт по-прежнему замораживает хеш, но с 1.0.1 есть легальный путь:
   `factory artifact re-record --run-id ... --path ... --role ... --reason ...
   --evidence ...` пишет `ARTIFACT_RERECORD` с `old_sha256` и новым `sha256`,
   причиной и ссылкой на доказательство. Гейт это не переоткрывает
   (`stage_status`/`gate_status` не двигаются), `PASS` в этом событии
   запрещён, а любая правка после re-record снова `tampered`. Для
   `traceability` правило «план следует матрице» остаётся предпочтительным
   путём: re-record — для случая, когда правка уже случилась и её надо
   назвать, а не для «допишу потом».
5. **Проверка целостности привязана к последней записи.** Более поздняя
   сдача отклонённой задачи перекрывает хеш принятой: файл, оставшийся от
   отклонённой работы, не считается подменой. Это следует из «каждый байт
   на диске должен быть записан в журнале»; статус задачи при этом виден.
   `ARTIFACT_RERECORD` участвует в том же правиле: последняя запись на путь
   побеждает, но добавить её может только владелец и только с причиной.
6. **Песочница.** `bwrap` с read-only control plane, но тот же uid, сеть и
   запись в `~/.hermes/{installs,cache,logs}`. Отдельный пользователь/сервис —
   следующий шаг.
7. **Human-гейты в eval закрыты как `human:eval`.** Это замер, а не ревью;
   перед релизом гейты закрывает живой человек.
8. **Одна модель, один провайдер.** Числа не переносятся на другую модель;
   `--reasoning medium` этот провайдер отвергает HTTP 400.
9. **Закрыто в 1.0.1: ссылка на тест проверяет имя функции.** `build_matrix`
   требует, чтобы последний сегмент после `::` был объявлен в файле:
   `def`/`async def`/`class` для Python, `fn` с `#[test]`/`#[tokio::test]`
   для Rust. Ссылка без `::` по-прежнему проверяется только на существование
   файла. Проба на исторических прогонах показала, что расхождений больше,
   чем считалось: не только `nano-product-mini` (BUG-001, REQ-003), но и
   `nano-todo-20261030-191409` (REQ-001: два имени из двух не существуют) и
   `brownfield-proxy-mini-brownfield-real2` (REQ-003). Прогоны не правились:
   это история, а не регресс. `nano-product-mini` теперь честно показывает
   2/3 с сообщением, какой символ не найден.

## Что дальше

- **CI зелёный на `980a812`:** run `41215104365`, 14/14 jobs — ubuntu/macOS/
  Windows × Python 3.10–3.13, eval и build. Матрица трёх ОС закрыта.
  Предыдущий прогон (`41214643319` на `1bbb8c8`) упал на одном джобе:
  `test_budget_exhaustion_stops_the_loop` на windows-latest 3.11 — 10-мс шаг
  округлялся до `0.000` с, бюджет не срабатывал, и цикл останавливался на
  человеческом гейте. Шаг в тесте поднят до 50 мс (`980a812`).
- **Fake-эталон перегенерирован 31.10:** `eval/baseline.json` содержит
  13/33 для `nano-todo` и 15/35 для `standard-mini` (значения из `aeeb148`;
  в старом тексте handoff было 14/33 и 16/35 — оно устарело до этой сессии),
  `prompt_chars` обновлены под новые промпты.
- **Все три режима на реальном продукте закрыты:** `nano` (`nano-product-mini`),
  `standard` (`standard-mini`, `product-mini`), `brownfield`
  (`brownfield-proxy-mini`). Это условие DESIGN.md §11 для 1.0.0.
- Следить за потолками `nano` 3.1M, `standard` 2.9M и `brownfield` 4.5M: если
  законный прогон подойдёт к ним ближе чем на 10 %, поднимать по новому
  замеру, а не на глаз.
- Добавить `re-record` для легитимного обновления принятого артефакта, если
  практика покажет, что правило «план следует матрице» слишком тесное.
  **Сделано в 1.0.1:** `factory artifact re-record` + событие
  `ARTIFACT_RERECORD` (docs/runtime.md §5.7).
- Сравнить зелёный brownfield с baseline через `factory compare` и
  зафиксировать дельту в CHANGELOG следующей версии.

## Решение по релизу

Потолки `nano` — **3.1M / 120 минут** по худшему из двух кейсов: 2 436 448
total на `nano-product-mini` (97,5 % прежних 2.5M) + 25 %; `standard` —
**2.9M / 120 минут** по худшему из двух замеров (2 284 755 на `product-mini`,
1 978 597 на `standard-mini`). Потолок `brownfield` пересчитан по первому
полному прогону: **3 560 791 total + 25 % → 4.5M**, 17,2 мин + 25 % →
**30 минут**. Прежние расчётные 1.8M / 480 минут не покрывали даже
`s2_architecture` (948k после шага 9).
