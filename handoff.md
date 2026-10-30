# Handoff — 0.3.0

Состояние на 30 октября 2026. Ветка `main`, версия `0.3.0`, 224 теста.
Eval-набор зелёный на реальном Hermes: `eval/baseline-hermes.json` хранит
две записи — `nano-todo` (`runs/nano-todo-20261030-191409`) и
`standard-mini` (`runs/standard-mini-standard-real`). Fake-эталон CI
(`eval/baseline.json`) не менялся и проходит.

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
                дефолт режима `nano` после 0.3.0 — 2.5M / 120 мин)
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
(дефолт `standard` после пересчёта — 2.5M / 120 минут).

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

1. **Бюджет `nano` поднят до 2.5M / 120 мин по замеру.** Три реальных прогона
   дали 1.78–1.96M total (327–367k billable), поэтому 2.5M оставляет ~25 %
   запаса; прежние 400k останавливали реальный прогон сразу после плана, до
   написания кода. Флаги `--budget-tokens/--budget-minutes` нужны, только если
   модель дороже измеренной. USD-потолок $6 номинальный: цена не измеряется.
2. **USD не измеряется.** `estimated_cost_usd = 0.0` при `cost_status:
   unknown`; в baseline стоит 0.0, а не `null`, но ценой это не является.
3. **`brownfield` на реальном Hermes не гонялся.** Зелёные `nano-todo`
   (13 шагов) и `standard-mini` (15 шагов); гейты `standard` закрыты
   eval-харнессом (`human:eval`), живого ревью ролей `05`/`06`/`08` не было.
4. **Артефакт, принятый на гейте, заморожен.** Легального способа обновить
   его на более поздней стадии нет: единственный путь — объявить его
   артефактом этой стадии заранее. Для `traceability` это решено правилом
   «план следует матрице»; если понадобится обратное, нужен отдельный
   `re-record`-эвент с явной записью в журнал, а не тихая перезапись.
5. **Проверка целостности привязана к последней записи.** Более поздняя
   сдача отклонённой задачи перекрывает хеш принятой: файл, оставшийся от
   отклонённой работы, не считается подменой. Это следует из «каждый байт
   на диске должен быть записан в журнале»; статус задачи при этом виден.
6. **Песочница.** `bwrap` с read-only control plane, но тот же uid, сеть и
   запись в `~/.hermes/{installs,cache,logs}`. Отдельный пользователь/сервис —
   следующий шаг.
7. **Human-гейты в eval закрыты как `human:eval`.** Это замер, а не ревью;
   перед релизом гейты закрывает живой человек.
8. **Одна модель, один провайдер.** Числа не переносятся на другую модель;
   `--reasoning medium` этот провайдер отвергает HTTP 400.

## Что дальше

- Перегенерировать fake-эталон `eval/baseline.json`: в нём 14/33 и 16/35,
  текущий fake даёт 13/33 и 15/35. Шаги упали после фикса «стадия без
  pending не зовёт модель» (`f1895da`), но падение шагов не считается
  регрессией, поэтому тест молчит.
- Следить за потолком `nano` 2.5M: если законный прогон подойдёт к нему ближе
  чем на 10 %, поднимать по новому замеру, а не на глаз.
- Добавить `re-record` для легитимного обновления принятого артефакта, если
  практика покажет, что правило «план следует матрице» слишком тесное.
- Сравнить зелёный прогон с baseline через `factory compare` и зафиксировать
  дельту в CHANGELOG следующей версии.

## Решение по релизу

Потолки `nano` подняты до 2.5M / 120 минут по измерению (1.78–1.96M на трёх
прогонах), после чего `0.3.0` публикуется. Потолок `standard` пересчитан по
первому реальному прогону: 1 978 597 total + 26 % → 2.5M / 120 минут.
