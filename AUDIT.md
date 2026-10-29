# AUDIT.md — что изменилось после аудита

Этот документ фиксирует, какие блокеры аудита закрыты в `0.1.0`, а какие
остались ограничениями. Это не независимая сертификация.

## Закрытые блокеры

| Было | Стало | Проверка |
| :--- | :--- | :--- |
| Документация — заглушки | README (RU/EN), DESIGN, PIPELINE, ROSTER, AGENTS, 4 протокола, glossary, docs | `wc -l`, ревью |
| `pipeline.yaml` без стадий | 13 стадий, 4 режима, гейты, владельцы | `factory doctor` |
| `Nano`/`Standard` без автора spec | `01` пишет spec в `nano`; `standard` содержит `05` и `04` | `validate_pipeline` |
| `verify` — no-op без флага | проверка схем, state, journal, ролей, шаблонов, артефактов | `factory verify` |
| Пустой/усечённый журнал проходит | ошибка на пустом; state anchor ловит усечение | `test_empty_journal_is_invalid`, `test_tail_truncation_detected` |
| Конкурентная запись ломает цепочку | `fcntl.flock` | `test_concurrent_appends_keep_chain` |
| `PASS` можно подделать | проверка роли и evidence при записи и проверке | `test_verify_chain_rejects_forged_pass` |
| `--run-id` traversal | строгий regex + resolved path | `test_no_absolute_or_traversal_write` |
| Повторный `init` стирает state | `--force` архивирует в `.archive/` | `test_init_force_archives_previous_run` |
| Карантин не реализован | SkillSpector-адаптер, fail-closed, хеш-лок | `tests/test_quarantine.py` |
| Схемы принимают мусор | 9 полноценных JSON-схем | `test_garbage_state_is_rejected` |
| 21 из 22 ролей нарушают scope | `active_stages`, scopes по обязанностям, журнал только через CLI | `validate_role_card` |
| LICENSE без MIT-текста | полный MIT + отдельный NOTICE | `factory doctor` |
| `.gitignore` без `runs/` | `runs/`, `.env`, ключи, quarantine | ревью |
| Шаблоны ставят `PASS` | `PENDING`/`[ТРЕБУЕТ ПРОВЕРКИ]` | `validate_templates` |
| Битые ссылки на P2 | удалены, заменены контрактами | ревью |
| `advisory` «блокирует» | формулировки исправлены | ревью |
| Версии 1.0/v3.0/3.0.0 | `0.1.0` во всех источниках | `factory doctor` |
| Мёртвый код и тест-тавтология | код подключён или удалён; тесты на инварианты | `pytest` |
| Нет примера | `examples/nano-todo/`, 33 события | `factory verify` |

## Остаточные ограничения

- журнал не подписан; для сильной модели угроз нужна внешняя контрольная точка;
- встроенной модели нет; роли исполняет рантайм;
- стоимость `null`, пока рантайм не пишет токены и цену;
- Brownfield только для git;
- `s4_build` парсит только таблицу `plan.md`;
- `s9_learn` не применяет патчи автоматически.

## Как проверить

```bash
factory doctor
pytest -q
python examples/build_nano_todo.py
FACTORY_ROOT="$PWD/examples/nano-todo" FACTORY_RUNS_ROOT="$PWD/examples" \
  factory verify --run-id nano-todo --stage s9_learn --require-gate
```

Ожидаемо: `DOCTOR OK`, все тесты зелёные, `EXAMPLE OK`, `VERIFY OK`.
